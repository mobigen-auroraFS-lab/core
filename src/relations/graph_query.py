"""그래프 **read seam** — 자산 하나의 관계 이웃을 양방향·정규화해 조회.

왜 이 통로가 필요한가
    대칭 kind(``relation_kind.is_symmetric=True``)는 ``graph_persist._canonical_pair`` 가
    ``(min(node_id), max(node_id))`` 캐논 순서 **단일 행**으로 저장한다. 그래서 쓰기는
    중복 없이 깔끔하지만, "자산 X의 이웃"을 순진하게 ``WHERE src_node = X`` 로만 찾으면
    **X가 큰 쪽(dst)으로 접힌 대칭 엣지를 통째로 누락**한다. 또 ``is_symmetric``·``kind_code``
    는 엣지 행에 없고 ``relation_kind`` 에만 있다.
    → 그래서 **양방향으로 매칭**하고(``src`` 든 ``dst`` 든), 관계 종류를 조인해 붙이고, 질의
      자산 관점으로 방향을 정규화해야 한다. 이 읽기 경로를 여기 하나로 고정해 두는 이유는
      검색·상세·묶음 같은 소비자들이 각자 단방향 쿼리를 다시 만들어 쓰지 못하게 하기 위해서다.

읽기 전용
    스키마·쓰기 경로 변경 0(헌법 6조). VIEW 미사용 — ``src/relations/`` 파이썬 쿼리 함수 관례.

두 갈래가 한 파일에 있다(파일 아래쪽 ``084 멀티모달 메타`` 절)
    - **자산 ↔ 자산 관계**(대칭 kind 포함) — 위에서 말한 양방향 매칭·방향 정규화가 필요하다.
    - **자산 → 개체 소속**(``mm_member`` · 084) — **비대칭**이라 양방향 매칭을 하지 않는다. 같은
      파일에 두는 이유는 소비자(자산 상세·묶음 카드)가 그래프 읽기를 여기 하나로만 하게 하려는
      것이고(모듈이 존재하는 이유와 같다), SQL 상수는 서로 **갈라 둔다** — 공유하면 한쪽 수정이
      다른 쪽을 조용히 바꾼다.
"""
from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from src.config.filename_util import display_file_name
from src.domain.text_norm import normalize_text_key
from src.relations.approval_policy import choose_folded_edge, exposure_tier, tier_rank
from src.relations.schema import MM_MEMBER_KIND_CODE

# 엣지 양 끝을 node → asset 으로 두 번 조인한다. 앞의 조인은 asset_id 를 얻기 위한 것이고,
# 뒤의 조인은 화면에 보일 파일명·모달리티를 함께 가져오기 위한 것이다 — 이게 없으면 소비자가
# 이웃마다 자산을 다시 조회해야 한다.
# 정렬에 edge_id 를 2차 키로 둔 이유: 신뢰도가 같은 엣지들의 순서가 실행 계획에 따라 흔들리면
# 같은 질의가 매번 다른 순서를 낸다.
_FETCH_RELATIONS_SQL = """
SELECT ge.edge_id, rk.kind_code, rk.is_symmetric,
       ge.confidence, ge.reason, ge.topic, ge.status,
       sn.asset_id AS src_asset, dn.asset_id AS dst_asset,
       sa.modality AS src_modality, sa.fs_path AS src_fs_path,
       da.modality AS dst_modality, da.fs_path AS dst_fs_path
FROM graph_edge ge
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
JOIN node sn ON sn.node_id = ge.src_node AND sn.node_kind = 'asset'
JOIN node dn ON dn.node_id = ge.dst_node AND dn.node_kind = 'asset'
JOIN asset sa ON sa.asset_id = sn.asset_id
JOIN asset da ON da.asset_id = dn.asset_id
WHERE (sn.asset_id = %s OR dn.asset_id = %s)
  AND ge.status = ANY(%s)
ORDER BY ge.confidence DESC NULLS LAST, ge.edge_id
"""
# 도메인별 제외 조건은 없다 — 모든 도메인을 균일하게 노출한다.

# 노출 등급의 정렬 우선순위(강칸 먼저)는 ``approval_policy.tier_rank``(``TIER_ORDER``) 하나만 쓴다 —
# 종전 사본 ``_TIER_RANK`` 는 093 1단계에서 제거(백엔드 상세도 같은 함수를 쓴다). 신뢰도만으로 정렬하면
# **고신뢰 약칸이 저신뢰 강칸을 밀어내** 사용자가 "확실한 관계"라 믿은 것이 아래로 내려간다.


def fetch_relations_for_asset(
    conn: Connection[Any],
    *,
    asset_id: str,
    statuses: list[str] | None = None,
    include_weak: bool = False,
    min_conf_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """``asset_id`` 의 관계 이웃을 **노출 등급과 함께** 조회한다(2단 노출).

    등급은 ``tier`` 필드로 실린다 — ``"strong"``(연관 자료 · 승인됨) · ``"weak"``(참고 자료 ·
    미승인). 약칸이 필요한 이유: 자동승인 게이트가 ``same_domain`` 을 강등하면 관계 보유
    자산이 크게 줄어 화면이 빈다. 미승인 제안에도 진짜 관계가 상당수 섞여 있어, 이 칸이
    없으면 그만큼이 사용자에게서 사라진다.
    설계 배경: docs/관계_품질_측정_20260728.md

    Args:
        asset_id: 관점이 되는 자산. src/dst 어느 쪽에 있든 매칭한다.
        statuses: 조회할 엣지 상태 목록. 주면 ``include_weak`` 보다 **우선**한다
            (기존 함수의 ``status`` 인자를 그대로 흘려보내기 위한 통로).
        include_weak: ``True`` 면 ``proposed`` 도 함께 읽어 약칸으로 노출한다.
        min_conf_similarity: 약칸 노출 하한. **영속화 게이트와 같은 값을 써야 한다** —
            다르면 "행은 있는데 화면에 없는" 유령 구간이 생긴다(`approval_policy` 참조).

    Returns:
        **이웃 자산당 한 건**으로 접힌 리스트. 기존 키(``asset_id``·``kind_code``·
        ``is_symmetric``·``direction``·``confidence``·``status``·``topic``·``reason``·
        ``edge_id``·``file_name``·``modality``)에 ``tier``·``folded_kind_codes`` 가
        **추가**된다(하위호환). 정렬은 등급 → 신뢰도 내림차순 → edge_id 로 결정적이다.
    """
    wanted = statuses if statuses is not None else (
        ["active", "proposed"] if include_weak else ["active"])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_FETCH_RELATIONS_SQL, (asset_id, asset_id, wanted))
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        # 등급을 **먼저** 정한다 — None 이면 이 행은 노출 대상이 아니므로 정규화 비용도 아낀다.
        tier = exposure_tier(str(r["status"] or ""), str(r["kind_code"] or ""),
                             r["confidence"], min_conf_similarity=min_conf_similarity)
        if tier is None:
            continue
        is_symmetric = bool(r["is_symmetric"])
        src_asset = str(r["src_asset"])
        # 질의 자산 관점으로 뒤집는다: 이웃은 늘 **반대편**이고, 방향은 대칭 관계면 무방향,
        # 비대칭이면 질의 자산이 어느 쪽에 있느냐로 나가는·들어오는 방향이 갈린다.
        is_query_src = src_asset == str(asset_id)
        other_asset = str(r["dst_asset"]) if is_query_src else src_asset
        other_modality = r["dst_modality"] if is_query_src else r["src_modality"]
        other_fs_path = r["dst_fs_path"] if is_query_src else r["src_fs_path"]
        if is_symmetric:
            direction = "undirected"
        else:
            direction = "outbound" if is_query_src else "inbound"
        out.append(
            {
                "asset_id": other_asset,
                "kind_code": r["kind_code"],
                "is_symmetric": is_symmetric,
                "direction": direction,
                "confidence": r["confidence"],
                "status": r["status"],
                # topic 은 이 관계(쌍)의 **맥락 라벨**이며 자산 주제가 아니다 — 관계 검토
                #   UI 표시용으로만 존치. 자산 주제는 asset_topic 정본이 결정한다(엣지 topic 소비 중단).
                "topic": r["topic"],
                "reason": r["reason"],
                "edge_id": str(r["edge_id"]),
                # 이웃의 표시 정보를 함께 내려 준다 — 없으면 소비자가 이웃마다 자산을 다시 조회해야 한다.
                # 🔴 표시용 파일명은 정본 함수를 거친다(065 T605) — `basename` 만 쓰면
                #    아카이브 이동으로 붙은 `{asset_id}__` 프리픽스가 화면에 노출된다.
                #    2026-08-27 실측: 상세 화면에 `019f490f-…__Han_River.jpg` 가 그대로 보였다.
                "file_name": display_file_name(other_fs_path),
                "modality": other_modality,
                # 강칸/약칸 구분. 기존 키는 그대로 두고 **추가**만 한다(하위호환).
                "tier": tier,
            }
        )
    out = _fold_by_neighbor(out)
    # 등급 → 신뢰도 → edge_id 로 전부 다시 정렬한다. 접기가 남기는 행의 신뢰도는 그 이웃의
    # 최댓값이 아닐 수 있어(약한 주장이 낮은 점수로 남는 경우), SQL 이 정한 순서에 기대면
    # 같은 등급 안에서 신뢰도 역순이 생긴다 — 반환 계약(Returns)이 약속한 순서를 여기서 보장한다.
    out.sort(key=lambda e: (
        tier_rank(str(e["tier"])),
        -float(e["confidence"]) if e["confidence"] is not None else float("inf"),
        str(e["edge_id"]),
    ))
    return out


def _fold_by_neighbor(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """이웃 자산당 엣지 하나만 남긴다 — 동시보유 접기(규칙은 `approval_policy` 소관).

    여기서는 그룹을 만들어 넘기고 결과를 조립하기만 한다. 판정을 여기 두면 소비처마다
    복제되고 화면마다 결과가 갈린다 — 이 모듈이 존재하는 이유와 같다.

    **DB 의 쌍이 아니라 이웃 자산 단위로 묶는다.** 쌍 ``(src_node, dst_node)`` 로 접으면
    A→B 와 B→A 가 각각 남아 한 이웃이 두 번 나온다(비대칭 kind 에서 실제로 발생한다).

    Args:
        rows: 등급이 매겨진 이웃 엣지 목록. ``asset_id`` 로 묶는다.

    Returns:
        이웃당 한 건으로 접힌 목록(입력 순서 보존 · 첫 등장 순). 각 항목에
        ``folded_kind_codes``(접힌 종류·정렬됨)가 추가된다 — 접힌 행에만 넣으면 소비처가
        ``.get()`` 유무로 분기하게 되고 그 분기가 곧 "접힘을 모르는 코드"를 만든다.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r["asset_id"]), []).append(r)

    folded_out: list[dict[str, Any]] = []
    for edges in groups.values():
        keep_idx, folded_kinds = choose_folded_edge(edges)
        kept = dict(edges[keep_idx])
        kept["folded_kind_codes"] = folded_kinds
        folded_out.append(kept)
    return folded_out


def fetch_active_relations_for_asset(
    conn: Connection[Any], *, asset_id: str, status: str = "active"
) -> list[dict[str, Any]]:
    """``asset_id`` 의 관계 이웃을 **한 상태만** 조회한다(하위호환 래퍼).

    081 이전부터 있던 계약이다 — 포탈 상세·다운로드 번들 등 기존 호출부가 이 이름과 반환 키에
    의존하므로 그대로 남긴다. 새 코드는 등급까지 받는 ``fetch_relations_for_asset`` 을 쓴다.

    Args:
        asset_id: 관점이 되는 자산. src/dst 어느 쪽에 있든 매칭한다.
        status: 엣지 상태 필터(기본 ``active``). ``proposed``·``rejected`` 등도 조회 가능.

    Returns:
        이웃 dict 리스트(``fetch_relations_for_asset`` 과 같은 키 — ``tier``·
        ``folded_kind_codes`` 포함). ⚠️ **동시보유 접기도 그대로 적용된다** — 같은 이웃과
        관계가 여럿이면 한 행으로 접혀 종전보다 행 수가 줄 수 있다(접힌 종류는
        ``folded_kind_codes`` 로 관측 가능). ``status`` 를 명시했으므로 노출 하한은
        적용하지 않는다 — 호출자가 원한 상태를 조용히 걸러내면 "왜 안 나오나"를 추적할 수 없다.
    """
    return fetch_relations_for_asset(conn, asset_id=asset_id, statuses=[status])


# ── 084 멀티모달 메타(mm_meta) 조회 ─────────────────────────────────────────────
# 소속 엣지는 ``자산 → 개체``(``mm_member``) **비대칭**이다. 그래서 위의 관계 조회와 달리 양방향
# 매칭을 하지 않는다 — 개체 노드가 src 인 엣지는 존재하지 않으므로 찾을 필요가 없고, 찾으려 들면
# 나중에 붙을 개체↔개체 엣지(비범위)까지 섞여 든다.
#
# 노출 상태 기본값에 **``proposed`` 를 포함**하는 것이 이 기능의 생사다: 초기에는 전건 proposed
# 이므로 active 만 보면 화면이 영구히 빈다(081 번들이 active 0건으로 무동작이던 재발 방지 · spec §7).
# 승격 경로는 084 범위 밖이라 "확인된 N건"은 **승인 상태가 아니라 판정 사실**을 가리킨다.
_MM_META_DEFAULT_STATUSES = ("active", "proposed")

# 자산 → 속한 메타 목록. ``bundle_size`` 는 그 메타에 달린 소속 엣지 수(= "확인된 N건")이며
# **같은 상태 필터 + 같은 asset 노드 조건**을 쓴다 — 목록의 건수와 묶음 화면(``mm_meta_bundle``)의
# 건수가 어긋나면 사용자가 둘 중 무엇을 믿어야 할지 알 수 없다. 하위질의에 asset 노드 조인을
# 넣은 이유가 그 일치다(소속 엣지의 src 는 늘 자산이지만, 조건이 없으면 나중에 붙는 개체↔개체
# 엣지가 이 셈에 섞여 두 화면의 숫자가 갈린다).
# ⚠️ 바인딩 순서 주의: 상관 하위질의가 SELECT 절에 있어 그 ``%s`` 가 **가장 먼저** 온다
#    (statuses, asset_id, kind_code, statuses).
_MM_META_OF_ASSET_SQL = """
SELECT en.entity_type,
       en.entity_uid,
       COALESCE(NULLIF(en.canonical->>'name', ''), en.entity_uid) AS name,
       ge.edge_id, ge.status, ge.reason,
       (SELECT count(*) FROM graph_edge be
          JOIN node bn ON bn.node_id = be.src_node AND bn.node_kind = 'asset'
         WHERE be.dst_node = en.node_id
           AND be.relation_kind_id = ge.relation_kind_id
           AND be.status = ANY(%s)) AS bundle_size
FROM graph_edge ge
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
JOIN node an ON an.node_id = ge.src_node AND an.node_kind = 'asset'
JOIN node en ON en.node_id = ge.dst_node AND en.node_kind = 'entity'
WHERE an.asset_id = %s
  AND rk.kind_code = %s
  AND ge.status = ANY(%s)
ORDER BY en.entity_type, en.entity_uid
"""

# 메타 노드 1건. 엣지가 0건이어도 메타는 존재할 수 있으므로(수동 선등록한 빈 메타 · spec §6-1)
# 노드 조회와 구성 자산 조회를 **갈라 둔다** — 한 질의로 합치면 "없는 메타"와 "빈 메타"를 구분할 수 없다.
_MM_META_NODE_SQL = """
SELECT node_id,
       COALESCE(NULLIF(canonical->>'name', ''), entity_uid) AS name,
       canonical
FROM node
WHERE node_kind = 'entity' AND entity_type = %s AND entity_uid = %s
LIMIT 1
"""

# 메타의 구성 자산. 파일명·모달리티는 ``asset`` 에만 있으므로 node→asset 조인이 필요하다
# (위 관계 조회와 같은 이유). 정렬은 모달리티 → 자산 id 로 결정적이다.
_MM_META_MEMBERS_SQL = """
SELECT ge.edge_id, ge.status, ge.reason,
       a.asset_id, a.modality, a.fs_path
FROM graph_edge ge
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
JOIN node an ON an.node_id = ge.src_node AND an.node_kind = 'asset'
JOIN asset a ON a.asset_id = an.asset_id
WHERE ge.dst_node = %s
  AND rk.kind_code = %s
  AND ge.status = ANY(%s)
ORDER BY a.modality NULLS LAST, a.asset_id
"""


def _wanted_statuses(statuses: list[str] | None) -> list[str]:
    """노출 대상 상태 목록을 정한다(기본값에 ``proposed`` 포함).

    Args:
        statuses: 호출자가 지정한 상태 목록. ``None`` 이면 기본값(active+proposed)을 쓴다.
            빈 목록을 주면 **빈 결과**가 나온다 — 조용히 기본값으로 되돌리지 않는다(호출자가
            원한 필터를 함수가 덮으면 "왜 다 보이나"를 추적할 수 없다).

    Returns:
        바인딩용 리스트(psycopg 가 ``ANY(%s)`` 배열로 적응시킨다).
    """
    return list(_MM_META_DEFAULT_STATUSES) if statuses is None else list(statuses)


def mm_meta_of_asset(
    conn: Connection[Any], *, asset_id: str, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    """자산이 속한 **멀티모달 메타 목록**을 조회한다(읽기 전용 · spec §5).

    자산 상세의 "이 파일이 속한 메타" 진입점이 쓰는 통로다. 메타마다 묶음 크기를 함께 주므로
    소비자가 추가 질의 없이 "확인된 N건"을 표시할 수 있다.

    ⚠️ **1건짜리 메타(묶음 미성립)를 감추는 것은 소비자 몫**이다(spec §7 리뷰 지점 ②). 여기서
    걸러 내면 "왜 이 메타가 자산 상세에 없나"를 조회 계층에서 다시 파야 하고, 화면 정책이 SQL 에
    숨는다.

    Args:
        asset_id: 관점이 되는 자산. 소속 엣지의 **src** 쪽으로만 매칭한다(비대칭 kind).
        statuses: 조회할 엣지 상태 목록. ``None``(기본) 이면 ``active``+``proposed`` — 초기에는
            전건 proposed 라 이 기본값이 없으면 기능이 무동작이다(spec §7).

    Returns:
        ``[{entity_type, entity_uid, name, bundle_size, edge_id, status, reason}]`` —
        ``(entity_type, entity_uid)`` 오름차순(결정적). ``edge_id`` 는 **문자열**이다(조회행 id →
        str 관례). ``reason`` 은 스탬프 원문이며 해석은 ``src.mm_meta.persist.parse_member_reason``
        가 한다(조회 계층이 스탬프 형식을 알 필요는 없다). 소속이 없으면 빈 리스트.
    """
    wanted = _wanted_statuses(statuses)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MM_META_OF_ASSET_SQL,
                    (wanted, asset_id, MM_MEMBER_KIND_CODE, wanted))
        rows = cur.fetchall()
    return [
        {
            "entity_type": str(r["entity_type"]),
            "entity_uid": str(r["entity_uid"]),
            # SQL 이 이미 폴백하지만 파이썬에서도 한 번 더 받는다 — 빈 라벨은 화면에서 "이름 없는
            # 묶음"이 되어 클릭할 수 없게 된다.
            "name": str(r["name"] or r["entity_uid"]),
            "bundle_size": int(r["bundle_size"] or 0),
            "edge_id": str(r["edge_id"]),
            "status": str(r["status"]),
            "reason": r["reason"],
        }
        for r in rows
    ]


def mm_meta_bundle(
    conn: Connection[Any],
    *,
    entity_type: str,
    entity_uid: str,
    statuses: list[str] | None = None,
) -> dict[str, Any] | None:
    """메타 하나의 **구성 자산을 모달리티별로 묶어** 조회한다(읽기 전용 · spec §5).

    메타 카드가 쓰는 통로다. 이 기능의 존재 이유가 크로스모달 응집(텍스트·이미지·영상·오디오가
    한 묶음)이므로 반환도 모달리티별 그룹 + 건수다.

    빈 경우를 **두 가지로 구분**한다 — 메타 자체가 없으면 ``None``(호출부는 404), 메타는 있고 자산이
    0건이면 ``total=0``(수동 선등록한 빈 메타 · spec §6-1). 구분하지 않으면 "등록했는데 안 보인다"와
    "주소가 틀렸다"가 같은 응답이 된다.

    Args:
        entity_type: 개체 타입(닫힌 5종). 같은 표기·다른 타입은 별개 메타다(동음이의 분리).
        entity_uid: 표기 키. 컬럼에는 정규화 키만 저장되므로 입력도 같은 규칙으로 눌러 대조한다
            (``normalize_text_key`` 는 멱등이라 이미 키인 값은 그대로다 — URL 로 오는 값의 표기
            차이를 흡수한다).
        statuses: 조회할 엣지 상태 목록. ``None``(기본) 이면 ``active``+``proposed``.

    Returns:
        ``{entity_type, entity_uid, name, source, total, modalities}`` 또는 메타가 없으면 ``None``.
        ``source`` 는 ``user``(수동 선등록)·``auto``(배치 발굴) — ``canonical.source`` 부재는
        ``auto`` 다(spec §6-1). ``modalities`` 는
        ``[{modality, count, assets: [{asset_id, modality, file_name, edge_id, status, reason}]}]``
        이며 모달리티 → 자산 id 오름차순이다. id 는 전부 **문자열**이다(조회행 id → str 관례).
    """
    uid = normalize_text_key(entity_uid)
    wanted = _wanted_statuses(statuses)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MM_META_NODE_SQL, (entity_type, uid))
        node = cur.fetchone()
        if node is None:
            return None  # 없는 메타 — 빈 메타(아래 total=0)와 구분한다
        cur.execute(_MM_META_MEMBERS_SQL,
                    (node["node_id"], MM_MEMBER_KIND_CODE, wanted))
        rows = cur.fetchall()

    canonical = node["canonical"] if isinstance(node["canonical"], dict) else {}
    # 그룹은 SQL 정렬 순서(모달리티 오름차순)를 그대로 따른다 — dict 는 삽입 순서를 보존하므로
    # 파이썬에서 다시 정렬하지 않아도 결정적이다.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        modality = str(r["modality"] or "")
        groups.setdefault(modality, []).append({
            "asset_id": str(r["asset_id"]),
            "modality": modality,
            # 표시용 파일명 — 경로 전체를 내려보내지 않는다(기존 관계 조회와 같은 관례).
            # 표시용 파일명 — 정본 함수 경유(위 주석과 같은 이유).
            "file_name": display_file_name(r["fs_path"]),
            "edge_id": str(r["edge_id"]),
            "status": str(r["status"]),
            "reason": r["reason"],
        })
    return {
        "entity_type": str(entity_type),
        "entity_uid": uid,
        "name": str(node["name"] or uid),
        "source": str(canonical.get("source") or "auto"),
        # 생성 설명(있으면). 카드가 쓰는 값인데 종전엔 없어 소비자가 node 를 한 번 더 읽었다(095 · 질의 1회 절약).
        "description": (canonical.get("description") or None),
        "total": len(rows),
        "modalities": [{"modality": m, "count": len(items), "assets": items}
                       for m, items in groups.items()],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 095 개체(멀티모달 메타) 화면 seam — 데모 라우트의 직접 SQL 네 덩어리를 코어로 올린 것.
#
# 왜 여기인가(093 세 질문 ③): 전부 ``graph_edge`` 를 읽는 질의라 잘못 짜면 조용히 틀린다 — 상태 필터를
# 빠뜨리면 거절된 소속이 세어지고, DISTINCT 를 빠뜨리면 라벨이 여러 개인 자산이 두 번 세어진다. 그래서
# 백엔드는 이 함수들을 부르기만 하고 SQL 을 갖지 않는다. 응답 모양(키 이름·문구·상위 N 절단)은 백엔드 몫.
#
# "노출 개체" 의 정의는 네 함수가 같다: node_kind='entity' · 소속(mm_member) 엣지 · 상태 active+proposed(기본)
# · 구성 자산(DISTINCT src) 수 ≥ ``min_bundle_size``. 임계값 자체는 호출자가 준다(화면 정책 — 목록은 3,
# 갈래 필터 시 2 처럼 갈릴 수 있다 · spec 095 §6). 조건식은 데모와 글자까지 같게 두어 응답이 바뀌지 않는다.
# ══════════════════════════════════════════════════════════════════════════════

# 갈래(개체 라벨) AND 필터 — "고른 갈래 이름을 **모두** 가진 개체". 라벨 이름의 정본은 ``mm_skill.labels`` 다
# (코드→이름 표를 소비자가 들고 있지 않게). 이름이 스킬마다 겹칠 수 있어 COUNT(DISTINCT 이름) 으로 센다.
_ENTITY_AREA_FILTER_SQL = """
      (%(areas)s::text[] IS NULL OR EXISTS (
            SELECT 1
              FROM entity_mm_skill_label el
              JOIN mm_skill es ON es.skill_code = el.skill_code
              CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
             WHERE el.entity_type = {alias}.entity_type
               AND el.entity_uid = {alias}.entity_uid
               AND (elb->>'code') = el.label_code
               AND (elb->>'name') = ANY(%(areas)s)
             GROUP BY el.entity_type, el.entity_uid
            HAVING COUNT(DISTINCT (elb->>'name')) = %(area_n)s))
"""

# 노출 개체 집합(공통 CTE 본문). ``{type_cond}``·``{area_cond}`` 자리에 조건을 끼운다 — 같은 조각을 축마다
# **다른 조건으로** 쓰기 때문이다(종류 칩은 조건 없이 · 갈래 칩은 종류+갈래 적용 · spec 087 2차 정정).
_EXPOSED_ENTITIES_SQL = """
    SELECT n.entity_type, n.entity_uid, n.node_id
      FROM node n
      JOIN graph_edge ge    ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
     WHERE n.node_kind = 'entity' AND rk.kind_code = %(kind)s
       AND ge.status = ANY(%(statuses)s)
       {type_cond}
       {area_cond}
     GROUP BY n.entity_type, n.entity_uid, n.node_id
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
"""
_TYPE_COND_SQL = "AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)"

_LIST_ENTITIES_SQL = """
SELECT n.entity_type, n.entity_uid, n.node_id,
       COALESCE(n.canonical->>'name', n.entity_uid) AS name,
       COALESCE(n.canonical->>'source', 'auto')     AS source,
       n.canonical->>'description'                  AS description,
       COUNT(DISTINCT ge.src_node)                  AS confirmed_count,
       -- 이 개체에 묶인 **전량**(종류·갈래 필터와 무관). 화면이 "이 갈래 5건 / 전체 6건"을 함께 보인다
       -- (2026-08-27 개념 감사 — 자산 라벨로 좁히면 한 개체가 갈래마다 쪼개져 카드 5건·상세 6건이 됐다).
       (SELECT COUNT(DISTINCT ge2.src_node)
          FROM graph_edge ge2
          JOIN relation_kind rk2 ON rk2.relation_kind_id = ge2.relation_kind_id
         WHERE ge2.dst_node = n.node_id
           AND rk2.kind_code = %(kind)s
           AND ge2.status = ANY(%(statuses)s))       AS total_count,
       ARRAY_AGG(DISTINCT a.modality)               AS modalities,
       -- 근거 키워드 = 소속 엣지 reason 의 kw= 값(어떤 낱말로 묶였나) — 새 LLM 호출 없이 저장된 사실만 모은다.
       ARRAY_AGG(DISTINCT substring(ge.reason from 'kw=([^|]*)'))
           FILTER (WHERE ge.reason IS NOT NULL)     AS keywords,
       ARRAY_AGG(DISTINCT t.topic_ko)
           FILTER (WHERE t.topic_ko IS NOT NULL)    AS topics,
       -- 형식 축(085 자산 라벨) 이름. 자산 하나가 라벨 여러 개를 가질 수 있어 합이 자산 수보다 클 수 있다.
       ARRAY_AGG(DISTINCT fl.form_name)
           FILTER (WHERE fl.form_name IS NOT NULL)  AS forms,
       -- 갈래 = **대상에 붙은** 라벨(spec 087). 자산 라벨(forms)과 층이 다르다 — 좁혀도 대상이 쪼개지지 않는다.
       (SELECT ARRAY_AGG(DISTINCT (elb->>'name') ORDER BY (elb->>'name'))
          FROM entity_mm_skill_label el
          JOIN mm_skill es ON es.skill_code = el.skill_code
          CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
         WHERE el.entity_type = n.entity_type
           AND el.entity_uid = n.entity_uid
           AND (elb->>'code') = el.label_code
           AND el.label_code <> 'unassigned')          AS areas
  FROM node n
  JOIN graph_edge ge    ON ge.dst_node = n.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
  JOIN node sn          ON sn.node_id = ge.src_node
  JOIN asset a          ON a.asset_id = sn.asset_id
  LEFT JOIN asset_topic t ON t.asset_id = sn.asset_id
  LEFT JOIN (
        SELECT l.asset_id, (lb->>'name') AS form_name
          FROM asset_mm_skill_label l
          JOIN mm_skill s ON s.skill_code = l.skill_code
          CROSS JOIN LATERAL jsonb_array_elements(s.labels) AS lb
         WHERE l.skill_code = ANY(%(form_skills)s)
           AND (lb->>'code') = l.label_code
       ) fl ON fl.asset_id = sn.asset_id
 WHERE n.node_kind = 'entity'
   AND rk.kind_code = %(kind)s
   AND ge.status = ANY(%(statuses)s)
   AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)
   AND {area_filter}
 -- node_id 를 함께 묶는다: 전체 건수 서브쿼리가 이 컬럼을 참조한다. 그룹이 쪼개질 위험은 없다 —
 -- uq_node_entity(entity_type, entity_uid) 가 개체마다 node_id 1개를 보장한다.
 GROUP BY n.node_id, n.entity_type, n.entity_uid, n.canonical
HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
 ORDER BY confirmed_count DESC, n.entity_uid ASC
 LIMIT %(limit)s
"""

_COUNT_BY_TYPE_SQL = """
SELECT t.entity_type, COUNT(*) AS n
  FROM ({exposed}) t
 GROUP BY t.entity_type
 ORDER BY t.entity_type
"""

# 갈래(개체 라벨)별 노출 개체 수. **0건 라벨도 돌려준다**(커버리지 갭 신호 — 감추는 것은 화면 몫).
# ⚠️ LEFT JOIN + COUNT(DISTINCT (a,b)) 는 쓰지 않는다 — 매칭이 없을 때 (NULL,NULL) 복합값이 1로 세어져
#    0건 라벨이 전부 1건으로 나왔다(2026-08-27 실측 함정). 그래서 hit 를 따로 세고 LEFT JOIN 한다.
_COUNT_BY_AREA_SQL = """
WITH ex AS ({exposed}),
lab AS (
    SELECT s.skill_code, s.name AS skill,
           (lb->>'code') AS code, (lb->>'name') AS name, ord
      FROM mm_skill s,
           LATERAL jsonb_array_elements(s.labels) WITH ORDINALITY AS t(lb, ord)
     WHERE s.status = 'active' AND s.skill_code <> ALL(%(reserved)s)
),
hit AS (
    SELECT el.skill_code, el.label_code, COUNT(*) AS n
      FROM (
        SELECT DISTINCT el2.skill_code, el2.label_code, el2.entity_type, el2.entity_uid
          FROM entity_mm_skill_label el2
          JOIN ex ON ex.entity_type = el2.entity_type AND ex.entity_uid = el2.entity_uid
      ) el
     GROUP BY 1, 2
)
SELECT lab.name, lab.skill, lab.skill_code, COALESCE(hit.n, 0) AS n
  FROM lab
  LEFT JOIN hit ON hit.skill_code = lab.skill_code AND hit.label_code = lab.code
 WHERE lab.code <> 'unassigned'
 ORDER BY n DESC, lab.skill_code, lab.ord
"""

_ASSETS_OF_ENTITIES_SQL = """
WITH picked AS ({exposed})
SELECT DISTINCT a.asset_id::text AS asset_id, a.modality, a.fs_path,
       COALESCE(a.file_size, 0) AS file_size
  FROM picked
  JOIN graph_edge ge    ON ge.dst_node = picked.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
  JOIN node sn          ON sn.node_id = ge.src_node
  JOIN asset a          ON a.asset_id = sn.asset_id
 WHERE rk.kind_code = %(kind)s AND ge.status = ANY(%(statuses)s)
   AND a.status = 'registered'
   AND (NOT %(exclude_video)s OR a.modality <> 'video')
 -- DISTINCT 와 함께 쓰므로 정렬 키는 선택 목록의 컬럼이어야 한다(첫 컬럼 = asset_id 문자열).
 -- 데모 SQL 은 여기서 `a.asset_id` 로 정렬해 PG17 이 거부했다(정식화 중 발견한 데모 결함).
 ORDER BY 1
"""


def _area_params(area_names: list[str] | None) -> dict[str, Any]:
    """갈래 필터 바인딩 — 이름 목록과 그 고유 개수(AND 판정용).

    Args:
        area_names: 고른 갈래 이름들. ``None``·빈 목록이면 필터 없음(``areas`` 를 ``None`` 으로 묶는다).

    Returns:
        ``{"areas": 목록 또는 None, "area_n": 고유 개수}``.
    """
    names = [str(n) for n in (area_names or []) if str(n).strip()]
    return {"areas": names or None, "area_n": len(set(names))}


def _sorted_strs(values: Any) -> list[str]:
    """배열 컬럼을 **빈 값 제거 · 문자열 · 가나다 순**의 리스트로 만든다(결정적 순서).

    Args:
        values: SQL ``ARRAY_AGG`` 결과(``None`` 가능).

    Returns:
        정렬된 문자열 리스트.
    """
    return sorted(str(v) for v in (values or []) if v)


def list_entities(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    min_bundle_size: int,
    limit: int,
    statuses: list[str] | None = None,
    form_skill_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """노출 개체(멀티모달 메타) **목록**을 구성 자산 수 내림차순으로 조회한다(읽기 전용 · 095 FR-1).

    개체 화면의 카드 그리드가 쓰는 재료다. 카드 한 장에 필요한 사실을 한 질의로 모은다 — 이름·출처·생성
    설명·구성 자산 수(필터 안)·전체 자산 수(필터 무관)·모달리티·근거 키워드·주제·형식 라벨·갈래.
    새 LLM 호출은 없다(저장된 사실의 집계).

    Args:
        entity_type: 종류(타입) 필터. ``None`` 이면 전체.
        area_names: 갈래(개체 라벨) 이름들 — **모두 가진** 개체만(AND). ``None``·빈 목록이면 필터 없음.
        min_bundle_size: 노출 임계 — 구성 자산 수가 이 값 이상인 개체만. 값은 호출자(화면 정책)가 정한다.
        limit: 최대 개체 수(구성 자산 수 상위). 검색·좁히기 **전** 집계 상한이다.
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed(초기엔 전건 proposed 라 빼면 화면이 빈다).
        form_skill_codes: 형식 축(``forms``)으로 읽을 자산 라벨 스킬 코드들. ``None``·빈 목록이면 ``forms``
            는 빈 리스트다. 어느 스킬을 형식 축으로 보이나는 화면 정책이라 호출자가 준다(데모는 ``content_form`` 하나).

    Returns:
        ``[{entity_type, entity_uid, node_id, name, source, description, confirmed_count, total_count,
        modalities, keywords, topics, forms, areas}]`` — 구성 자산 수 내림차순 → 표기 키 오름차순.
        배열 필드는 빈 값을 뺀 **가나다 순**이다(같은 입력이면 같은 순서 · 헌법 3조). ``keywords`` 는 원문
        **전부**다 — 상위 몇 개를 어떤 순서로 보일지는 호출자 몫. id 는 전부 문자열.
    """
    params = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "etype": entity_type,
        "minsize": int(min_bundle_size),
        "limit": int(limit),
        "form_skills": [str(c) for c in (form_skill_codes or [])],
        **_area_params(area_names),
    }
    sql = _LIST_ENTITIES_SQL.format(area_filter=_ENTITY_AREA_FILTER_SQL.format(alias="n"))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "entity_type": str(r["entity_type"]),
            "entity_uid": str(r["entity_uid"]),
            "node_id": str(r["node_id"]),
            "name": str(r["name"]),
            "source": str(r["source"]),
            "description": (r["description"] or None),
            "confirmed_count": int(r["confirmed_count"]),
            "total_count": int(r["total_count"]),
            "modalities": _sorted_strs(r["modalities"]),
            "keywords": _sorted_strs(r["keywords"]),
            "topics": _sorted_strs(r["topics"]),
            "forms": _sorted_strs(r["forms"]),
            "areas": [str(x) for x in (r["areas"] or []) if x],  # SQL 이 이미 이름순
        }
        for r in rows
    ]


def count_entities_by_type(
    conn: Connection[Any], *, min_bundle_size: int, statuses: list[str] | None = None
) -> dict[str, int]:
    """종류(타입)별 **노출 개체 수**(읽기 전용 · 095 FR-1).

    종류 칩·타입 어휘 화면이 쓴다. **아무 필터도 걸지 않는다** — 종류는 좁히는 축이 아니라 갈아타는 축이라
    "이 종류로 갈아타면 몇 개"가 필요하다(spec 087 2차 정정: 갈래 조건을 적용하면 갈아탈 칩이 0건으로 사라졌다).

    Args:
        min_bundle_size: 노출 임계(구성 자산 수 하한).
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed.

    Returns:
        ``{entity_type: 개체 수}`` — 타입 이름 오름차순 삽입. 어휘에 있으나 개체가 없는 타입은 키가 없다
        (0 으로 채우는 것은 어휘를 아는 호출자 몫).
    """
    params = {"kind": MM_MEMBER_KIND_CODE, "statuses": _wanted_statuses(statuses),
              "minsize": int(min_bundle_size)}
    exposed = _EXPOSED_ENTITIES_SQL.format(type_cond="", area_cond="")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_COUNT_BY_TYPE_SQL.format(exposed=exposed), params)
        rows = cur.fetchall()
    return {str(r["entity_type"]): int(r["n"]) for r in rows}


def count_entities_by_area(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    min_bundle_size: int,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """갈래(개체 라벨)별 **노출 개체 수** — 고른 종류와 이미 고른 갈래를 적용한 뒤 센다(095 FR-1).

    갈래 칩이 쓴다. 갈래는 다중 선택·AND 라 "이 칩을 더 누르면 몇 개가 되나"가 맞는 숫자다(더할수록
    좁아진다). 그래서 종류 조건과 이미 고른 갈래를 **먼저 적용**한 노출 개체 안에서 라벨마다 센다.
    활성 스킬의 라벨 전부를 돌려주며 **0건도 포함**한다 — 0 은 "이 갈래엔 아직 자료가 없다"는 커버리지 갭
    신호라 API 가 지우지 않는다(감추는 것은 화면 몫). 예약 스킬(타입 어휘 저장용)과 ``unassigned`` 는 뺀다.

    Args:
        entity_type: 고른 종류. ``None`` 이면 종류 조건 없음.
        area_names: 이미 고른 갈래들(AND). ``None``·빈 목록이면 갈래 조건 없음.
        min_bundle_size: 노출 임계.
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed.

    Returns:
        ``[{name, skill, skill_code, count}]`` — 개체 수 내림차순 → 스킬 코드 → 스킬 정의 순서(라벨 순).
    """
    from src.mm_classify.model import NON_CLASSIFY_SKILL_CODES  # 순환 import 회피(model 은 순수)

    params = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "etype": entity_type,
        "minsize": int(min_bundle_size),
        "reserved": sorted(NON_CLASSIFY_SKILL_CODES),
        **_area_params(area_names),
    }
    exposed = _EXPOSED_ENTITIES_SQL.format(
        type_cond=_TYPE_COND_SQL,
        area_cond="AND " + _ENTITY_AREA_FILTER_SQL.format(alias="n"),
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_COUNT_BY_AREA_SQL.format(exposed=exposed), params)
        rows = cur.fetchall()
    return [
        {"name": str(r["name"]), "skill": str(r["skill"]), "skill_code": str(r["skill_code"]),
         "count": int(r["n"])}
        for r in rows
    ]


def assets_of_entities(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    min_bundle_size: int,
    statuses: list[str] | None = None,
    exclude_video: bool = False,
) -> list[dict[str, Any]]:
    """좁힌 **노출 개체들의 구성 자산 전부**(중복 제거 · 등록 자산만)를 조회한다(095 FR-1).

    "지금 보고 있는 대상들을 한 zip 으로 받기"가 쓴다 — 화면의 좁히기 축(종류·갈래)과 다운로드 축이 같아야
    "지금 보는 것을 받는다"가 성립한다. 용량 상한·잘림·헤더·파일명은 다운로드 정책이라 호출자 몫이다.

    Args:
        entity_type: 종류 필터. ``None`` 이면 전체.
        area_names: 갈래 이름들(AND). ``None``·빈 목록이면 필터 없음.
        min_bundle_size: 노출 임계.
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed.
        exclude_video: 참이면 영상 자산을 뺀다(용량이 크게 준다).

    Returns:
        ``[{asset_id, modality, fs_path, file_size}]`` — 자산 id 오름차순. ``file_size`` 는 없으면 0.
        경로가 비어 있는 행도 그대로 준다(빼는 판단은 호출자 — manifest 에 남길 수 있게).
    """
    params = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "etype": entity_type,
        "minsize": int(min_bundle_size),
        "exclude_video": bool(exclude_video),
        **_area_params(area_names),
    }
    exposed = _EXPOSED_ENTITIES_SQL.format(
        type_cond=_TYPE_COND_SQL,
        area_cond="AND " + _ENTITY_AREA_FILTER_SQL.format(alias="n"),
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ASSETS_OF_ENTITIES_SQL.format(exposed=exposed), params)
        rows = cur.fetchall()
    return [
        {"asset_id": str(r["asset_id"]), "modality": str(r["modality"] or ""),
         "fs_path": r["fs_path"], "file_size": int(r["file_size"] or 0)}
        for r in rows
    ]
