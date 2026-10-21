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
# 질의 자산의 노드 id 를 먼저 구해 src_node·dst_node 와 직접 비교한다 — 두 별칭에 걸친 OR
# (sn.asset_id = X OR dn.asset_id = X)은 src/dst 인덱스를 못 타 graph_edge 를 전부 훑었다
# (2026-10-01 실측 0.13~0.2초 → 0.002초). 스칼라 서브쿼리는 uq_node_asset(asset_id 고유 ·
# node_kind='asset')이 1행 이하를 보장하고, 양방향 매칭(ADR 2026-05-28)은 그대로다.
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
WHERE (ge.src_node = (SELECT node_id FROM node WHERE asset_id = %s AND node_kind = 'asset')
    OR ge.dst_node = (SELECT node_id FROM node WHERE asset_id = %s AND node_kind = 'asset'))
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
# 갈래 필터 시 2 처럼 갈릴 수 있다 · spec 095 §6). 조건식의 모양은 속도 때문에 데모와 달라졌어도 결과는
# 데모와 같다 — 실 DB e2e 테스트가 데모 SQL 과 결과를 대조해 지킨다.
# ══════════════════════════════════════════════════════════════════════════════

# 갈래(개체 라벨) AND 필터 — "고른 갈래 이름을 **모두** 가진 개체". 라벨 이름의 정본은 ``mm_skill.labels`` 다
# (코드→이름 표를 소비자가 들고 있지 않게). 이름이 스킬마다 겹칠 수 있어 (종류, 표기, 이름) 짝을 먼저 중복
# 제거한 뒤 개수로 센다 — 짝이 유일하면 행 수가 곧 서로 다른 이름 수라 COUNT(DISTINCT 이름) 과 같은 뜻이다.
# ⚠️ 바깥 개체(``{alias}``)를 참조하지 않는 명단으로 둔다 — 참조하면 개체 노드마다 부분질의가 한 번씩 돌아
#    갈래를 고른 요청(목록·개수·칩·묶음)이 모두 초 단위로 느려진다. 명단의 DISTINCT 는 고른 갈래의 라벨 행만
#    다루지만, 플래너가 정렬로 집계하면 아래 hit 와 같은 한글 정렬 비용이 붙는다(큰 갈래에서 수백 ms).
# ⚠️ 이 조각은 WHERE 의 AND 로만 붙인다(NOT 안·SELECT 목록 금지) — 바깥 표기(``entity_uid``)는 NULL 을 허용하고
#    그때 IN 이 NULL 이 되는데, AND 에서는 거짓과 같아 상관 EXISTS 와 결과가 같다. 다른 자리에서는 이 동치가 깨진다.
# 설계 배경: `specs/105-entity-area-count-speed`
_ENTITY_AREA_FILTER_SQL = """
      (%(areas)s::text[] IS NULL OR ({alias}.entity_type, {alias}.entity_uid) IN (
            SELECT t.entity_type, t.entity_uid
              FROM (SELECT DISTINCT el.entity_type, el.entity_uid, (elb->>'name') AS name
                      FROM entity_mm_skill_label el
                      JOIN mm_skill es ON es.skill_code = el.skill_code
                      CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
                     WHERE (elb->>'code') = el.label_code
                       AND (elb->>'name') = ANY(%(areas)s)) t
             GROUP BY t.entity_type, t.entity_uid
            HAVING COUNT(*) = %(area_n)s))
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
       {allow_cond}
     GROUP BY n.entity_type, n.entity_uid, n.node_id
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
"""
_TYPE_COND_SQL = "AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)"

# 개체 id **화이트리스트** — 검색(집합 판정)이 고른 개체만 남긴다(099 G4 · spec §3-2a).
# 🔴 ``NULL``(필터 없음)과 **빈 배열**(매칭 0건)을 SQL 이 직접 가른다. 판정을 파이썬 ``if not …`` 에
#    두면 빈 집합이 "필터 없음"으로 접혀 **검색했는데 전체가 나오는** 조용한 오류가 된다 — 오류가
#    나지 않으므로 아무도 모른다. 빈 배열이면 ``unnest`` 가 0행이라 ``EXISTS`` 가 거짓이 된다.
# 개체의 자연키가 (타입, 표기) 둘이라 **두 배열을 나란히** 풀어 짝으로 맞춘다. 한 배열로 표기만
# 맞추면 타입이 다른 동명 개체가 새어 든다(`김밥` 이 음식과 작품으로 갈린 실례 · entity_doc_id 참조).
_ENTITY_ALLOW_FILTER_SQL = """
      (%(allow_types)s::text[] IS NULL OR EXISTS (
            SELECT 1
              FROM unnest(%(allow_types)s::text[], %(allow_uids)s::text[]) AS al(t, u)
             WHERE al.t = {alias}.entity_type AND al.u = {alias}.entity_uid))
"""

# **우선 티어**(099 G7) — 호출부가 앞세우라고 준 개체에 1, 나머지에 0.
# 왜 필요한가(실측 2026-09-17): 이름으로 찾아도 그 개체가 위에 오지 않았다(`숭례문` 7위 · `경포대`
# 15위). 정렬이 구성 자산 수뿐이라 **큰 개체가 늘 위**로 오기 때문이다. 도서관에서 제목이 정확히
# 같은 책이 있는데도 "두꺼운 책부터" 꽂아 둔 셈이다.
# 🔴 **누가 우선인지는 코어가 정하지 않는다** — "이름이 정확히 같다"는 판정은 화면 정책이라
#    호출부가 집합으로 준다(093 책무 경계). 코어가 아는 것은 "이 짝들을 앞세워라"뿐이다.
# 🔴 ``NULL``(우선 대상 없음)을 SQL 이 직접 가른다 — 배열이 NULL 이면 전원 0 이라 **종전 순서와
#    완전히 같다**. 빈 배열도 결과가 같다(앞세울 개체가 없다). ``uid_allow`` 와 달리 여기서는
#    NULL 과 빈 집합의 뜻이 **같다**: 저쪽은 "무엇을 남길까"(거르기)이고 이쪽은 "무엇을 앞세울까"
#    (순서)이기 때문이다.
# 🔴 티어는 **3단**이다(2026-09-21). 종전 2단에서는 「남자 배우」처럼 이름이 하나도 안 맞는
#    개념 질의에서 전원이 티어 0 이 되어, 구성 자산 수가 순서를 지배했다 — 뜻으로 14등인 개체가
#    자산이 많으면 1위로 올라왔다(실측: 하정우 1등인데 채원빈이 화면 1위).
#    이름 일치(2)를 뜻 상위(1)보다 앞에 두는 이유는 **이름이 더 확실한 신호**라서다.
#    ⚠️ 관련도로 정렬하는 것이 아니라 관련도 상위를 **앞자리로 승급**시키는 것이라, 099 가 막은
#    "관련도순은 커서를 못 만든다" 제약에 걸리지 않는다. 정렬 키 개수도 그대로다.
_ENTITY_FIRST_TIER_SQL = """
       CASE WHEN %(first_types)s::text[] IS NOT NULL AND EXISTS (
                 SELECT 1
                   FROM unnest(%(first_types)s::text[], %(first_uids)s::text[]) AS fs(t, u)
                  WHERE fs.t = n.entity_type AND fs.u = n.entity_uid) THEN 2
            WHEN %(semantic_types)s::text[] IS NOT NULL AND EXISTS (
                 SELECT 1
                   FROM unnest(%(semantic_types)s::text[], %(semantic_uids)s::text[]) AS sm(t, u)
                  WHERE sm.t = n.entity_type AND sm.u = n.entity_uid) THEN 1
            ELSE 0 END                               AS prio_tier
"""

# 이어읽기(keyset) 조건 — **동점 그룹을 갈라 잇는다**.
# 정렬이 ``prio_tier DESC, confirmed_count DESC, entity_uid ASC`` 라 튜플 비교(``(a,b) < (c,d)``)로는
# 못 쓴다(방향이 섞였다).
# 🔴 동점 처리가 이 조건의 전부다: 구성 자산 수 3건짜리 개체가 수백 개인 것이 정상이라(실측 291개
#    무더기), 쪽 경계는 거의 늘 동점 무더기 한가운데에 떨어진다. ``<`` 만 쓰면 그 무더기의 나머지를
#    **통째로 잃고**, ``<=`` 를 쓰면 **통째로 다시 읽는다**(중복). 둘 다 오류를 내지 않아 아무도 모른다.
# 🔴 정렬 키가 넷이 되면 이 조건도 **4단**이어야 한다. 한 단이 빠지면 그 경계의 무리가 통째로
#    날아가거나 두 번 읽힌다 — 오류 없이 조용히.
# 🔴 마지막 단이 ``entity_type`` 인 이유(2026-09-18): 개체의 자연키는 **(종류, 표기) 둘**이다.
#    같은 표기가 종류로 갈린 개체가 실제로 있고(``백두산`` 장소/작품 등 4쌍), 그 둘이 같은 티어·
#    같은 구성 자산 수가 되면 표기까지 같아 **순서가 정해지지 않는다.** 책갈피는 "마지막 값 다음
#    부터"라 같은 값이 둘이면 하나를 건너뛰거나 두 번 낸다. 종류를 **표기 뒤에** 둔 것은 이미
#    정해져 있던 순서를 바꾸지 않기 위해서다(앞에 두면 동점 무리가 종류별로 뭉쳐 보인다).
# NULL(커서 없음)이면 조건 전체가 참이 되어 첫 쪽이 된다 — 커서 유무로 SQL 을 갈라 두면 한쪽만
# 고쳐져 첫 쪽과 다음 쪽의 정의가 어긋난다. 세 값은 파이썬이 **함께 오거나 함께 없게** 막는다.
_ENTITY_KEYSET_SQL = """
      (%(after_count)s::bigint IS NULL
       OR ent.prio_tier < %(after_tier)s::int
       OR (ent.prio_tier = %(after_tier)s::int
           AND (ent.confirmed_count < %(after_count)s::bigint
                OR (ent.confirmed_count = %(after_count)s::bigint
                    AND (ent.entity_uid > %(after_uid)s::text
                         OR (ent.entity_uid = %(after_uid)s::text
                             AND ent.entity_type > %(after_type)s::text))))))
"""

# 개체 목록 — **한 쪽을 먼저 고르고, 카드 재료는 그 쪽에만 만든다**.
#   ① ``ent``  : 정렬·이어읽기·자르기에 필요한 값만(종류·표기·구성 자산 수·우선 티어). 노출 조건
#                (종류·갈래·허용목록·상태·임계)은 여기서 건다.
#   ② ``page`` : ``ent`` 에 이어읽기 조건·정렬·``LIMIT`` 을 걸어 한 쪽만 고른다.
#   ③ 바깥     : 고른 개체에만 이름·출처·설명·전체 건수·배열 4종·갈래를 집계한다.
# 왜 쪽을 먼저 고르나: 배열 집계·갈래 부분질의·전체 건수 부분질의를 노출 개체 전부에 돌린 뒤 자르면
#    화면에 안 나갈 개체 몫까지 계산한다. 정렬 키는 싸고 비싼 것은 카드 재료다.
# ⚠️ ①을 CTE 로 감싸는 이유: ``confirmed_count`` 는 ``HAVING COUNT(DISTINCT ge.src_node)`` **집계**라
#    같은 질의의 ``WHERE`` 에서 비교할 수 없다(SQL 규칙 — "집계 함수는 WHERE 에 올 수 없다"). 한 겹
#    감싸면 ②에서는 그냥 컬럼이라 이어읽기 조건을 걸 수 있다.
# 🔴 ①에는 자산 조인이 없다. 전제는 "소속 엣지의 출발점은 늘 자산 노드"다 — 소속 엣지를 만드는 곳은
#    ``src/mm_meta/persist.py`` 한 곳(출발점 = ``ensure_asset_node``)이고, 자산 노드는 자산 행을 외래키로
#    가리켜 자산 조인이 행을 빼지 않는다. 이 전제는 ``scripts/verify_relation_invariants.py`` 의
#    ``소속_엣지_방향_위반`` 검사가 센다. 덕분에 노출 정의가 ``_EXPOSED_ENTITIES_SQL`` 과 같아진다.
#    ③의 ``confirmed_count`` 는 ①의 값을 그대로 내보낸다 — 화면에 찍히는 건수와 정렬·커서 키가
#    어떤 경우에도 한 값이어야 이어읽기가 중복·누락 없이 이어진다.
# ③에서 종류·갈래·허용목록 조건을 다시 걸지 않는 이유: 셋 다 개체 단위 조건이라 ②가 고른 개체는 이미
#    만족하고, 개체가 가진 엣지 집합을 바꾸지 않는다. 소속 종류·상태 조건은 엣지 단위라 ③에서도 건다
#    — 빼면 배열이 거절된 소속까지 모은다.
# ③이 같은 4단 키로 다시 정렬하는 이유: ②의 순서는 조인·집계를 거치면 보장되지 않는다.
# ③의 ``GROUP BY`` 에 ``node_id`` 를 함께 묶는 이유: 전체 건수 부분질의가 이 컬럼을 참조한다. 그룹이
#    쪼개질 위험은 없다 — ``uq_node_entity(entity_type, entity_uid)`` 가 개체마다 node_id 1개를 보장한다.
#    ``p.prio_tier``·``p.confirmed_count`` 는 CTE 열이라 함께 묶어야 한다(PG 는 CTE 열의 함수 종속을
#    알아보지 못한다). ``page`` 는 개체마다 한 행이라 그룹이 늘지 않는다.
# ``{ent_mat}`` · ``{cnt_cte}`` · ``{cnt_col}`` 는 ``list_entities_with_total`` 만 채운다(``list_entities`` 는 빈 문자열 —
#    질의가 글자 하나까지 종전과 같다). 채우면 ①을 한 번만 만들어 두고 그 전체 개수를 바깥 행에 한 칸으로 싣는다
#    — 총계를 따로 세면 같은 노출 집합을 한 번 더 만든다(spec 113). 총계를 ``page`` 에서 세면 안 된다: 이어읽기
#    조건 · ``LIMIT`` 뒤라 모수가 아니라 쪽 크기가 나온다.
# 설계 배경: `specs/106-entity-list-speed` · `specs/113-entity-list-with-total`
_LIST_ENTITIES_SQL = """
WITH ent AS{ent_mat} (
SELECT n.node_id, n.entity_type, n.entity_uid,
       COUNT(DISTINCT ge.src_node) AS confirmed_count,
{first_tier}
  FROM node n
  JOIN graph_edge ge    ON ge.dst_node = n.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
 WHERE n.node_kind = 'entity'
   AND rk.kind_code = %(kind)s
   AND ge.status = ANY(%(statuses)s)
   AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)
   AND {area_filter}
   AND {allow_filter}
 GROUP BY n.node_id, n.entity_type, n.entity_uid
HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
),
page AS (
SELECT ent.* FROM ent
 WHERE {keyset}
 ORDER BY ent.prio_tier DESC, ent.confirmed_count DESC,
          ent.entity_uid ASC, ent.entity_type ASC
 LIMIT %(limit)s
){cnt_cte}
SELECT n.entity_type, n.entity_uid, n.node_id,
       COALESCE(n.canonical->>'name', n.entity_uid) AS name,
       COALESCE(n.canonical->>'source', 'auto')     AS source,
       n.canonical->>'description'                  AS description,
       p.confirmed_count                            AS confirmed_count,
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
           AND el.label_code <> 'unassigned')          AS areas,
       p.prio_tier{cnt_col}
  FROM page p
  JOIN node n          ON n.node_id = p.node_id
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
 WHERE rk.kind_code = %(kind)s
   AND ge.status = ANY(%(statuses)s)
 GROUP BY n.node_id, n.entity_type, n.entity_uid, n.canonical, p.prio_tier, p.confirmed_count
 ORDER BY p.prio_tier DESC, p.confirmed_count DESC,
          n.entity_uid ASC, n.entity_type ASC
"""

# 모수 총계(``scope_total`` — "좁히기를 지우면 N건") — **행을 조립하지 않는다**.
# 목록 질의를 다시 돌려 ``len()`` 을 세면 배열 집계(ARRAY_AGG 5종)·전체 건수 서브쿼리까지 헛돌고,
# 무엇보다 ``LIMIT`` 에 걸려 "돌려준 개수"가 나온다 — 그것은 모수가 아니다(화면에 200 이 찍히던 원인).
# 노출 개체의 정의는 다른 세 집계 함수와 **같은 조각**(``_EXPOSED_ENTITIES_SQL``)을 쓴다. 정의가
# 갈라지면 "822건 중 200건"의 822 와 목록이 서로 다른 모수를 말하게 된다.
_COUNT_ENTITIES_SQL = """
SELECT COUNT(*) AS n
  FROM ({exposed}) t
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
# ⚠️ hit 에서 한글 열(종류·표기)로 중복 제거(SELECT DISTINCT) 정렬을 하지 않는다 — DB 문자열 비교 규칙
#    (en_US.utf8 등)이 한글 비교에 비싸 라벨 행 전부를 정렬하면 칩 응답이 초 단위로 느려진다.
#    중복은 원래 생기지 않는다 — 라벨 행은 entity_mm_skill_label 기본키 (entity_type, entity_uid,
#    skill_code, label_code) 로 유일하고, EXISTS 반조인은 ex 에 같은 개체가 겹쳐도 행을 늘리지 않는다.
#    설계 배경: `specs/105-entity-area-count-speed`
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
      FROM entity_mm_skill_label el
     WHERE EXISTS (SELECT 1 FROM ex WHERE ex.entity_type = el.entity_type AND ex.entity_uid = el.entity_uid)
     GROUP BY 1, 2
)
SELECT lab.name, lab.skill, lab.skill_code, COALESCE(hit.n, 0) AS n
  FROM lab
  LEFT JOIN hit ON hit.skill_code = lab.skill_code AND hit.label_code = lab.code
 WHERE lab.code <> 'unassigned'
 ORDER BY n DESC, lab.skill_code, lab.ord
"""

# 개체 칩 세 축(종류 · 갈래 · 자료 유형)의 건수를 한 질의로 센다(``count_entity_facets``) — 노출 개체(``base``)를
#    한 번 만들어 세 축이 나눠 쓴다. ``base`` 는 ``_EXPOSED_ENTITIES_SQL`` 에서 조건을 뺀 것과 같은 정의여야 한다
#    (갈라지면 칩 숫자와 목록 · 모수가 서로 다른 개체를 센다).
# 🔴 칩 숫자 = 그 칩을 눌렀을 때 나올 결과 수. 축마다 **자기 축 조건만 빼고(종류) 또는 자기 값을 더해(갈래 · 자료
#    유형)** 나머지 조건을 모두 적용해 센다 — 화면의 클릭 규칙을 따른 것이다.
#    · 종류는 하나만 고르는 축이라 누르면 종류가 **바뀐다** → 종류 조건만 빼고 갈래 · 자료 유형은 적용한다. 종류
#      조건까지 걸면 하나를 고르는 순간 다른 종류가 모두 0 이 되어 갈아탈 칩이 사라진다.
#    · 갈래 · 자료 유형은 여럿을 AND 로 고르는 축이라 누르면 조건이 **더해진다** → 지금 조건을 모두 적용한 위에
#      자기 값을 더해 센다.
#    예: 갈래 「한식」을 고른 채 종류 칩 「인물」에 찍히는 수는 「한식」인 인물 수다(갈래를 무시한 인물 수가 아니다).
# 범위 CTE 와 축이 읽는 곳:
#    ``ascoped`` = ``base`` ∩ 갈래 — 종류 칩의 모수. ``scoped`` 에 갈래와 종류를 한꺼번에 걸면 종류 칩이 읽을
#        「종류만 뺀 범위」가 없어지므로, 갈래까지만 적용한 집합을 따로 둔다.
#    ``scoped`` = ``ascoped`` ∩ 종류 — 자료 유형을 고르지 않았을 때 갈래 · 자료 유형 칩의 모수.
#    ``have`` = 개체별 자료 유형. 자료 유형을 골랐으면 ``ascoped`` 에서 모은다 — 종류 칩도 「고른 유형을 모두 가진
#        개체」에서 세야 하는데, ``scoped`` 에서 모으면 고른 종류 밖의 개체가 빠져 다른 종류 칩이 모두 0 이 된다.
#        고르지 않았으면 ``scoped`` 에서 모은다.
#    ``pick`` = ``have`` 중 고른 유형을 모두 가진 개체(자료 유형을 골랐을 때만). 종류 칩 · 갈래 칩이 여기서 센다.
#        종류 열을 함께 실어 두고, 갈래 칩 · 자료 유형 칩의 종류 조건은 그 열로 건다(``{hit_type}`` · ``{mod_type}``).
#    자료 유형을 골랐으면 ``scoped`` 는 아무도 읽지 않는다 — PostgreSQL 은 읽지 않는 CTE 를 계산하지 않는다.
# ⚠️ ``pick`` 을 ``scoped`` 와 맞대지 않는다(``pick`` ∩ ``scoped`` 로 종류를 거르지 않는다) — CTE 끼리 맞대는 반조인은
#    플래너가 양쪽 행 수를 모두 작게 짐작해 중첩 반복을 고르고, 실제 개체가 많으면 개체 수의 제곱으로 느려진다.
#    ``pick`` 에 종류 열을 실어 둔 이유다.
# ⚠️ ``elab`` = 노출 개체(``base``)의 갈래 라벨 행. 갈래 칩(``hit``)은 라벨 표를 직접 읽지 않고 여기서 센다. 지우고
#    ``hit`` 가 라벨 표를 직접 읽게 하면, 범위(``pick`` · 종류를 건 ``scoped``)를 아주 작게 짐작할 때 플래너가
#    개체마다 라벨 인덱스를 조회하는 경로를 골라 실제 개체가 많으면 갈래 칩이 크게 느려진다. ``elab`` 을 먼저
#    만들어 두면 ``hit`` 는 두 집합을 해시로 묶는다(인덱스 경로가 끊긴다).
# 🔴 조건이 없으면 그 조각을 비운다(``%(x)s IS NULL OR …`` 로 한 문장에 담지 않는다) — 같은 연결에서 반복 실행하면
#    재사용 계획(값을 모른 채 세운 계획)이 그 조건을 걷어내지 못해 크게 느려진다. 조건별로 채우는 자리:
#    종류 → ``{type_where}``(``scoped``), 자료 유형도 있으면 ``{hit_type}`` · ``{mod_type}`` 까지.
#    갈래 → ``{amatch}``(``base`` 뒤 · ``ascoped`` 앞) · ``{area_where}``(``ascoped``).
#    자료 유형 → ``{pick}`` 을 넣고 ``{have_src}`` 를 ``ascoped`` 로, ``{type_src}`` · ``{hit_src}`` 를 ``pick`` 으로,
#    ``{picked_expr}`` 를 「고른 유형 ∪ 칩 유형」으로.
# 🔴 ``MATERIALIZED`` — PostgreSQL(12 이상)은 두 곳 이상에서 참조하는 CTE 를 기본으로 한 번만 계산하고, 한 곳에서만
#    참조하는 CTE 는 그 자리에 펼친다. 참조 수가 조건마다 달라 중간 집합을 모두 명시로 고정한다. 한 곳에서만
#    참조하는 것을 지우면 펼쳐진다 — ``elab`` 은 위 인덱스 경로로 돌아가고, ``have``(자료 유형을 고르지 않았을 때)는
#    개체별 자료 유형 판정이 칩마다 반복되고, ``amatch`` 는 플래너가 명단 크기를 잘못 짐작해 갈래 칩이 느려질 수 있다.
# 순서는 축마다 ``ord`` 로 받는다. 종류 · 갈래는 DB 문자열 비교 규칙으로 매긴 ``row_number()`` 다(파이썬 정렬과
#    갈릴 수 있다 — 종류는 이름순, 갈래는 개체 수 내림차순 → 스킬 코드 → 라벨 순서). 자료 유형은
#    ``modality_codes`` 순서(``WITH ORDINALITY``).
# 설계 배경: `specs/108-entity-facets-own-axis` · `specs/107-entity-facets`
_ENTITY_FACETS_SQL = """
WITH base AS MATERIALIZED (
    SELECT n.node_id, n.entity_type, n.entity_uid
      FROM node n
      JOIN graph_edge ge    ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
     WHERE n.node_kind = 'entity' AND rk.kind_code = %(kind)s AND ge.status = ANY(%(statuses)s)
     GROUP BY n.node_id, n.entity_type, n.entity_uid
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
),
{amatch}ascoped AS MATERIALIZED (
    SELECT b.* FROM base b
     WHERE TRUE {area_where}
),
scoped AS MATERIALIZED (
    SELECT s.* FROM ascoped s
     WHERE TRUE {type_where}
),
have AS MATERIALIZED (
    SELECT s.node_id, s.entity_type, ARRAY_AGG(DISTINCT a.modality::text) AS mods
      FROM {have_src} s
      JOIN graph_edge ge    ON ge.dst_node = s.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
      JOIN node sn          ON sn.node_id = ge.src_node
      JOIN asset a          ON a.asset_id = sn.asset_id
     WHERE rk.kind_code = %(kind)s AND ge.status = ANY(%(statuses)s)
       AND a.modality = ANY(%(have_codes)s)
     GROUP BY s.node_id, s.entity_type
),
{pick}lab AS (
    SELECT s.skill_code, s.name AS skill, (lb->>'code') AS code, (lb->>'name') AS name, ord
      FROM mm_skill s, LATERAL jsonb_array_elements(s.labels) WITH ORDINALITY AS t(lb, ord)
     WHERE s.status = 'active' AND s.skill_code <> ALL(%(reserved)s)
),
elab AS MATERIALIZED (
    SELECT el.skill_code, el.label_code, b.node_id
      FROM entity_mm_skill_label el
      JOIN base b ON b.entity_type = el.entity_type AND b.entity_uid = el.entity_uid
),
hit AS (
    SELECT e.skill_code, e.label_code, COUNT(*) AS n
      FROM elab e
     WHERE e.node_id IN (SELECT node_id FROM {hit_src}{hit_type})
     GROUP BY 1, 2
)
SELECT 'type' AS axis, t.entity_type AS name, NULL::text AS skill, NULL::text AS skill_code,
       COUNT(*) AS n, row_number() OVER (ORDER BY t.entity_type) AS ord
  FROM {type_src} t GROUP BY t.entity_type
UNION ALL
SELECT 'area', lab.name, lab.skill, lab.skill_code, COALESCE(hit.n, 0),
       row_number() OVER (ORDER BY COALESCE(hit.n, 0) DESC, lab.skill_code, lab.ord)
  FROM lab LEFT JOIN hit ON hit.skill_code = lab.skill_code AND hit.label_code = lab.code
 WHERE lab.code <> 'unassigned'
UNION ALL
SELECT 'modality', c.code, NULL, NULL,
       (SELECT COUNT(*) FROM have h WHERE h.mods @> {picked_expr}{mod_type}), c.ord
  FROM unnest(%(codes)s::text[]) WITH ORDINALITY AS c(code, ord)
"""

# 갈래 명단(``{amatch}`` 자리) — 고른 갈래 이름을 모두 가진 노출 개체의 ``node_id``(``_ENTITY_AREA_FILTER_SQL`` 과
#    같은 집합: 가진 고른 이름의 고유 수 = ``area_n``). ``node_id`` 로 묶는다 — (종류, 표기, 이름) 짝으로 묶으면 한글
#    열 정렬 비용이 붙는다. 개체마다 ``node_id`` 는 하나(``uq_node_entity``)라 결과는 같다.
# 끝의 쉼표는 뒤따르는 ``ascoped`` CTE 와 잇는 구분자다(조각이 빠지면 쉼표도 함께 빠진다).
_ENTITY_FACETS_AREA_MATCH_SQL = """\
amatch AS MATERIALIZED (
    SELECT b.node_id
      FROM base b
      JOIN entity_mm_skill_label el
        ON el.entity_type = b.entity_type AND el.entity_uid = b.entity_uid
      JOIN mm_skill es ON es.skill_code = el.skill_code
      CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
     WHERE (elb->>'code') = el.label_code
       AND (elb->>'name') = ANY(%(areas)s)
     GROUP BY b.node_id
    HAVING COUNT(DISTINCT (elb->>'name')) = %(area_n)s
),
"""

# 자료 유형을 고른 개체(``{pick}`` 자리 · 자료 유형을 골랐을 때만) — ``have`` 중 고른 유형을 **모두** 가진 개체.
#    종류 열을 함께 싣는다: 갈래 칩 · 자료 유형 칩은 이 열로 종류를 거르고, 종류 칩은 이 열로 묶어 센다
#    (``scoped`` 와 맞대지 않는 이유는 ``_ENTITY_FACETS_SQL`` 위 주석).
# 끝의 쉼표는 뒤따르는 ``lab`` CTE 와 잇는 구분자다(조각이 빠지면 쉼표도 함께 빠진다).
_ENTITY_FACETS_PICK_SQL = """\
pick AS MATERIALIZED (
    SELECT h.node_id, h.entity_type FROM have h WHERE h.mods @> %(picked)s::text[]
),
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


def _allow_params(uid_allow: set[tuple[str, str]] | None) -> dict[str, Any]:
    """개체 id 화이트리스트 바인딩 — (타입, 표기) 집합을 **두 배열**로 편다.

    🔴 ``None`` 과 빈 집합을 여기서 섞지 않는다 — ``None`` 은 ``NULL``(필터 없음)로, 빈 집합은
    **빈 배열**(매칭 0건)로 간다. 파이썬에서는 둘 다 거짓값이라 ``if not uid_allow`` 같은 한 줄이
    "검색했는데 전체가 나오는" 오류를 만든다. 그래서 ``is None`` 으로만 가른다.

    Args:
        uid_allow: 허용할 ``(entity_type, entity_uid)`` 집합. ``None`` 이면 필터를 걸지 않는다.

    Returns:
        ``{"allow_types": […] 또는 None, "allow_uids": […] 또는 None}`` — 짝이 유지되도록 **정렬**한다
        (같은 집합이면 같은 바인딩 · 헌법 3조).
    """
    if uid_allow is None:
        return {"allow_types": None, "allow_uids": None}
    pairs = sorted((str(etype), str(uid)) for etype, uid in uid_allow)
    return {"allow_types": [etype for etype, _ in pairs], "allow_uids": [uid for _, uid in pairs]}


def _semantic_params(uid_semantic: set[tuple[str, str]] | None) -> dict[str, Any]:
    """**뜻으로 앞세울** 개체 집합을 두 배열로 편다(순서 전용 · ``_first_params`` 와 같은 모양).

    Args:
        uid_semantic: 뜻으로 상위인 ``(entity_type, entity_uid)`` 집합. ``None`` 이면 없음.

    Returns:
        ``{"semantic_types": […] 또는 None, "semantic_uids": […] 또는 None}``. 짝이 유지되도록
        **정렬**한다(같은 집합이면 같은 바인딩 · 헌법 3조).
    """
    if uid_semantic is None:
        return {"semantic_types": None, "semantic_uids": None}
    pairs = sorted((str(etype), str(uid)) for etype, uid in uid_semantic)
    return {"semantic_types": [t for t, _ in pairs], "semantic_uids": [u for _, u in pairs]}


def _first_params(uid_first: set[tuple[str, str]] | None) -> dict[str, Any]:
    """**앞세울 개체** 집합을 (타입, 표기) 두 배열로 편다(099 G7 · 순서 전용).

    ⚠️ ``_allow_params`` 와 모양은 같지만 **뜻이 다르다**: 저쪽은 "무엇을 남길까"(거르기)라
    ``None``(전체)과 빈 집합(0건)이 정반대지만, 이쪽은 "무엇을 앞세울까"(순서)라 둘 다
    **앞세울 것이 없다**로 같은 결과가 된다. 그래도 두 표기를 모두 받는 이유는 호출부가 목록·총계에
    같은 인자 묶음을 그대로 넘길 수 있게 하기 위해서다.

    Args:
        uid_first: 맨 앞에 둘 ``(entity_type, entity_uid)`` 집합. ``None`` 이면 우선 대상 없음.

    Returns:
        ``{"first_types": […] 또는 None, "first_uids": […] 또는 None}`` — 짝이 유지되도록 **정렬**
        한다(같은 집합이면 같은 바인딩 · 헌법 3조).
    """
    if uid_first is None:
        return {"first_types": None, "first_uids": None}
    pairs = sorted((str(etype), str(uid)) for etype, uid in uid_first)
    return {"first_types": [etype for etype, _ in pairs], "first_uids": [uid for _, uid in pairs]}


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
    after_tier: int | None = None,
    after_count: int | None = None,
    after_uid: str | None = None,
    after_type: str | None = None,
    uid_allow: set[tuple[str, str]] | None = None,
    uid_first: set[tuple[str, str]] | None = None,
    uid_semantic: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """노출 개체(멀티모달 메타) **목록**을 구성 자산 수 내림차순으로 조회한다(읽기 전용 · 095 FR-1).

    ⚠️ 099 G7 부터 정렬 앞에 **우선 티어**가 한 단 붙는다(``uid_first`` — 호출부가 앞세우라고 준
    개체). 주지 않으면 전원 같은 티어라 **종전과 완전히 같은 순서**다.

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
        after_tier: **이어읽기 책갈피** ①(099 G7) — 직전 쪽 마지막 개체의 우선 티어(1=앞세운 개체·
            0=나머지). 정렬 첫 키라 이것부터 잇는다. ``None`` 이면 첫 쪽이다.
        after_count: 이어읽기 책갈피 ② — 직전 쪽 마지막 개체의 ``confirmed_count``. 같은 티어 안에서
            이 값보다 적은 개체(그리고 같은 값이면 ``after_uid`` 보다 뒤인 개체)부터 잇는다.
        after_type: 이어읽기 책갈피 ④ — ``entity_type``. 표기까지 같은 자리를 가른다(자연키가
            (종류, 표기) 둘이라 표기만으로는 유일하지 않다).
        after_uid: 이어읽기 책갈피 ③ — ``entity_uid``. 🔴 네 값은
            **함께 주거나 함께 생략**해야 한다 — 일부만 주면 ``ValueError``.
        uid_allow: 허용할 개체 ``(entity_type, entity_uid)`` 집합 — 검색(집합 판정)이 고른 개체만
            남기는 화이트리스트다(099 G4). 🔴 **``None`` 과 빈 집합은 다른 값이다**: ``None`` 이면
            필터를 걸지 않아 **종전과 완전히 같고**, 빈 집합이면 **0건**이다(검색했는데 매칭이 없음).
        uid_semantic: 뜻으로 상위인 개체 집합(순서만 바꾼다 · 거르지 않는다). 이름 일치
            (``uid_first``)보다 한 단 아래 티어다 — 이름이 더 확실한 신호라서다.
            ``None``·빈 집합이면 앞세울 것이 없다(둘의 뜻이 같다 · 거르기가 아니므로).
        uid_first: **맨 앞에 둘** 개체 집합(099 G7 · 순서만 바꾼다 · 거르지 않는다). 이름으로 찾아도
            그 개체가 7위·15위에 있던 결함을 푸는 자리다. 🔴 누가 우선인지의 **판정 규칙은 호출부**
            (화면)에 있다 — 코어는 "이 짝들을 앞세워라"만 안다(093 책무 경계).
            ⚠️ ``uid_allow`` 와 달리 ``None`` 과 빈 집합의 **뜻이 같다**(앞세울 것이 없음 = 종전 순서).

    Raises:
        ValueError: ``after_tier``·``after_count``·``after_uid`` 중 일부만 준 경우. 조용히 무시하면
            첫 쪽을 다시 돌려주어 **중복**이 나는데, 오류가 없어 화면은 그것을 알 수 없다.

    Returns:
        ``[{entity_type, entity_uid, node_id, name, source, description, confirmed_count, total_count,
        modalities, keywords, topics, forms, areas}]`` — 우선 티어 내림차순 → 구성 자산 수 내림차순 →
        표기 키 오름차순(``uid_first`` 가 없으면 종전과 같은 두 단 정렬이다).
        배열 필드는 빈 값을 뺀 **가나다 순**이다(같은 입력이면 같은 순서 · 헌법 3조). ``keywords`` 는 원문
        **전부**다 — 상위 몇 개를 어떤 순서로 보일지는 호출자 몫. id 는 전부 문자열.
    """
    sql, params = _list_entities_query(
        entity_type=entity_type, area_names=area_names, min_bundle_size=min_bundle_size, limit=limit,
        statuses=statuses, form_skill_codes=form_skill_codes,
        bookmark=(after_tier, after_count, after_uid, after_type),
        uid_allow=uid_allow, uid_first=uid_first, uid_semantic=uid_semantic, with_total=False)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_shape_entity_row(r) for r in rows]


def list_entities_with_total(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    min_bundle_size: int,
    limit: int,
    statuses: list[str] | None = None,
    form_skill_codes: list[str] | None = None,
    after_tier: int | None = None,
    after_count: int | None = None,
    after_uid: str | None = None,
    after_type: str | None = None,
    uid_allow: set[tuple[str, str]] | None = None,
    uid_first: set[tuple[str, str]] | None = None,
    uid_semantic: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """개체 목록 한 쪽과 **총계**를 한 질의로 돌려준다(읽기 전용 · spec 113).

    ``list_entities`` 한 번 + ``count_entities`` 한 번과 결과가 같다 — 쪽 행은 모양 · 순서까지 같고, 총계는
    같은 조건의 모수다(책갈피 · ``limit`` · 우선 티어와 무관). 다른 점은 노출 개체 집합을 **한 번만** 만든다는
    것이다. 두 함수를 따로 부르면 소속 간선 조인 → 개체별 묶기를 두 번 한다(k8s 실측: 요청마다 9~161ms 더).

    🔴 쪽이 비면 행이 없어 총계를 실을 곳이 없다 — 그때만 같은 조건의 ``count_entities`` 로 따로 센다(질의 두 번 ·
    결과 0건이거나 끝을 넘긴 이어 받기뿐이라 드물다). 첫 쪽이 비면 0 이라고 단정하지 않는 이유: 바깥 질의의 자산
    조인이 행을 빼는 경우(소속 엣지 출발점이 자산이 아닌 데이터 오류)에 총계가 조용히 0 으로 거짓말을 한다.

    Args:
        entity_type: 종류 필터 — ``list_entities`` 와 같다. ``None`` 이면 전체.
        area_names: 갈래 이름들(모두 가진 개체만) — ``list_entities`` 와 같다.
        min_bundle_size: 노출 임계(구성 자산 수 하한) — 쪽과 총계에 함께 걸린다.
        limit: 쪽 크기. 총계에는 영향이 없다.
        statuses: 소속 엣지 상태 — ``list_entities`` 와 같다(``None`` 이면 active+proposed).
        form_skill_codes: 형식 축으로 읽을 자산 라벨 스킬 코드 — ``list_entities`` 와 같다.
        after_tier: 이어읽기 책갈피 ① — ``list_entities`` 와 같다. 네 값은 함께 주거나 함께 생략한다.
        after_count: 이어읽기 책갈피 ②.
        after_uid: 이어읽기 책갈피 ③.
        after_type: 이어읽기 책갈피 ④.
        uid_allow: 개체 화이트리스트 — 쪽과 총계에 함께 걸린다. ``None`` = 필터 없음 · 빈 집합 = 0건.
        uid_first: 맨 앞에 둘 개체 집합(순서만 바꾼다) — 총계에는 영향이 없다.
        uid_semantic: 뜻으로 앞세울 개체 집합(순서만 바꾼다) — 총계에는 영향이 없다.

    Returns:
        ``(쪽 행, 총계)`` — 쪽 행은 ``list_entities`` 의 반환과 같은 모양, 총계는 0 이상의 정수.

    Raises:
        ValueError: 책갈피 네 값 중 일부만 준 경우(``list_entities`` 와 같다).
    """
    bookmark = (after_tier, after_count, after_uid, after_type)
    sql, params = _list_entities_query(
        entity_type=entity_type, area_names=area_names, min_bundle_size=min_bundle_size, limit=limit,
        statuses=statuses, form_skill_codes=form_skill_codes, bookmark=bookmark,
        uid_allow=uid_allow, uid_first=uid_first, uid_semantic=uid_semantic, with_total=True)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if rows:
        total = int(rows[0]["scope_n"])
    else:
        total = count_entities(conn, entity_type=entity_type, area_names=area_names,
                               min_bundle_size=min_bundle_size, statuses=statuses, uid_allow=uid_allow)
    return [_shape_entity_row(r) for r in rows], total


def _list_entities_query(
    *,
    entity_type: str | None,
    area_names: list[str] | None,
    min_bundle_size: int,
    limit: int,
    statuses: list[str] | None,
    form_skill_codes: list[str] | None,
    bookmark: tuple[int | None, int | None, str | None, str | None],
    uid_allow: set[tuple[str, str]] | None,
    uid_first: set[tuple[str, str]] | None,
    uid_semantic: set[tuple[str, str]] | None,
    with_total: bool,
) -> tuple[str, dict[str, Any]]:
    """목록 질의와 바인딩을 만든다 — ``list_entities`` · ``list_entities_with_total`` 이 함께 쓴다(두 벌이면 한쪽만 고쳐진다).

    Args:
        entity_type: 종류 필터(``None`` 이면 전체).
        area_names: 갈래 이름들(모두 가진 개체만).
        min_bundle_size: 노출 임계.
        limit: 쪽 크기.
        statuses: 소속 엣지 상태(``None`` 이면 active+proposed).
        form_skill_codes: 형식 축 스킬 코드.
        bookmark: 이어읽기 책갈피 ``(tier, count, uid, type)`` — 모두 ``None`` 이면 첫 쪽.
        uid_allow: 개체 화이트리스트(``None`` = 필터 없음 · 빈 집합 = 0건).
        uid_first: 맨 앞에 둘 개체 집합.
        uid_semantic: 뜻으로 앞세울 개체 집합.
        with_total: 참이면 ``ent`` 를 물질화하고 그 전체 개수를 ``scope_n`` 칸으로 싣는다.

    Returns:
        ``(sql, params)`` — ``cur.execute`` 에 그대로 넘긴다.

    Raises:
        ValueError: 책갈피 네 값 중 일부만 준 경우.
    """
    # 네 값은 **함께** 와야 한다 — 하나라도 빠지면 이어읽기 조건이 성립하지 않는다(정렬 키가
    # 넷이라 값도 넷이다. 옛 2·3값 토큰은 호출부의 커서 길이 검사가 400 으로 막는다).
    if any(v is not None for v in bookmark) and any(v is None for v in bookmark):
        raise ValueError(
            "이어읽기 책갈피는 after_tier·after_count·after_uid·after_type 을 함께 줘야 한다"
            "(반쪽이면 중복이 난다)")
    after_tier, after_count, after_uid, after_type = bookmark
    params = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "etype": entity_type,
        "minsize": int(min_bundle_size),
        "limit": int(limit),
        "form_skills": [str(c) for c in (form_skill_codes or [])],
        "after_tier": None if after_tier is None else int(after_tier),
        "after_count": None if after_count is None else int(after_count),
        "after_uid": None if after_uid is None else str(after_uid),
        "after_type": None if after_type is None else str(after_type),
        **_area_params(area_names),
        **_allow_params(uid_allow),
        **_first_params(uid_first),
        **_semantic_params(uid_semantic),
    }
    sql = _LIST_ENTITIES_SQL.format(
        area_filter=_ENTITY_AREA_FILTER_SQL.format(alias="n"),
        allow_filter=_ENTITY_ALLOW_FILTER_SQL.format(alias="n"),
        first_tier=_ENTITY_FIRST_TIER_SQL.strip("\n"),
        keyset=_ENTITY_KEYSET_SQL.strip(),
        # ``ent`` 를 두 곳(page · cnt)에서 읽으면 PG 가 알아서 물질화하지만, 한 번만 만든다는 것이 이 함수의
        # 존재 이유라 명시한다(기본값이 바뀌어도 두 번 만들지 않게).
        ent_mat=" MATERIALIZED" if with_total else "",
        cnt_cte=",\ncnt AS (SELECT COUNT(*) AS n FROM ent)" if with_total else "",
        cnt_col=",\n       (SELECT c.n FROM cnt c) AS scope_n" if with_total else "")
    return sql, params


def _shape_entity_row(r: dict[str, Any]) -> dict[str, Any]:
    """목록 질의 한 행 → 반환 모양(id 는 문자열 · 배열은 빈 값을 뺀 가나다 순). 총계 칸(``scope_n``)은 싣지 않는다.

    Args:
        r: 목록 질의의 한 행(dict).
    """
    return {
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


def count_entities(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    min_bundle_size: int,
    statuses: list[str] | None = None,
    uid_allow: set[tuple[str, str]] | None = None,
    uid_first: set[tuple[str, str]] | None = None,
    uid_semantic: set[tuple[str, str]] | None = None,
) -> int:
    """지금 걸린 조건으로 **노출 개체가 모두 몇 개인지** 센다(읽기 전용 · 099 FR-006).

    화면의 "N건 중 M건"에서 **N** 을 만드는 값이다. 목록(``list_entities``)은 한 쪽(``limit``)만 돌려주므로
    돌려준 개수를 세면 모수가 아니라 **쪽 크기**가 나온다 — 200개만 받아 놓고 "200건"이라 적던 것이
    그 오해였다(실제 노출 대상은 822개). 조건은 목록과 **같게** 걸되 카드 재료(이름·모달리티·키워드…)는
    조립하지 않는다. 새 LLM 호출 없음.

    Args:
        entity_type: 종류(타입) 필터. ``None`` 이면 전체.
        area_names: 갈래(개체 라벨) 이름들 — **모두 가진** 개체만(AND). ``None``·빈 목록이면 필터 없음.
        min_bundle_size: 노출 임계 — 구성 자산 수가 이 값 이상인 개체만(목록과 같은 값을 줘야 한다).
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed(목록 기본과 같다).
        uid_allow: 개체 화이트리스트 — **목록과 같은 값을 줘야 한다**(099 G4). 총계와 목록이 다른
            모수를 말하면 "N건 중 M건"이 거짓말이 된다. ``None`` = 필터 없음 · 빈 집합 = 0건.
        uid_semantic: 목록에서 뜻으로 앞세울 개체 집합. 🔴 총계는 이 값에 영향받지 않는다 —
            순서 인자를 받는 것은 호출부가 목록·총계에 같은 인자 묶음을 그대로 넘기게 하기 위해서다.
        uid_first: 목록에서 **맨 앞에 둘** 개체 집합(099 G7). 🔴 **총계는 이 값에 영향을 받지 않는다**
            — 순서를 바꿀 뿐 대상을 늘리거나 줄이지 않기 때문이다(줄을 어떻게 세우든 사람 수는 같다).
            그래도 인자로 받는 이유는 호출부가 목록·총계에 **같은 인자 묶음**을 그대로 넘겨
            "조건이 어긋난 두 수"를 만들지 않게 하기 위해서다. 단위 테스트가 무영향을 봉인한다.

    Returns:
        개체 수(0 이상). 상한에 걸리지 않는 **모수**다.
    """
    params = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "etype": entity_type,
        "minsize": int(min_bundle_size),
        **_area_params(area_names),
        **_allow_params(uid_allow),
    }
    exposed = _EXPOSED_ENTITIES_SQL.format(
        type_cond=_TYPE_COND_SQL,
        area_cond="AND " + _ENTITY_AREA_FILTER_SQL.format(alias="n"),
        allow_cond="AND " + _ENTITY_ALLOW_FILTER_SQL.format(alias="n"),
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_COUNT_ENTITIES_SQL.format(exposed=exposed), params)
        row = cur.fetchone()
    return int(row["n"]) if row else 0


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
    # 종류 칩은 조건을 걸지 않는다(갈아타는 축) — 검색 화이트리스트도 얹지 않는다(현행 유지 · T019a).
    exposed = _EXPOSED_ENTITIES_SQL.format(type_cond="", area_cond="", allow_cond="")
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
        allow_cond="",   # 칩·묶음 내려받기는 검색 화이트리스트를 쓰지 않는다(현행 유지 · T019a)
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_COUNT_BY_AREA_SQL.format(exposed=exposed), params)
        rows = cur.fetchall()
    return [
        {"name": str(r["name"]), "skill": str(r["skill"]), "skill_code": str(r["skill_code"]),
         "count": int(r["n"])}
        for r in rows
    ]


def count_entity_facets(
    conn: Connection[Any],
    *,
    entity_type: str | None = None,
    area_names: list[str] | None = None,
    modalities: list[str] | None = None,
    min_bundle_size: int,
    modality_codes: list[str],
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    """개체 칩 세 축(종류 · 갈래 · 자료 유형)의 노출 개체 수를 한 질의로 센다(읽기 전용).

    노출 개체 집합을 한 번 만들어 세 축이 나눠 쓴다. 칩 숫자 = **그 칩을 눌렀을 때 나올 결과 수**다 — 종류는
    종류만 그 값으로 바꾼 결과(갈래 · 자료 유형 조건은 그대로), 갈래 · 자료 유형은 지금 조건에 그 값을 더한
    결과. 예: 갈래 「한식」을 고른 채 종류 칩 「인물」에 찍히는 수는 「한식」인 인물 수이고, 자료 유형 「이미지」를
    고른 채 갈래 칩 「역사」에 찍히는 수는 이미지 자료가 있으면서 「역사」인 개체 수다. 눌렀을 때 나올 결과란 그
    조건으로 부른 ``count_entities``(자료 유형은 「고른 유형 ∪ 칩 유형」을 모두 가진 개체만 허용목록으로 준 것)다.
    옛 함수와는 수가 다를 수 있다 — ``count_entities_by_type`` 은 종류 칩을 조건 없이 세어 갈래나 자료 유형을
    골랐을 때, ``count_entities_by_area`` 는 갈래 칩을 자료 유형 없이 세어 자료 유형을 골랐을 때 달라진다.

    Args:
        entity_type: 고른 종류. ``None`` 이면 종류 조건 없음(빈 문자열은 조건이다). 갈래 · 자료 유형 칩에 걸리고
            종류 칩에는 걸지 않는다 — 종류 칩은 종류를 바꿨을 때의 수라 자기 조건만 빼고 센다(걸면 하나를
            고르는 순간 다른 종류가 모두 0 이 되어 갈아탈 칩이 사라진다).
        area_names: 이미 고른 갈래들(AND). ``None`` · 빈 목록 · 공백 이름뿐이면 갈래 조건 없음. 세 축 모두에
            걸린다(갈래 칩은 그 위에 자기 갈래를 더해 센다).
        modalities: 이미 고른 자료 유형들(AND). ``None`` · 빈 목록이면 없음. 세 축 모두에 걸린다(자료 유형 칩은
            그 위에 자기 유형을 더해 센다). ``modality_codes`` 밖의 유형이어도 조건으로 건다.
        min_bundle_size: 노출 임계(구성 자산 수 하한). 목록과 같은 값을 준다.
        modality_codes: 칩으로 셀 자료 유형 코드들 — 화면 정책이라 호출자가 준다. 반환
            ``modalities`` 의 키와 순서가 된다.
        statuses: 소속 엣지 상태. ``None`` 이면 active+proposed.

    Returns:
        ``{"types": {종류: 개체 수}, "areas": [{name, skill, skill_code, count}], "modalities":
        {코드: 개체 수}}``. ``types`` 는 종류 이름순(DB 정렬 규칙)이고 개체가 없는 종류는 키가 없다.
        ``areas`` 는 0건 갈래도 담고 개체 수 내림차순 → 스킬 코드 → 라벨 순서다. ``modalities`` 는
        ``modality_codes`` 순서이고 0건도 담는다.
    """
    from src.mm_classify.model import NON_CLASSIFY_SKILL_CODES  # 순환 import 회피(model 은 순수)

    params: dict[str, Any] = {
        "kind": MM_MEMBER_KIND_CODE,
        "statuses": _wanted_statuses(statuses),
        "minsize": int(min_bundle_size),
        "reserved": sorted(NON_CLASSIFY_SKILL_CODES),
        "codes": list(modality_codes),
    }
    # 자리마다 무엇이 들어가는지는 ``_ENTITY_FACETS_SQL`` 위 주석(조건별로 채우는 자리)에 있다.
    type_where = ""
    # ``None`` 만 「조건 없음」이다 — 빈 문자열도 조건으로 건다(기존 함수의 ``IS NULL OR`` 와 같은 판정).
    if entity_type is not None:
        type_where = " AND s.entity_type = %(etype)s"
        params["etype"] = entity_type
    area_where = amatch = ""
    # 빈 목록 · 공백 이름 정규화는 기존 함수와 같은 ``_area_params`` 에 맡긴다(판정이 갈리면 칩 수가 갈린다).
    area = _area_params(area_names)
    if area["areas"] is not None:
        area_where = " AND b.node_id IN (SELECT node_id FROM amatch)"
        amatch = _ENTITY_FACETS_AREA_MATCH_SQL
        params.update(area)
    # 정렬 · 고유로 묶는다 — 같은 고름이면 같은 바인딩이 된다(순서 · 중복이 달라도 같은 질의).
    picked = sorted({str(m) for m in (modalities or [])})
    if picked:
        params["picked"] = picked
        # 종류 · 갈래 칩은 ``pick``(고른 유형을 모두 가진 개체)에서 센다. 종류 조건은 ``pick`` · ``have`` 가 실은 종류
        # 열로 갈래 칩 · 자료 유형 칩에만 건다 — 종류 칩은 자기 조건을 빼고, ``pick`` 은 ``scoped`` 와 맞대지 않는다.
        type_filter = entity_type is not None
        slots = {
            "have_src": "ascoped",
            "pick": _ENTITY_FACETS_PICK_SQL,
            "type_src": "pick",
            "hit_src": "pick",
            "hit_type": " WHERE entity_type = %(etype)s" if type_filter else "",
            "picked_expr": "(%(picked)s::text[] || c.code)",
            "mod_type": " AND h.entity_type = %(etype)s" if type_filter else "",
        }
    else:
        # 종류 칩은 ``ascoped``(갈래만 적용), 갈래 · 자료 유형 칩은 ``scoped``(종류까지 적용)에서 센다.
        slots = {
            "have_src": "scoped",
            "pick": "",
            "type_src": "ascoped",
            "hit_src": "scoped",
            "hit_type": "",
            "picked_expr": "ARRAY[c.code]",
            "mod_type": "",
        }
    # ``have`` 가 모을 유형 = 칩 유형 ∪ 고른 유형. 고른 유형이 칩 목록 밖이어도 모아야 「고른 유형을 모두
    # 가진 개체」 판정이 기존 허용목록과 같다(칩 유형만 모으면 그 칩들이 모두 0 이 된다).
    params["have_codes"] = sorted({str(c) for c in modality_codes} | set(picked))
    sql = _ENTITY_FACETS_SQL.format(amatch=amatch, area_where=area_where, type_where=type_where, **slots)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # UNION ALL 은 행 순서를 보장하지 않는다 — 축마다 SQL 이 준 ``ord`` 로 다시 줄 세운다.
    type_rows = sorted((r for r in rows if r["axis"] == "type"), key=lambda r: int(r["ord"]))
    area_rows = sorted((r for r in rows if r["axis"] == "area"), key=lambda r: int(r["ord"]))
    mod_rows = sorted((r for r in rows if r["axis"] == "modality"), key=lambda r: int(r["ord"]))
    return {
        "types": {str(r["name"]): int(r["n"]) for r in type_rows},
        "areas": [
            {"name": str(r["name"]), "skill": str(r["skill"]), "skill_code": str(r["skill_code"]),
             "count": int(r["n"])}
            for r in area_rows
        ],
        "modalities": {str(r["name"]): int(r["n"]) for r in mod_rows},
    }


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
        allow_cond="",   # 칩·묶음 내려받기는 검색 화이트리스트를 쓰지 않는다(현행 유지 · T019a)
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ASSETS_OF_ENTITIES_SQL.format(exposed=exposed), params)
        rows = cur.fetchall()
    return [
        {"asset_id": str(r["asset_id"]), "modality": str(r["modality"] or ""),
         "fs_path": r["fs_path"], "file_size": int(r["file_size"] or 0)}
        for r in rows
    ]
