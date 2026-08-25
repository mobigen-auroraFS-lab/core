"""084 T003 — 멀티모달 메타 **영속**(``src/mm_meta/persist.py``) 단위 테스트(모의 커넥션·네트워크 0).

무엇을 검증하나: 판정 결과(개체 목록)를 노드·엣지·계보로 남기는 층이다. 이 층에는 **스키마가 막아
주지 못하는 계약 5건**이 걸려 있어 그것을 못 박는 것이 이 파일의 목적이다.

    ① **대표 표기 1회 고정** — 같은 (타입, 표기 키) 노드가 이미 있으면 표기를 덮지 않는다. 덮으면
       배치가 도는 순서에 따라 묶음 이름이 흔들려 결정성을 잃는다(spec §4 · 2026-08-21 정정).

    ② **자산 스코프 교체** — 재판정은 그 자산의 기존 ``mm_member`` 엣지를 **모두 지우고** 다시
       넣는다. 규칙이 강화돼 이제 제외돼야 할 옛 엣지가 남으면 "왜 이 파일이 이 묶음에 있나"를
       설명할 수 없다(spec §6). 지우는 범위는 **그 자산 · mm_member 종류**로 한정된다 — 다른
       종류(자산↔자산 관계) 엣지를 건드리면 기존 기능이 소리 없이 사라진다.

    ③ **멱등** — 같은 판정을 두 번 영속해도 엣지 총수는 늘지 않는다(spec §8 합격선).

    ④ **reason 스탬프 왕복** — ``kw=…|pv=…|rv=…`` 형식이 파서로 되돌아온다. 배치의 재선별이 이
       값을 읽어 "옛 규칙으로 만든 엣지"를 찾으므로(구현 확정 3), 형식이 깨지면 백필이 멈춘다.

    ⑤ **kind 미등록 시 침묵 금지** — ``mm_member`` 카탈로그 행이 없으면 **예외**다. 조용히 0건을
       쓰면 "묶음이 왜 안 생기나"를 매번 다시 조사한다(닫힌 taxonomy 시드 누락으로 관계 0건이던
       선례와 같은 함정).

DB·LLM·네트워크 불필요 — 아래 ``_Conn`` 이 노드·엣지·계보를 파이썬 dict 로 들고 있는 **작은 가짜
DB** 다(실행된 SQL 도 함께 모은다). 실 DB 왕복(UUID→str 계약 등 모의로 못 잡는 결함)은 사람 몫이다
(T008 · ``RUN_DB_E2E``).

⚠️ 여기 쓰인 자산 id 는 전부 **더미**다(UUIDv7 형태만 맞춘 상수) — 실 자산 id 는 코드 레포에 넣지
않는다.
"""

from __future__ import annotations

import contextlib
import json
import unittest
import uuid
from typing import Any

from src.domain.text_norm import normalize_text_key
from src.mm_meta.describe import DESC_PROMPT_VERSION, build_description_prompt
from src.mm_meta.judge import PROMPT_VERSION
from src.mm_meta.persist import (
    DESC_REGEN_MIN_DELTA_RATIO,
    DESC_TARGET_REASONS,
    LINEAGE_ACTIVITY,
    MM_MEMBER_KIND_CODE,
    MM_META_SOURCE_AUTO,
    MM_META_SOURCE_USER,
    MM_META_VISIBLE_STATUSES,
    MmMetaPersistError,
    ensure_entity_node,
    ensure_mm_member_kind,
    fetch_meta_description_targets,
    fetch_meta_members,
    fetch_official_name_index,
    fetch_registered_alias_index,
    format_member_reason,
    list_mm_meta,
    normalize_registration,
    parse_member_reason,
    register_mm_meta,
    resolve_registered_aliases,
    upsert_entity_edges,
    upsert_meta_description,
)
from src.mm_meta.rules import (
    RULE_VERSION,
    ExtractedEntity,
    apply_rules,
    build_official_name_index,
)

# 더미 식별자(실 자산 id 금지 — 형태만 UUIDv7).
_ASSET = "018f0000-0000-7000-8000-0000000000a1"
_ASSET2 = "018f0000-0000-7000-8000-0000000000a2"
_ASSET3 = "018f0000-0000-7000-8000-0000000000a3"
_KIND_ID = "018f0000-0000-7000-8000-0000000000c1"


class _Cur:
    """가짜 커서 — 실행 SQL 을 로그에 쌓고 가짜 DB(``_Conn``)에 위임한다.

    ``row_factory`` 를 흉내내는 이유: 호출부가 ``conn.cursor()``(튜플 행)와
    ``conn.cursor(row_factory=dict_row)``(dict 행)를 섞어 쓴다(기존 ``graph_persist``·
    ``relation_type_catalog`` 관례). 가짜 DB 는 언제나 dict 를 만들고, 여기서 튜플로 되돌린다.
    """

    def __init__(self, conn: _Conn, row_factory: Any = None) -> None:
        self._conn = conn
        self._dict_rows = row_factory is not None
        self._rows: list[dict[str, Any]] = []

    def execute(self, sql: Any, params: Any = None) -> _Cur:
        flat = " ".join(str(sql).split())
        self._conn.log.append((flat, params))
        self._rows = self._conn.dispatch(flat, params)
        return self

    def fetchone(self) -> Any:
        return self._row(self._rows[0]) if self._rows else None

    def fetchall(self) -> list[Any]:
        return [self._row(r) for r in self._rows]

    def _row(self, row: dict[str, Any]) -> Any:
        return row if self._dict_rows else tuple(row.values())

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _Conn:
    """가짜 커넥션 = **작은 인메모리 DB**(relation_kind·node·graph_edge·asset_lineage).

    상태를 들고 있어야 "두 번 호출해도 엣지가 안 늘어난다"(멱등)·"옛 엣지가 지워진다"(교체) 같은
    **행동**을 검증할 수 있다. SQL 문자열 조각으로 문장을 알아보므로, 구현이 문장 형태를 크게
    바꾸면 여기도 함께 고쳐야 한다(의도적 결합 — 이 층의 SQL 은 계약이다).

    Args:
        kind: ``mm_member`` 카탈로그 행 초기값. ``None`` 이면 **미등록** 상태에서 시작한다.
    """

    def __init__(self, *, kind: dict[str, Any] | None = None) -> None:
        self.log: list[tuple[str, Any]] = []
        self.kind = dict(kind) if kind else None
        self.asset_nodes: dict[str, str] = {}          # asset_id → node_id
        self.entity_nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}     # edge_id → 행
        self.lineage: list[dict[str, Any]] = []
        # 자산 재료(설명 생성 입력) — asset_id → {modality, summary}. 실 DB 의 ``asset`` +
        # ``asset_metadata`` 두 테이블을 합친 모양이다. **행이 없는 자산도 소속될 수 있다** —
        # 메타데이터가 없는 자산(요약 미생성)을 흉내내려는 것이고, 그때 조회는 빈 요약을 준다
        # (구현이 LEFT JOIN 을 쓰는 이유를 이 가짜 DB 가 함께 재현한다).
        self.assets: dict[str, dict[str, Any]] = {}
        self.tx_depth = 0

    # ── psycopg3 형상 ────────────────────────────────────────────────────────
    def cursor(self, row_factory: Any = None, **_k: Any) -> _Cur:
        return _Cur(self, row_factory)

    @contextlib.contextmanager
    def transaction(self):  # noqa: ANN201 - 테스트 헬퍼
        self.tx_depth += 1
        try:
            yield self
        finally:
            self.tx_depth -= 1

    # ── 가짜 DB ─────────────────────────────────────────────────────────────
    def dispatch(self, sql: str, params: Any) -> list[dict[str, Any]]:
        p = tuple(params or ())
        if "INSERT INTO relation_kind" in sql:
            if self.kind is None:
                self.kind = {"relation_kind_id": _KIND_ID, "kind_code": p[0],
                             "kind_name_ko": p[1], "description": p[2],
                             "is_symmetric": p[3], "status": p[4]}
            return [{"relation_kind_id": self.kind["relation_kind_id"]}]
        if "UPDATE relation_kind" in sql:
            if self.kind is not None:
                self.kind["is_symmetric"] = False
                self.kind["status"] = "active"
            return []
        if "FROM relation_kind" in sql:  # fetch_relation_kind
            k = self.kind
            if k is None or k["kind_code"] != p[0]:
                return []
            if len(p) > 1 and k["status"] != p[1]:
                return []
            return [{"relation_kind_id": k["relation_kind_id"], "is_symmetric": k["is_symmetric"]}]
        if sql.startswith("SELECT node_id FROM node WHERE node_kind = 'asset'"):
            nid = self.asset_nodes.get(str(p[0]))
            return [{"node_id": nid}] if nid else []
        if "INSERT INTO node (node_id, node_kind, asset_id)" in sql:
            nid = self.asset_nodes.setdefault(str(p[1]), str(p[0]))
            return [{"node_id": nid}]
        if sql.startswith("SELECT node_id, canonical FROM node"):
            row = self.entity_nodes.get((p[0], p[1]))
            return [{"node_id": row["node_id"], "canonical": row["canonical"]}] if row else []
        if "INSERT INTO node (node_id, node_kind, entity_type, entity_uid, canonical)" in sql:
            key = (p[1], p[2])
            if key in self.entity_nodes:      # 동시 삽입 경쟁 흉내(ON CONFLICT DO NOTHING)
                return []
            self.entity_nodes[key] = {"node_id": str(p[0]), "entity_type": p[1],
                                      "entity_uid": p[2], "canonical": json.loads(p[3])}
            return [{"node_id": str(p[0])}]
        if sql.startswith("SELECT entity_type, entity_uid, canonical FROM node"):
            return [
                {"entity_type": r["entity_type"], "entity_uid": r["entity_uid"],
                 "canonical": r["canonical"]}
                for r in sorted(self.entity_nodes.values(),
                                key=lambda r: (r["entity_type"], r["entity_uid"]))
            ]
        if sql.startswith("SELECT en.entity_type, en.entity_uid, count(DISTINCT"):
            # 묶음 크기 조회 — p = (statuses, kind_code). GROUP BY 라 **소속 0인 개체는 행이 없다**
            # (읽는 쪽이 "없으면 0"으로 본다). 자산 노드에서 온 엣지만 세는 JOIN 도 함께 흉내낸다.
            if self.kind is None or self.kind["kind_code"] != p[1]:
                return []   # 카탈로그 행 미등록 = 어떤 엣지도 조건을 못 맞춘다
            asset_node_ids = set(self.asset_nodes.values())
            out = []
            for row in sorted(self.entity_nodes.values(),
                              key=lambda r: (r["entity_type"], r["entity_uid"])):
                assets = {
                    e["src_node"] for e in self.edges.values()
                    if e["dst_node"] == row["node_id"]
                    and e["relation_kind_id"] == self.kind["relation_kind_id"]
                    and e["status"] in p[0]
                    and e["src_node"] in asset_node_ids
                }
                if assets:
                    out.append({"entity_type": row["entity_type"],
                                "entity_uid": row["entity_uid"], "bundle_size": len(assets)})
            return out
        if sql.startswith("SELECT entity_type, entity_uid, COALESCE(NULLIF(canonical"):
            # 등록 목록 조회 — p = (source|None, source|None, limit).
            out = []
            for r in sorted(self.entity_nodes.values(),
                            key=lambda r: (r["entity_type"], r["entity_uid"])):
                canonical = r["canonical"] if isinstance(r["canonical"], dict) else {}
                source = str(canonical.get("source") or "auto")
                if p[0] is not None and source != p[0]:
                    continue
                out.append({"entity_type": r["entity_type"], "entity_uid": r["entity_uid"],
                            "name": str(canonical.get("name") or r["entity_uid"]),
                            "source": source, "canonical": canonical})
            return out[: int(p[2])] if len(p) > 2 else out
        if sql.startswith("UPDATE node SET canonical"):
            # p = (canonical_json, node_id) — 표기(name)는 구현이 유지해서 넘겨야 한다.
            for row in self.entity_nodes.values():
                if row["node_id"] == str(p[1]):
                    row["canonical"] = json.loads(p[0])
            return []
        if "DELETE FROM graph_edge" in sql:
            src = self.asset_nodes.get(str(p[1]))
            gone = [eid for eid, e in self.edges.items()
                    if e["relation_kind_id"] == p[0] and e["src_node"] == src]
            for eid in gone:
                del self.edges[eid]
            return [{"edge_id": eid} for eid in gone]
        if "INSERT INTO graph_edge" in sql:
            key = (p[1], p[2], p[3])
            if any((e["src_node"], e["dst_node"], e["relation_kind_id"]) == key
                   for e in self.edges.values()):
                raise AssertionError(f"uq_graph_edge_kind 위반 — 중복 엣지 삽입: {key}")
            self.edges[str(p[0])] = {"edge_id": str(p[0]), "src_node": p[1], "dst_node": p[2],
                                     "relation_kind_id": p[3], "reason": p[4], "status": p[5]}
            return []
        if sql.startswith("SELECT en.entity_type, en.entity_uid, en.node_id"):
            # 설명 대상 조회 — p = (statuses, kind_code, min_bundle_size). 묶음 크기는 **같은 상태
            # 필터**로 센다(카드의 "확인된 N건"과 어긋나면 어느 쪽을 믿을지 모른다).
            if self.kind is None or self.kind["kind_code"] != p[1]:
                return []
            out = []
            for row in self.entity_nodes.values():
                size = sum(
                    1 for e in self.edges.values()
                    if e["dst_node"] == row["node_id"]
                    and e["relation_kind_id"] == self.kind["relation_kind_id"]
                    and e["status"] in p[0]
                )
                if size < int(p[2]):
                    continue
                canonical = row["canonical"] if isinstance(row["canonical"], dict) else {}
                out.append({"entity_type": row["entity_type"], "entity_uid": row["entity_uid"],
                            "node_id": row["node_id"],
                            "name": str(canonical.get("name") or row["entity_uid"]),
                            "canonical": canonical, "bundle_size": size})
            out.sort(key=lambda r: (-r["bundle_size"], r["entity_type"], r["entity_uid"]))
            return out
        if sql.startswith("SELECT a.modality, m.ext_meta"):
            # 설명 재료 조회 — p = (statuses, kind_code, entity_type, entity_uid) · asset_id 순.
            node = self.entity_nodes.get((p[2], p[3]))
            if node is None or self.kind is None or self.kind["kind_code"] != p[1]:
                return []
            rows = []
            for asset_id, node_id in sorted(self.asset_nodes.items()):
                linked = any(
                    e["src_node"] == node_id and e["dst_node"] == node["node_id"]
                    and e["status"] in p[0] for e in self.edges.values()
                )
                if not linked:
                    continue
                material = self.assets.get(asset_id, {})
                rows.append({"modality": material.get("modality"),
                             "summary": material.get("summary")})
            return rows
        if "INSERT INTO asset_lineage" in sql:
            self.lineage.append({"asset_id": p[1], "activity": p[2], "agent": p[3],
                                 "generated": json.loads(p[5]), "payload": json.loads(p[6])})
            return []
        return []

    # ── 조회 헬퍼(단정용) ───────────────────────────────────────────────────
    def sqls(self) -> list[str]:
        return [s for s, _ in self.log]


def _kind_row(*, is_symmetric: bool = False, status: str = "active") -> dict[str, Any]:
    """등록된 ``mm_member`` 카탈로그 행(더미).

    Args:
        is_symmetric: 대칭 플래그. 오등록 교정 검증에서 ``True`` 를 준다.
        status: 카탈로그 상태(``active``·``inactive``).

    Returns:
        ``_Conn(kind=...)`` 에 넣는 행 dict.
    """
    return {"relation_kind_id": _KIND_ID, "kind_code": MM_MEMBER_KIND_CODE,
            "kind_name_ko": "멀티모달 메타 소속", "description": "…",
            "is_symmetric": is_symmetric, "status": status}


def _ent(keyword: str, name: str, entity_type: str = "장소") -> ExtractedEntity:
    """판정 1건(더미).

    Args:
        keyword: 출처 키워드(reason ``kw=`` 로 실린다).
        name: 개체 대표 표기.
        entity_type: 닫힌 5종 중 하나.

    Returns:
        ``ExtractedEntity``.
    """
    return ExtractedEntity(keyword=keyword, name=name, entity_type=entity_type)


class TestEnsureMmMemberKind(unittest.TestCase):
    """``mm_member`` 카탈로그 행 보장 — 비대칭·active 명시 + 오등록 교정."""

    def test_registers_asymmetric_active(self) -> None:
        conn = _Conn()
        kid = ensure_mm_member_kind(conn)

        self.assertEqual(kid, _KIND_ID)
        self.assertIsNotNone(conn.kind)
        assert conn.kind is not None
        self.assertEqual(conn.kind["kind_code"], "mm_member")
        # 비대칭 — 자산→개체는 방향이 있다(대칭이면 캐논 정렬로 src/dst 가 뒤집혀 조회가 깨진다).
        self.assertIs(conn.kind["is_symmetric"], False)
        # active — graph_persist·조회가 active kind 만 다루기 때문(프롬프트 노출은 제외 상수가 막는다).
        self.assertEqual(conn.kind["status"], "active")

    def test_description_mentions_direction(self) -> None:
        conn = _Conn()
        ensure_mm_member_kind(conn)
        assert conn.kind is not None
        self.assertIn("멀티모달 메타 소속", conn.kind["kind_name_ko"])
        self.assertIn("비대칭", conn.kind["description"])

    def test_idempotent_second_call_keeps_row(self) -> None:
        conn = _Conn()
        first = ensure_mm_member_kind(conn)
        second = ensure_mm_member_kind(conn)
        self.assertEqual(first, second)
        # 충돌 처리는 SQL 이 한다(ON CONFLICT (kind_code)) — 사전 SELECT 로 갈라지지 않게.
        self.assertTrue(any("ON CONFLICT (kind_code)" in s for s in conn.sqls()))

    def test_repairs_misregistered_flags(self) -> None:
        # 누가 mm_member 를 대칭·inactive 로 만들어 뒀다면(손 SQL·구버전 코드) 교정한다 —
        # ensure_relation_kind_for_llm_proposal 의 ON CONFLICT 는 is_symmetric·status 를
        # 갱신하지 않으므로(plan 확인점 3), 그 결함을 이 전용 함수가 덮는다.
        conn = _Conn(kind=_kind_row(is_symmetric=True, status="inactive"))
        ensure_mm_member_kind(conn)
        assert conn.kind is not None
        self.assertIs(conn.kind["is_symmetric"], False)
        self.assertEqual(conn.kind["status"], "active")
        # 교정문은 **어긋난 행만** 건드리는 가드를 갖는다(정상 행에 쓰기 0).
        upd = [s for s in conn.sqls() if s.startswith("UPDATE relation_kind")]
        self.assertEqual(len(upd), 1)
        self.assertIn("IS DISTINCT FROM", upd[0])


class TestEnsureEntityNode(unittest.TestCase):
    """메타 노드 upsert — 부분 유니크 충돌 · 표기 1회 고정 · 어휘 검사."""

    def test_inserts_with_partial_unique_conflict_and_canonical_name(self) -> None:
        conn = _Conn()
        nid = ensure_entity_node(conn, "장소", "제주도")

        row = conn.entity_nodes[("장소", normalize_text_key("제주도"))]
        self.assertEqual(nid, row["node_id"])
        # PK 는 **앱 발급 UUIDv7**(헌법 6조 · PG17 에 uuidv7() 없음).
        self.assertEqual(uuid.UUID(nid).version, 7)
        self.assertEqual(row["canonical"], {"name": "제주도"})
        ins = [s for s in conn.sqls() if s.startswith("INSERT INTO node")]
        # uq_node_entity 는 **부분** 유니크 인덱스라 ON CONFLICT 에 WHERE 절이 필요하다.
        self.assertIn("ON CONFLICT (entity_type, entity_uid) WHERE node_kind = 'entity'", ins[0])

    def test_uid_is_normalized_key_not_raw_name(self) -> None:
        conn = _Conn()
        ensure_entity_node(conn, "인물", " 김 수녕 ")
        self.assertIn(("인물", "김수녕"), conn.entity_nodes)

    def test_existing_node_keeps_first_name(self) -> None:
        # 표기 1회 고정(spec §4) — 같은 키의 다른 표기가 나중에 와도 대표 표기는 안 바뀐다.
        conn = _Conn()
        first = ensure_entity_node(conn, "장소", "제주도")
        second = ensure_entity_node(conn, "장소", "제 주 도")
        self.assertEqual(first, second)
        row = conn.entity_nodes[("장소", "제주도")]
        self.assertEqual(row["canonical"], {"name": "제주도"})
        # 두 번째 호출은 SELECT 로 끝난다(UPDATE 없음 — 표기를 덮지 않는다).
        self.assertFalse(any(s.startswith("UPDATE node") for s in conn.sqls()))

    def test_same_uid_different_type_is_separate_node(self) -> None:
        # 동음이의 분리는 **의도**다(파리=장소 / 파리=인물). 유니크 키가 (type, uid) 라서 별개 행.
        conn = _Conn()
        a = ensure_entity_node(conn, "장소", "파리")
        b = ensure_entity_node(conn, "인물", "파리")
        self.assertNotEqual(a, b)
        self.assertEqual(len(conn.entity_nodes), 2)

    def test_concurrent_insert_reselects(self) -> None:
        conn = _Conn()
        existing = ensure_entity_node(conn, "장소", "서울특별시")
        # ON CONFLICT DO NOTHING 이 빈 RETURNING 을 주는 경합 경로: 같은 키를 다시 INSERT 시도해도
        # 재조회로 그 행을 찾아 돌려준다(가짜 DB 가 그 경합을 흉내낸다).
        conn.entity_nodes[("장소", "서울특별시")]["node_id"] = existing
        again = ensure_entity_node(conn, "장소", "서울특별시")
        self.assertEqual(again, existing)

    def test_rejects_type_outside_vocabulary(self) -> None:
        conn = _Conn()
        with self.assertRaises(MmMetaPersistError):
            ensure_entity_node(conn, "동물", "진돗개")
        self.assertEqual(conn.log, [])  # 검사는 쓰기보다 먼저 — 아무 것도 실행되지 않는다

    def test_rejects_empty_name(self) -> None:
        conn = _Conn()
        with self.assertRaises(MmMetaPersistError):
            ensure_entity_node(conn, "장소", "   ")
        self.assertEqual(conn.log, [])


class TestFetchOfficialNameIndex(unittest.TestCase):
    """등록된 메타에서 **공식 표기 색인**을 읽는다 — 배치 시작 1회(구현 확정 1·2).

    2026-08-24 개정: 옛 ``fetch_known_entity_names`` 는 "표기 키 → 대표 표기"라 **타입을 잃었고**,
    호출부가 값만 뽑아 자산마다 대응표를 다시 만들었다(결함 ①②). 이제 (타입, 몸통 표기 키) 색인을
    바로 돌려주고, 배치는 그 한 벌을 ``apply_rules(official_index=)`` 로 재사용한다.
    """

    def test_타입_스코프_색인을_돌려준다(self) -> None:
        conn = _Conn()
        ensure_entity_node(conn, "장소", "서울특별시")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "서울")], "서울특별시")

    def test_같은_몸통_다른_타입은_섞이지_않는다(self) -> None:
        # 🔴 결함 ① 의 원천 차단 — '경주시(장소)'가 '경주(사건)' 판정을 끌어가지 못한다.
        conn = _Conn()
        ensure_entity_node(conn, "장소", "경주시")
        index = fetch_official_name_index(conn)
        self.assertIn(("장소", "경주"), index)
        self.assertNotIn(("사건", "경주"), index)

    def test_접미가_없는_메타는_색인에_없다(self) -> None:
        # 병합의 목표가 될 수 없는 표기다(대표할 몸통이 없다) — 색인이 불필요하게 커지지 않는다.
        # 타입은 **장소**로 둔다: 타입 스코프(T018 결함 ③)가 아니라 "접미가 없다"는 이유로 빠지는
        # 것을 봐야 하므로, 병합 대상 타입에서 확인해야 뜻이 산다.
        conn = _Conn()
        ensure_entity_node(conn, "장소", "경복궁")
        self.assertEqual(dict(fetch_official_name_index(conn)), {})

    def test_장소가_아닌_메타는_색인에_없다(self) -> None:
        # 🔴 T018 결함 ③ — '리오넬 메시'(인물)·'영국 해군'(조직)·'한사군'(사건)의 끝 글자를 행정
        # 접미로 오인해 몸통 항목이 만들어져 있었다(dev 색인 실측).
        conn = _Conn()
        ensure_entity_node(conn, "인물", "리오넬 메시")
        ensure_entity_node(conn, "조직", "영국 해군")
        ensure_entity_node(conn, "사건", "한사군")
        self.assertEqual(dict(fetch_official_name_index(conn)), {})

    def test_한글자_몸통_메타는_색인에_없다(self) -> None:
        # 🔴 T018 결함 ② — ``(장소,'독')→'독도'`` 가 있으면 '독' 판정이 독도 묶음으로 빨려 들어간다.
        conn = _Conn()
        ensure_entity_node(conn, "장소", "독도")
        ensure_entity_node(conn, "장소", "인도")
        self.assertEqual(dict(fetch_official_name_index(conn)), {})

    def test_별칭도_같은_타입_스코프로_들어간다(self) -> None:
        # T013 수동 선등록의 ``canonical.aliases`` 도 후보다 — 등록 표기가 짧은 쪽('서울')이고
        # 공식형이 별칭('서울특별시')인 경우까지 합류시킨다.
        conn = _Conn()
        conn.entity_nodes[("장소", "서울")] = {
            "node_id": "n1", "entity_type": "장소", "entity_uid": "서울",
            "canonical": {"name": "서울", "aliases": ["서울특별시"], "source": "user"},
        }
        self.assertEqual(fetch_official_name_index(conn)[("장소", "서울")], "서울특별시")

    def test_별칭_공식형으로_합친_뒤_등록_표기로_되돌아온다(self) -> None:
        # 두 단계의 순서가 계약이다(persist 호출 순서 4): 접미 병합이 공식형으로 모으고, 별칭
        # 치환이 **등록 표기**로 되돌린다. 그래서 등록 메타(표기 '서울')의 묶음이 갈라지지 않는다.
        conn = _Conn()
        register_mm_meta(conn, "장소", "서울", aliases=["서울특별시"])
        merged = apply_rules([_ent("가키워드", "서울")],
                             official_index=fetch_official_name_index(conn))
        resolved = resolve_registered_aliases(merged, fetch_registered_alias_index(conn))
        self.assertEqual([entity.name for entity in resolved], ["서울"])

    def test_이름이_없는_행은_표기_키로_대신한다(self) -> None:
        # 손 SQL·구버전이 남긴 행 — 빈 라벨을 공식형으로 내보내지 않는다.
        conn = _Conn()
        conn.entity_nodes[("장소", "제주도")] = {
            "node_id": "n2", "entity_type": "장소", "entity_uid": "제주도", "canonical": {},
        }
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주도")

    def test_순수_빌더와_같은_계약이다(self) -> None:
        # 🔴 두 함수의 계약이 어긋나면 배치가 만드는 색인과 테스트가 보는 색인이 갈린다.
        conn = _Conn()
        ensure_entity_node(conn, "장소", "서울특별시")
        ensure_entity_node(conn, "사건", "경주시")
        self.assertEqual(
            dict(fetch_official_name_index(conn)),
            dict(build_official_name_index([("사건", "경주시"), ("장소", "서울특별시")])),
        )

    def test_조회는_쓰지_않는다(self) -> None:
        conn = _Conn()
        ensure_entity_node(conn, "장소", "서울특별시")
        before = dict(conn.entity_nodes[("장소", "서울특별시")]["canonical"])
        fetch_official_name_index(conn)
        self.assertEqual(conn.entity_nodes[("장소", "서울특별시")]["canonical"], before)


class TestOfficialNameIndexBundleSize(unittest.TestCase):
    """🔴 T018 결함 ① — 색인은 각 메타의 **묶음 크기**를 함께 읽어 큰 쪽을 공식형으로 고른다.

    무엇이 잘못됐었나(2026-08-24 dev 색인 실측): ``(장소,'제주')`` 의 공식형이 자산 **1건**짜리
    '제주특별자치도' 였고 알찬 묶음은 **13건**짜리 '제주도' 였다. 그러면 앞으로 '제주' 가 판정될
    때마다 1건짜리로 흘러가 묶음이 둘로 갈라진다. 색인의 목적이 "이미 자산이 붙은 곳으로 합류"
    (spec §구현 확정 2)이므로 크기가 첫 축이어야 한다.

    묶음 크기는 **소속 엣지의 distinct 자산 노드 수**이고, 세는 상태는 카드 조회와 같은
    ``MM_META_VISIBLE_STATUSES`` 다(다르면 카드가 "확인된 N건"이라 적는 수와 병합 판단의 근거가
    갈린다).
    """

    def _bind(self, conn: _Conn, asset_id: str, name: str, entity_type: str = "장소") -> None:
        """자산 하나를 메타에 붙인다(소속 엣지 1건 — 묶음 크기 재료).

        Args:
            conn: 가짜 커넥션.
            asset_id: 붙일 자산 id(더미).
            name: 메타 대표 표기.
            entity_type: 개체 타입(닫힌 5종). 기본 장소.
        """
        upsert_entity_edges(conn, asset_id, [_ent("가키워드", name, entity_type)])

    def test_자산이_많이_붙은_표기를_공식형으로_고른다(self) -> None:
        conn = _Conn(kind=_kind_row())
        self._bind(conn, _ASSET, "제주도")
        self._bind(conn, _ASSET2, "제주도")
        self._bind(conn, _ASSET3, "제주특별자치도")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주도")

    def test_묶음_0인_빈_메타도_후보다(self) -> None:
        # 수동 선등록만 된 메타(자산 0건)도 후보에 넣는다 — propose 모드에서는 그 등록이 곧 승인이고,
        # 첫 자산이 붙기 전에도 판정이 그쪽으로 합류해야 한다(크기 0 으로 겨룬다).
        conn = _Conn(kind=_kind_row())
        ensure_entity_node(conn, "장소", "제주특별자치도")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주특별자치도")

    def test_자산이_붙은_표기가_빈_메타를_이긴다(self) -> None:
        conn = _Conn(kind=_kind_row())
        ensure_entity_node(conn, "장소", "제주특별자치도")   # 0건
        self._bind(conn, _ASSET, "제주도")                   # 1건
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주도")

    def test_긴_접미도_묶음이_크면_이긴다(self) -> None:
        # 크기가 첫 축이라는 것뿐이다 — 접미 길이 자체를 벌주지 않는다(2건 > 1건).
        conn = _Conn(kind=_kind_row())
        self._bind(conn, _ASSET, "제주도")
        self._bind(conn, _ASSET2, "제주특별자치도")
        self._bind(conn, _ASSET3, "제주특별자치도")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주특별자치도")

    def test_같은_자산은_한_번만_센다(self) -> None:
        # distinct 자산 노드 — 재판정(자산 스코프 교체)이 돌아도 크기가 부풀지 않는다.
        conn = _Conn(kind=_kind_row())
        self._bind(conn, _ASSET, "제주도")
        self._bind(conn, _ASSET, "제주도")  # 같은 자산 재판정
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주도")
        # 세는 축이 자산(distinct src_node)이라는 것을 문장에서도 못 박는다 — 모의 DB 는 중복 엣지를
        # 애초에 못 만들므로(uq 위반) 값만으로는 이 계약이 드러나지 않는다.
        self.assertTrue(any("count(DISTINCT ge.src_node)" in sql for sql in conn.sqls()))

    def test_묶음_크기는_카드와_같은_상태를_센다(self) -> None:
        # 드리프트 가드 — 상태 집합·종류 코드가 카드 조회와 같은 값으로 바인딩된다.
        conn = _Conn(kind=_kind_row())
        self._bind(conn, _ASSET, "제주도")
        fetch_official_name_index(conn)
        params = [p for sql, p in conn.log if "count(DISTINCT" in sql]
        self.assertEqual(len(params), 1)          # 배치 시작 1회 — 개체마다 세지 않는다
        self.assertEqual(params[0][0], list(MM_META_VISIBLE_STATUSES))
        self.assertEqual(params[0][1], MM_MEMBER_KIND_CODE)

    def test_별칭_후보는_그_노드의_묶음_크기로_겨룬다(self) -> None:
        # '이 표기로 합류하면 그 노드의 묶음에 붙는다'가 크기의 뜻이다 — 등록 메타 '제주'(자산 2건)의
        # 별칭 '제주도' 는 2건으로 겨루고, 자산 1건짜리 '제주특별자치도'(긴 접미)를 이긴다.
        conn = _Conn(kind=_kind_row())
        register_mm_meta(conn, "장소", "제주", aliases=["제주도"])
        self._bind(conn, _ASSET, "제주")
        self._bind(conn, _ASSET2, "제주")
        self._bind(conn, _ASSET3, "제주특별자치도")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주도")

    def test_종류_미등록이면_크기_없이_옛_규칙으로_떨어진다(self) -> None:
        # 폴백 — ``mm_member`` 카탈로그 행이 없으면 크기 질의가 아무 행도 주지 않는다(전부 0 · 동점)
        # → 긴 접미 우선. 색인 조립이 예외로 멈추지 않는다.
        conn = _Conn()
        ensure_entity_node(conn, "장소", "제주도")
        ensure_entity_node(conn, "장소", "제주특별자치도")
        self.assertEqual(fetch_official_name_index(conn)[("장소", "제주")], "제주특별자치도")


class TestNormalizeRegistration(unittest.TestCase):
    """T013 수동 선등록의 **순수 검증·정규화**(DB 접속 전에 걸러야 하는 것들)."""

    def test_returns_normalized_payload(self) -> None:
        plan = normalize_registration("인물", " 아이유 ", aliases=["이지은", " IU "])
        self.assertEqual(plan["entity_type"], "인물")
        self.assertEqual(plan["name"], "아이유")          # 표시 표기는 strip 만(원문 보존)
        self.assertEqual(plan["entity_uid"], normalize_text_key("아이유"))
        self.assertEqual(plan["aliases"], ("이지은", "IU"))

    def test_rejects_type_outside_vocabulary(self) -> None:
        with self.assertRaises(MmMetaPersistError):
            normalize_registration("동물", "진돗개")

    def test_rejects_empty_name(self) -> None:
        with self.assertRaises(MmMetaPersistError):
            normalize_registration("장소", "   ")

    def test_drops_empty_and_duplicate_aliases(self) -> None:
        # 표기 키가 같은 별칭은 한 번만 — 저장 뒤 중복 별칭은 매칭에 아무 것도 더하지 않는다.
        plan = normalize_registration("인물", "아이유", aliases=["이지은", "", "  ", "이 지 은"])
        self.assertEqual(plan["aliases"], ("이지은",))

    def test_drops_alias_equal_to_name(self) -> None:
        # 대표 표기와 같은 별칭은 무의미하다(이미 표기 키로 잡힌다).
        plan = normalize_registration("장소", "제주도", aliases=["제 주 도", "탐라"])
        self.assertEqual(plan["aliases"], ("탐라",))


class TestRegisterMmMeta(unittest.TestCase):
    """T013 수동 선등록(spec §6-1) — 사용자 소유 메타를 만들고 표식을 남긴다."""

    def test_registers_user_meta_with_aliases(self) -> None:
        conn = _Conn()
        out = register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])

        self.assertEqual(out["action"], "registered")
        row = conn.entity_nodes[("인물", "아이유")]
        self.assertEqual(row["canonical"], {"name": "아이유", "source": MM_META_SOURCE_USER,
                                            "aliases": ["이지은"]})
        self.assertEqual(uuid.UUID(out["node_id"]).version, 7)

    def test_empty_meta_creates_no_edges(self) -> None:
        # 빈 메타(소속 0건) 허용 — 데이터가 들어오면 배치가 채운다(spec §6-1).
        conn = _Conn()
        register_mm_meta(conn, "장소", "울릉도")
        self.assertEqual(conn.edges, {})
        self.assertEqual(conn.entity_nodes[("장소", "울릉도")]["canonical"]["aliases"], [])

    def test_promotes_existing_auto_node_keeping_display_name(self) -> None:
        # 배치가 먼저 만든 노드(auto·표기 '아이유')에 사람이 별칭을 붙이는 경우.
        # 같은 메타의 기준은 **표기 키**다 — '아이 유'는 공백만 다르므로 그 노드에 붙는다.
        conn = _Conn()
        ensure_entity_node(conn, "인물", "아이유")
        out = register_mm_meta(conn, "인물", "아이 유", aliases=["이지은"])

        self.assertEqual(out["action"], "updated")
        canonical = conn.entity_nodes[("인물", "아이유")]["canonical"]
        # 표기 1회 고정(spec §4) — 등록이 대표 표기를 덮지 않는다.
        self.assertEqual(canonical["name"], "아이유")
        self.assertEqual(canonical["source"], MM_META_SOURCE_USER)  # source 승격만
        self.assertEqual(canonical["aliases"], ["이지은"])
        self.assertEqual(out["name"], "아이유")
        self.assertTrue(out["name_kept"])  # 요청 표기와 저장 표기가 다르다는 사실을 알린다

    def test_different_display_key_is_a_separate_meta(self) -> None:
        # 'IU' 는 표기 키가 달라(iu) 같은 메타가 아니다 — 같은 것으로 보려면 **별칭**으로 넣는다.
        # 이 경계를 흐리면 등록이 임의로 남의 메타를 삼킨다(표기 키가 유니크 축이다).
        conn = _Conn()
        ensure_entity_node(conn, "인물", "아이유")
        out = register_mm_meta(conn, "인물", "IU")

        self.assertEqual(out["action"], "registered")
        self.assertEqual(len(conn.entity_nodes), 2)
        self.assertIn(("인물", normalize_text_key("IU")), conn.entity_nodes)

    def test_merges_aliases_without_losing_existing(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        out = register_mm_meta(conn, "인물", "아이유", aliases=["IU", "이 지 은"])

        self.assertEqual(out["action"], "updated")
        self.assertEqual(conn.entity_nodes[("인물", "아이유")]["canonical"]["aliases"],
                         ["이지은", "IU"])

    def test_same_registration_twice_writes_nothing(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        before = len(conn.log)
        out = register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])

        self.assertEqual(out["action"], "unchanged")
        # 조회만 하고 끝난다 — 같은 내용을 다시 쓰면 updated_at 만 흔들린다.
        self.assertFalse(any(s.startswith("UPDATE node")
                             for s, _ in conn.log[before:]))

    def test_preserves_unknown_canonical_keys(self) -> None:
        # 나중에 다른 층이 canonical 에 넣은 값을 등록이 지우지 않는다.
        conn = _Conn()
        conn.entity_nodes[("장소", "제주도")] = {
            "node_id": "n9", "entity_type": "장소", "entity_uid": "제주도",
            "canonical": {"name": "제주도", "note": "보존"},
        }
        register_mm_meta(conn, "장소", "제주도", aliases=["탐라"])
        canonical = conn.entity_nodes[("장소", "제주도")]["canonical"]
        self.assertEqual(canonical["note"], "보존")
        self.assertEqual(canonical["source"], MM_META_SOURCE_USER)

    def test_rejects_bad_input_before_writing(self) -> None:
        conn = _Conn()
        with self.assertRaises(MmMetaPersistError):
            register_mm_meta(conn, "동물", "진돗개")
        with self.assertRaises(MmMetaPersistError):
            register_mm_meta(conn, "장소", "  ")
        self.assertEqual(conn.log, [])
        self.assertEqual(conn.entity_nodes, {})

    def test_same_name_different_type_is_separate_meta(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "장소", "파리")
        register_mm_meta(conn, "인물", "파리")
        self.assertEqual(len(conn.entity_nodes), 2)


class TestRegisteredAliasIndex(unittest.TestCase):
    """등록 메타 별칭 색인 — 판정 표기를 등록 메타로 잇는 **배치 1회 조회**."""

    def test_maps_alias_key_to_display_name_per_type(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        index = fetch_registered_alias_index(conn)
        self.assertEqual(index[("인물", "이지은")], "아이유")

    def test_includes_display_name_itself(self) -> None:
        # 표기 차이 흡수용 — '제 주 도' 판정도 등록 표기 '제주도' 로 맞춰진다.
        conn = _Conn()
        register_mm_meta(conn, "장소", "제주도")
        index = fetch_registered_alias_index(conn)
        self.assertEqual(index[("장소", "제주도")], "제주도")

    def test_alias_is_scoped_to_its_type(self) -> None:
        # 동음이의 분리 유지 — 인물 별칭이 장소 판정을 끌어가지 않는다.
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        index = fetch_registered_alias_index(conn)
        self.assertNotIn(("장소", "이지은"), index)

    def test_empty_when_no_meta(self) -> None:
        self.assertEqual(fetch_registered_alias_index(_Conn()), {})


class TestResolveRegisteredAliases(unittest.TestCase):
    """별칭 치환(순수) — 판정 표기를 등록 메타 대표 표기로 갈아 준다."""

    _INDEX = {("인물", "이지은"): "아이유", ("인물", "아이유"): "아이유"}

    def test_alias_judgement_becomes_registered_name(self) -> None:
        out = resolve_registered_aliases([_ent("이지은 노래", "이지은", "인물")], self._INDEX)
        self.assertEqual([e.name for e in out], ["아이유"])
        self.assertEqual(out[0].keyword, "이지은 노래")  # 근거 키워드는 원문 보존

    def test_other_type_is_untouched(self) -> None:
        out = resolve_registered_aliases([_ent("이지은", "이지은", "작품")], self._INDEX)
        self.assertEqual([e.name for e in out], ["이지은"])

    def test_two_aliases_collapse_into_one(self) -> None:
        out = resolve_registered_aliases(
            [_ent("아이유 콘서트", "아이유", "인물"), _ent("이지은 인터뷰", "이지은", "인물")],
            self._INDEX)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].keyword, "아이유 콘서트")  # 첫 판정의 키워드가 대표

    def test_empty_index_keeps_input(self) -> None:
        entities = [_ent("제주 장마", "제주도"), _ent("김수녕", "김수녕", "인물")]
        out = resolve_registered_aliases(entities, {})
        self.assertEqual([e.name for e in out], ["제주도", "김수녕"])

    def test_input_sequence_is_not_mutated(self) -> None:
        entities = [_ent("이지은 노래", "이지은", "인물")]
        resolve_registered_aliases(entities, self._INDEX)
        self.assertEqual(entities[0].name, "이지은")


class TestRegisteredMetaBinding(unittest.TestCase):
    """🔴 실제 매칭 배선 — 등록 메타에 **별칭 판정이 붙는다**(spec §6-1 목적)."""

    def test_alias_judgement_attaches_to_registered_meta(self) -> None:
        conn = _Conn(kind=_kind_row())
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])

        index = fetch_registered_alias_index(conn)
        entities = resolve_registered_aliases([_ent("이지은 인터뷰", "이지은", "인물")], index)
        upsert_entity_edges(conn, _ASSET, entities)

        # 별칭 이름의 새 노드가 생기지 않고, 엣지는 등록 메타 노드를 가리킨다.
        self.assertNotIn(("인물", "이지은"), conn.entity_nodes)
        edge = next(iter(conn.edges.values()))
        self.assertEqual(edge["dst_node"], conn.entity_nodes[("인물", "아이유")]["node_id"])

    def test_without_index_alias_makes_separate_meta(self) -> None:
        # 색인을 안 넘기면(배선 누락) 별칭이 별개 메타가 된다 — 위 테스트가 무엇을 지키는지의 대조군.
        conn = _Conn(kind=_kind_row())
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        upsert_entity_edges(conn, _ASSET, [_ent("이지은 인터뷰", "이지은", "인물")])
        self.assertIn(("인물", "이지은"), conn.entity_nodes)


class TestListMmMeta(unittest.TestCase):
    """등록 목록 조회 — 등록 CLI 의 "지금 무엇이 등록돼 있나"."""

    def test_lists_with_source_and_aliases(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유", aliases=["이지은"])
        ensure_entity_node(conn, "장소", "제주도")  # 배치 발굴분(auto)

        rows = list_mm_meta(conn)
        self.assertEqual([(r["entity_type"], r["name"], r["source"]) for r in rows],
                         [("인물", "아이유", MM_META_SOURCE_USER),
                          ("장소", "제주도", MM_META_SOURCE_AUTO)])
        self.assertEqual(rows[0]["aliases"], ["이지은"])

    def test_filters_by_source(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유")
        ensure_entity_node(conn, "장소", "제주도")
        rows = list_mm_meta(conn, source=MM_META_SOURCE_USER)
        self.assertEqual([r["name"] for r in rows], ["아이유"])

    def test_limit_is_bound(self) -> None:
        conn = _Conn()
        register_mm_meta(conn, "인물", "아이유")
        register_mm_meta(conn, "장소", "제주도")
        self.assertEqual(len(list_mm_meta(conn, limit=1)), 1)


class TestReasonStamp(unittest.TestCase):
    """``kw=…|pv=…|rv=…`` — 배치 재선별이 읽는 스탬프(구현 확정 3·8)."""

    def test_format_is_fixed(self) -> None:
        self.assertEqual(
            format_member_reason(keyword="제주 장마", prompt_version="mm_meta.v1", rule_version=1),
            "kw=제주 장마|pv=mm_meta.v1|rv=1",
        )

    def test_roundtrip(self) -> None:
        stamp = format_member_reason(keyword="올림픽 금메달", prompt_version=PROMPT_VERSION,
                                     rule_version=RULE_VERSION)
        parsed = parse_member_reason(stamp)
        self.assertEqual(parsed, {"keyword": "올림픽 금메달",
                                  "prompt_version": PROMPT_VERSION,
                                  "rule_version": RULE_VERSION})

    def test_keyword_containing_separator_survives(self) -> None:
        # 키워드에 구분자가 섞여도 뒤에서 두 칸만 떼면 원문이 보존된다(오른쪽 파싱).
        parsed = parse_member_reason("kw=a|b=c|pv=mm_meta.v1|rv=2")
        assert parsed is not None
        self.assertEqual(parsed["keyword"], "a|b=c")
        self.assertEqual(parsed["rule_version"], 2)

    def test_rule_version_is_int_for_ordering(self) -> None:
        parsed = parse_member_reason("kw=x|pv=mm_meta.v1|rv=10")
        assert parsed is not None
        # 순서 비교(이력<현행)를 하므로 정수여야 한다 — 문자열이면 "10"<"9" 가 참이 된다.
        self.assertIsInstance(parsed["rule_version"], int)

    def test_unparseable_returns_none(self) -> None:
        for bad in [None, "", "관계 유사도 0.9", "kw=x|pv=mm_meta.v1", "kw=x|pv=v1|rv=일",
                    "kw=x|pv=|rv=1"]:
            self.assertIsNone(parse_member_reason(bad), msg=bad)


class TestUpsertEntityEdges(unittest.TestCase):
    """자산→메타 엣지 영속 — 스코프 교체·멱등·proposed·계보."""

    def test_inserts_proposed_edges_with_stamp(self) -> None:
        conn = _Conn(kind=_kind_row())
        out = upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도"),
                                                 _ent("김수녕", "김수녕", "인물")])

        self.assertEqual(out["edges_inserted"], 2)
        self.assertEqual(out["edges_deleted"], 0)
        self.assertEqual(len(conn.edges), 2)
        for edge in conn.edges.values():
            self.assertEqual(edge["status"], "proposed")   # 전건 미검토(승격은 사람)
            self.assertIsNotNone(parse_member_reason(edge["reason"]))
        ins = [s for s in conn.sqls() if s.startswith("INSERT INTO graph_edge")]
        self.assertEqual(len(ins), 2)
        # confidence 는 미산정 → **NULL 을 명시**한다. 0.0 을 넣으면 "가장 약한 관계"로 읽히고
        # GREATEST 화해·정렬에 끼어든다(spec §4).
        self.assertIn("NULL", ins[0])

    def test_edge_direction_is_asset_to_entity(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        asset_node = conn.asset_nodes[_ASSET]
        entity_node = conn.entity_nodes[("장소", "제주도")]["node_id"]
        edge = next(iter(conn.edges.values()))
        self.assertEqual(edge["src_node"], asset_node)
        self.assertEqual(edge["dst_node"], entity_node)

    def test_idempotent_same_judgement_twice(self) -> None:
        conn = _Conn(kind=_kind_row())
        entities = [_ent("제주 장마", "제주도"), _ent("김수녕", "김수녕", "인물")]
        upsert_entity_edges(conn, _ASSET, entities)
        second = upsert_entity_edges(conn, _ASSET, entities)

        self.assertEqual(len(conn.edges), 2)          # 증가 0(spec §8 멱등)
        self.assertEqual(second["edges_deleted"], 2)  # 교체이므로 지웠다가 다시 넣는다
        self.assertEqual(second["edges_inserted"], 2)
        self.assertEqual(len(conn.entity_nodes), 2)   # 노드도 늘지 않는다

    def test_asset_scope_replacement_drops_stale_edges(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("대한민국 여행", "대한민국"),
                                           _ent("제주 장마", "제주도")])
        # 규칙 강화(광역 제외)로 이제 '대한민국' 은 판정에서 빠진다 → 재판정 후 그 엣지는 남지 않는다.
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])

        dsts = {e["dst_node"] for e in conn.edges.values()}
        self.assertEqual(len(dsts), 1)
        self.assertEqual(dsts, {conn.entity_nodes[("장소", "제주도")]["node_id"]})
        # 고아 노드('대한민국')는 남는다 — 노드 삭제는 배치 리포트(고아 목록)의 몫이다.
        self.assertIn(("장소", "대한민국"), conn.entity_nodes)

    def test_replacement_is_scoped_to_kind_and_asset(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        upsert_entity_edges(conn, _ASSET2, [_ent("제주 여행", "제주도")])
        # 다른 자산 재판정이 이 자산의 엣지를 지우지 않는다.
        upsert_entity_edges(conn, _ASSET2, [_ent("제주 여행", "제주도")])
        self.assertEqual(len(conn.edges), 2)

        delete_sqls = [s for s in conn.sqls() if s.startswith("DELETE FROM graph_edge")]
        self.assertTrue(delete_sqls)
        for sql in delete_sqls:
            # 종류·자산 둘 다로 좁힌다 — 종류를 빼면 자산↔자산 관계 엣지가 함께 지워진다.
            self.assertIn("relation_kind_id = %s", sql)
            self.assertIn("node_kind = 'asset' AND asset_id = %s", sql)

    def test_empty_entities_still_replaces_and_records_lineage(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        out = upsert_entity_edges(conn, _ASSET, [])

        self.assertEqual(out["edges_deleted"], 1)
        self.assertEqual(out["edges_inserted"], 0)
        self.assertEqual(conn.edges, {})
        # 성공·개체 0 도 **이력을 남긴다**(그래야 다음 배치가 이 자산을 또 판정하지 않는다).
        self.assertEqual(len(conn.lineage), 2)
        self.assertEqual(conn.lineage[-1]["activity"], LINEAGE_ACTIVITY)

    def test_empty_entities_does_not_create_asset_node(self) -> None:
        # 엣지가 없을 때 노드를 만들면 엣지 0 인 고아 asset 노드가 남는다(graph_persist 가 지키는 순서).
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [])
        self.assertEqual(conn.asset_nodes, {})

    def test_lineage_records_versions_and_entities(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        rec = conn.lineage[-1]
        self.assertEqual(rec["activity"], LINEAGE_ACTIVITY)
        self.assertEqual(uuid.UUID(str(rec["asset_id"])), uuid.UUID(_ASSET))
        self.assertEqual(rec["payload"]["prompt_version"], PROMPT_VERSION)
        self.assertEqual(rec["payload"]["rule_version"], RULE_VERSION)
        self.assertEqual(rec["generated"]["edges_inserted"], 1)
        self.assertEqual(rec["generated"]["entities"][0]["entity_uid"], "제주도")

    def test_version_stamps_are_overridable(self) -> None:
        conn = _Conn(kind=_kind_row())
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")],
                            prompt_version="mm_meta.v9", rule_version=7)
        edge = next(iter(conn.edges.values()))
        self.assertEqual(parse_member_reason(edge["reason"]),
                         {"keyword": "제주 장마", "prompt_version": "mm_meta.v9",
                          "rule_version": 7})

    def test_writes_happen_in_one_transaction(self) -> None:
        # 엣지만 지워지고 계보가 안 남는 반쪽 상태를 만들지 않는다(전부 아니면 전무).
        conn = _Conn(kind=_kind_row())

        depths: list[int] = []
        original = conn.dispatch

        def spy(sql: str, params: Any) -> list[dict[str, Any]]:
            depths.append(conn.tx_depth)
            return original(sql, params)

        conn.dispatch = spy  # type: ignore[method-assign]
        upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        self.assertTrue(all(d >= 1 for d in depths[1:]), depths)

    def test_missing_kind_raises_without_writing(self) -> None:
        conn = _Conn()  # mm_member 미등록
        with self.assertRaises(MmMetaPersistError):
            upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])
        self.assertEqual(conn.edges, {})
        self.assertEqual(conn.entity_nodes, {})
        self.assertEqual(conn.lineage, [])

    def test_inactive_kind_raises(self) -> None:
        conn = _Conn(kind=_kind_row(status="inactive"))
        with self.assertRaises(MmMetaPersistError):
            upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도")])

    def test_duplicate_entities_rejected_before_writing(self) -> None:
        # 같은 (타입, 표기 키) 두 건은 uq_graph_edge_kind 위반이 된다 — 쓰기 전에 막는다.
        conn = _Conn(kind=_kind_row())
        with self.assertRaises(MmMetaPersistError):
            upsert_entity_edges(conn, _ASSET, [_ent("제주 장마", "제주도"),
                                               _ent("제주 여행", "제 주 도")])
        self.assertEqual(conn.edges, {})
        self.assertEqual(conn.lineage, [])


# ── T015 메타 설명(spec §8-1) ───────────────────────────────────────────────────
def _seed_member(
    conn: _Conn,
    asset_id: str,
    entity: ExtractedEntity,
    *,
    modality: str | None = "image",
    summary: str | None = "가 오름 전경 사진",
) -> None:
    """자산 하나를 메타에 소속시키고 그 자산의 재료(모달리티·요약)를 심는다.

    상태를 **실제 코드 경로**(``upsert_entity_edges``)로 만든다 — 가짜 DB 에 직접 행을 꽂으면
    "조회가 저장 결과를 읽는다"는 것을 검증하지 못한다.

    Args:
        conn: 가짜 커넥션(``mm_member`` 카탈로그가 등록돼 있어야 한다).
        asset_id: 소속시킬 자산(더미 UUID).
        entity: 그 자산이 가리키는 개체 1건.
        modality: 자산 모달리티. ``None`` 이면 모달리티 미상 자산(빈 STT 등)을 흉내낸다.
        summary: 자산 요약(설명의 유일한 재료). ``None`` 이면 **메타데이터 없는 자산** —
            ``asset`` 행은 있는데 요약이 없는 경우다.
    """
    upsert_entity_edges(conn, asset_id, [entity])
    conn.assets[asset_id] = {"modality": modality, "summary": summary}


def _dummy_asset(index: int) -> str:
    """순번으로 더미 자산 id 를 만든다(UUIDv7 형태 · 실 자산 id 금지).

    묶음 크기 13~17 처럼 **여러 건**이 필요한 검증(설명 재생성 임계)에서 상수를 열 개 넘게 늘어놓지
    않으려고 둔다. 마지막 그룹만 순번으로 채우므로 값은 결정적이고 서로 겹치지 않는다.

    Args:
        index: 자산 순번(0 이상). 12자리로 채워 넣는다.

    Returns:
        UUIDv7 형태의 더미 자산 id 문자열.
    """
    return f"018f0000-0000-7000-8000-{index:012d}"


class TestFetchMetaMembers(unittest.TestCase):
    """설명 재료 조회 — ``(모달리티, 요약)`` 목록·**asset_id 순 결정적**.

    왜 이 모양인가: ``describe.describe_meta`` 의 입력 계약이 그대로 이것이라 호출부에 변환 코드가
    필요 없다. asset_id 를 함께 돌려주지 않는 이유도 같다 — 설명의 재료가 아니고, 구성 자산 목록은
    묶음 조회(``graph_query.mm_meta_bundle``)가 이미 준다(같은 숫자를 두 곳에서 세지 않는다).
    """

    def test_모달리티와_요약을_asset_id_순으로_돌려준다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET2, _ent("제주 장마", "제주도"),
                     modality="text", summary="나 기후 요약")
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"),
                     modality="image", summary="가 오름 요약")

        members = fetch_meta_members(conn, "장소", "제주도")
        # 순서는 asset_id 오름차순 — 심은 순서가 아니다(같은 묶음이면 늘 같은 프롬프트가 나온다).
        self.assertEqual(members, [("image", "가 오름 요약"), ("text", "나 기후 요약")])

    def test_요약이_없는_자산도_행으로_돌려준다(self) -> None:
        # 메타데이터가 없는 자산을 INNER JOIN 으로 떨구면 "왜 구성원 수가 카드와 다른가"가 된다 —
        # 빈 요약으로 돌려주고 버리는 판단은 문안 조립(describe)이 한다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"), summary=None)
        self.assertEqual(fetch_meta_members(conn, "장소", "제주도"), [("image", "")])

    def test_모달리티가_없으면_빈_문자열이다(self) -> None:
        # ``None`` 을 그대로 흘리면 문안에 "None" 이 글자로 박힌다 — 문자열로 눌러 담는다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"), modality=None)
        self.assertEqual(fetch_meta_members(conn, "장소", "제주도")[0][0], "")

    def test_요약을_상한으로_자른다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"), summary="가" * 400)

        self.assertEqual(len(fetch_meta_members(conn, "장소", "제주도")[0][1]), 150)
        self.assertEqual(
            len(fetch_meta_members(conn, "장소", "제주도", summary_max_chars=50)[0][1]), 50)

    def test_상한_0이하는_거부한다(self) -> None:
        # 0 이면 재료 없는 프롬프트가 나가고 설명이 통째로 환각이 된다 → fail-fast.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        for bad in (0, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(MmMetaPersistError):
                    fetch_meta_members(conn, "장소", "제주도", summary_max_chars=bad)

    def test_원표기로_물어도_찾는다(self) -> None:
        # URL·리포트로 오는 값의 표기 차이를 흡수한다(``mm_meta_bundle`` 과 같은 규칙).
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        self.assertEqual(len(fetch_meta_members(conn, "장소", " 제 주 도 ")), 1)

    def test_없는_메타는_빈_목록이다(self) -> None:
        conn = _Conn(kind=_kind_row())
        self.assertEqual(fetch_meta_members(conn, "장소", "제주도"), [])

    def test_노출_대상_밖_상태는_제외한다(self) -> None:
        # 소속이 취소된(다른 상태) 엣지가 재료에 섞이면 설명이 없는 자산을 말하게 된다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        for edge in conn.edges.values():
            edge["status"] = "rejected"
        self.assertEqual(fetch_meta_members(conn, "장소", "제주도"), [])

    def test_문안_조립에_그대로_넘길_수_있다(self) -> None:
        # 🔴 두 층의 계약이 맞물리는 지점 — 변환 코드 없이 그대로 흘러가야 한다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"), summary="가 오름 요약")
        prompt = build_description_prompt("제주도", "장소", fetch_meta_members(conn, "장소", "제주도"))
        self.assertIn("- (image) 가 오름 요약", prompt)
        self.assertIn("자산 1건의 요약이다", prompt)


class TestUpsertMetaDescription(unittest.TestCase):
    """설명 저장 — ``node.canonical`` **병합**(마이그레이션 0 · 기존 키 보존).

    왜 병합인가: ``canonical`` 한 칸에 대표 표기(``name``)·출처(``source``)·별칭(``aliases``)이 이미
    살고 있다. 통째로 덮으면 **사용자가 등록한 이름과 별칭이 설명 저장으로 지워진다** — 사람 소유
    필드는 배치가 건드리지 않는다는 규율(spec §6-1)이 여기서도 지켜져야 한다.
    """

    def test_설명과_스탬프_두_개를_저장한다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))

        out = upsert_meta_description(conn, "장소", "제주도", description="가 오름 전경과 기후 자료",
                                      member_count=1, prompt_version=DESC_PROMPT_VERSION)

        canonical = conn.entity_nodes[("장소", "제주도")]["canonical"]
        self.assertEqual(canonical["description"], "가 오름 전경과 기후 자료")
        # 🔴 건수·문안 판이 함께 저장돼야 재생성 대상 판정이 성립한다(둘 중 하나만 있으면 낡음을 못 본다).
        self.assertEqual(canonical["desc_member_count"], 1)
        self.assertEqual(canonical["desc_prompt_version"], DESC_PROMPT_VERSION)
        self.assertEqual(out["action"], "updated")

    def test_기존_canonical_키를_보존한다(self) -> None:
        conn = _Conn(kind=_kind_row())
        register_mm_meta(conn, "인물", "가수가", aliases=["가수나"])
        _seed_member(conn, _ASSET, _ent("가수가", "가수가", "인물"))

        upsert_meta_description(conn, "인물", "가수가", description="가 가수 공연 자료",
                                member_count=1, prompt_version=DESC_PROMPT_VERSION)

        canonical = conn.entity_nodes[("인물", "가수가")]["canonical"]
        self.assertEqual(canonical["name"], "가수가")            # 대표 표기 불변(spec §4)
        self.assertEqual(canonical["source"], MM_META_SOURCE_USER)  # 등록 표식 보존
        self.assertEqual(canonical["aliases"], ["가수나"])        # 사람 소유 필드 보존
        self.assertEqual(canonical["description"], "가 가수 공연 자료")

    def test_같은_값이면_쓰지_않는다(self) -> None:
        # 멱등 — 같은 내용을 다시 쓰면 얻는 것 없이 행만 흔든다(``register_mm_meta`` 와 같은 규율).
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=1, prompt_version=DESC_PROMPT_VERSION)
        before = [s for s in conn.sqls() if s.startswith("UPDATE node SET canonical")]

        out = upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                      member_count=1, prompt_version=DESC_PROMPT_VERSION)

        self.assertEqual(out["action"], "unchanged")
        after = [s for s in conn.sqls() if s.startswith("UPDATE node SET canonical")]
        self.assertEqual(len(after), len(before))  # 쓰기 0

    def test_설명을_교체할_수_있다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        upsert_meta_description(conn, "장소", "제주도", description="옛 요약",
                                member_count=1, prompt_version=DESC_PROMPT_VERSION)
        out = upsert_meta_description(conn, "장소", "제주도", description="새 요약",
                                      member_count=2, prompt_version=DESC_PROMPT_VERSION)

        self.assertEqual(out["action"], "updated")
        canonical = conn.entity_nodes[("장소", "제주도")]["canonical"]
        self.assertEqual(canonical["description"], "새 요약")
        self.assertEqual(canonical["desc_member_count"], 2)

    def test_원표기로_저장해도_같은_메타다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        upsert_meta_description(conn, "장소", " 제 주 도 ", description="가 요약",
                                member_count=1, prompt_version=DESC_PROMPT_VERSION)
        self.assertIn("description", conn.entity_nodes[("장소", "제주도")]["canonical"])

    def test_없는_메타는_거부한다(self) -> None:
        # 침묵 금지(계약 ⑤) — 조용히 0건을 쓰면 "설명이 왜 안 붙나"를 매번 다시 조사한다.
        conn = _Conn(kind=_kind_row())
        with self.assertRaises(MmMetaPersistError):
            upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                    member_count=1, prompt_version=DESC_PROMPT_VERSION)

    def test_빈_설명은_거부한다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        for bad in ("", "   ", None):
            with self.subTest(bad=bad):
                with self.assertRaises(MmMetaPersistError):
                    upsert_meta_description(conn, "장소", "제주도", description=bad,  # type: ignore[arg-type]
                                            member_count=1,
                                            prompt_version=DESC_PROMPT_VERSION)

    def test_건수가_1미만이면_거부한다(self) -> None:
        # 0 을 저장하면 낡음 판정(건수 불일치)이 **영원히 참**이 되어 매 배치가 다시 만든다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        for bad in (0, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(MmMetaPersistError):
                    upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                            member_count=bad,
                                            prompt_version=DESC_PROMPT_VERSION)

    def test_문안_판이_비면_거부한다(self) -> None:
        # 빈 판을 저장하면 현행 판과 절대 같아지지 않아 무한 재생성(LLM 비용)이 된다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        with self.assertRaises(MmMetaPersistError):
            upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                    member_count=1, prompt_version="  ")

    def test_쓰기_전에_막는다(self) -> None:
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        with self.assertRaises(MmMetaPersistError):
            upsert_meta_description(conn, "장소", "제주도", description="  ",
                                    member_count=1, prompt_version=DESC_PROMPT_VERSION)
        self.assertNotIn("description", conn.entity_nodes[("장소", "제주도")]["canonical"])


class TestFetchMetaDescriptionTargets(unittest.TestCase):
    """설명이 **없거나 낡은** 메타 선별 — 배치가 스스로 대상을 고르는 근거(spec §8-1).

    낡음의 뜻 둘: ①구성이 바뀌었다(``desc_member_count`` ≠ 현재 묶음 크기 — 자산이 붙거나 빠졌으면
    설명도 낡는다) ②문안이 바뀌었다(``desc_prompt_version`` ≠ 현행). 둘 다 **결정적 판정**이라
    사람이 목록을 관리할 필요가 없다.

    묶음 크기 1 은 대상 밖이다 — 카드가 노출하지 않으므로(spec §7 리뷰 지점 ②) 설명을 만들어도
    쓰이지 않는다. LLM 호출은 공짜가 아니다.
    """

    def _conn_with_bundle(self, *, size: int = 2) -> _Conn:
        """제주도 메타에 ``size`` 건이 소속된 가짜 DB 를 만든다.

        Args:
            size: 소속 자산 수(1~3).

        Returns:
            준비된 ``_Conn``.
        """
        conn = _Conn(kind=_kind_row())
        for asset_id in (_ASSET, _ASSET2, _ASSET3)[:size]:
            _seed_member(conn, asset_id, _ent("제주 오름", "제주도"))
        return conn

    def test_설명이_없으면_대상이다(self) -> None:
        conn = self._conn_with_bundle()
        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["entity_type"], "장소")
        self.assertEqual(targets[0]["entity_uid"], "제주도")
        self.assertEqual(targets[0]["name"], "제주도")
        self.assertEqual(targets[0]["bundle_size"], 2)
        self.assertEqual(targets[0]["reason"], "missing")

    def test_설명이_최신이면_대상이_아니다(self) -> None:
        conn = self._conn_with_bundle()
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=2, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual(fetch_meta_description_targets(conn,
                                                        prompt_version=DESC_PROMPT_VERSION), [])

    def test_묶음_크기가_바뀌면_대상이다(self) -> None:
        conn = self._conn_with_bundle()
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=2, prompt_version=DESC_PROMPT_VERSION)
        _seed_member(conn, _ASSET3, _ent("제주 오름", "제주도"))  # 자산 1건 추가 → 설명이 낡는다

        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["bundle_size"], 3)
        self.assertEqual(targets[0]["reason"], "member_count")

    def test_문안_판이_바뀌면_대상이다(self) -> None:
        conn = self._conn_with_bundle()
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=2, prompt_version="mm_meta.desc.v0")

        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["reason"], "prompt_version")

    def test_건수가_정수가_아니면_대상이다(self) -> None:
        # 손 SQL·구버전이 남긴 값으로 질의가 터지지 않고, 낡음으로 보아 다시 만든다(보수적 폴백).
        conn = self._conn_with_bundle()
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=2, prompt_version=DESC_PROMPT_VERSION)
        conn.entity_nodes[("장소", "제주도")]["canonical"]["desc_member_count"] = "둘"

        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual(targets[0]["reason"], "member_count")

    def test_묶음_1건은_대상_밖이다(self) -> None:
        conn = self._conn_with_bundle(size=1)
        self.assertEqual(fetch_meta_description_targets(conn,
                                                        prompt_version=DESC_PROMPT_VERSION), [])
        # 하한을 낮추면 잡힌다 — 정책은 인자이고 조회에 박아 넣지 않는다.
        self.assertEqual(len(fetch_meta_description_targets(
            conn, prompt_version=DESC_PROMPT_VERSION, min_bundle_size=1)), 1)

    def test_노출_대상_밖_상태는_묶음_크기에서_빠진다(self) -> None:
        # 카드가 세는 방식(active+proposed)과 같아야 한다 — 어긋나면 "확인된 2건"인데 설명은
        # 3건 기준으로 만들어진 상태가 된다.
        conn = self._conn_with_bundle(size=3)
        for edge in list(conn.edges.values())[:2]:
            edge["status"] = "rejected"
        self.assertEqual(fetch_meta_description_targets(conn,
                                                        prompt_version=DESC_PROMPT_VERSION), [])

    def test_큰_묶음이_먼저_온다(self) -> None:
        # 눈에 많이 띄는 카드부터 채운다(제주도 3건 · 경복궁 2건). ⚠️ 표기 키 순이면 '경복궁' 이
        # 앞이므로, 이 단정은 정렬 1순위가 **묶음 크기**임을 못 박는다.
        conn = _Conn(kind=_kind_row())
        _seed_member(conn, _ASSET, _ent("제주 오름", "제주도"))
        for asset_id in (_ASSET2, _ASSET3):
            upsert_entity_edges(conn, asset_id, [_ent("제주 오름", "제주도"),
                                                 _ent("경복궁 야간", "경복궁")])
            conn.assets[asset_id] = {"modality": "image", "summary": "가 요약"}

        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual([t["entity_uid"] for t in targets], ["제주도", "경복궁"])
        self.assertEqual([t["bundle_size"] for t in targets], [3, 2])

    def test_이전_설명도_함께_돌려준다(self) -> None:
        # 배치 diff 리포트가 "무엇이 무엇으로 바뀌었나"를 보이려면 옛 값이 필요하다.
        conn = self._conn_with_bundle()
        upsert_meta_description(conn, "장소", "제주도", description="옛 요약",
                                member_count=9, prompt_version=DESC_PROMPT_VERSION)
        targets = fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        self.assertEqual(targets[0]["description"], "옛 요약")

    def test_현행_문안_판이_비면_거부한다(self) -> None:
        # 빈 판을 기준으로 삼으면 저장된 판과 절대 같아지지 않아 **전량**이 대상이 된다 —
        # 조용히 LLM 비용만 늘어나는 종류의 결함이라 fail-fast 한다.
        conn = self._conn_with_bundle()
        for bad in ("", "   "):
            with self.subTest(bad=bad):
                with self.assertRaises(MmMetaPersistError):
                    fetch_meta_description_targets(conn, prompt_version=bad)

    def test_대상_사유_어휘가_닫혀있다(self) -> None:
        self.assertEqual(set(DESC_TARGET_REASONS), {"missing", "member_count", "prompt_version"})

    def test_소속이_없는_메타는_대상이_아니다(self) -> None:
        # 수동 선등록한 빈 메타(spec §6-1) — 재료가 0 이라 설명을 만들 수 없다.
        conn = _Conn(kind=_kind_row())
        register_mm_meta(conn, "인물", "가수가")
        self.assertEqual(fetch_meta_description_targets(conn,
                                                        prompt_version=DESC_PROMPT_VERSION), [])

    def test_조회는_쓰지_않는다(self) -> None:
        conn = self._conn_with_bundle()
        before = dict(conn.entity_nodes[("장소", "제주도")]["canonical"])
        fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)
        fetch_meta_members(conn, "장소", "제주도")
        self.assertEqual(conn.entity_nodes[("장소", "제주도")]["canonical"], before)


class TestDescriptionRegenThreshold(unittest.TestCase):
    """구성 변화 **비율 임계**(2026-08-24 결함 ③ 수정) — 인기 개체를 매번 다시 만들지 않는다.

    무엇이 문제였나: 낡음 판정이 ``desc_member_count`` **≠** 현재 묶음 크기였다. 그러면 인기 개체
    (제주도 13→14→15…)는 자산 1건이 붙을 때마다 설명이 재생성된다 — 대량 적재에서 같은 메타에
    LLM 을 수십 번 부른다. 비유하면 책 소개문이다: 시리즈에 한 권이 더 나올 때마다 소개문을 다시
    쓰지 않고, 구성이 눈에 띄게 달라졌을 때 고친다.

    임계는 **저장 시점 건수 대비 변화 비율**이다(분모 = 그 설명을 만든 건수). 그래서 임계 미만으로
    조금씩 늘어나면 재생성이 미뤄지고, 누적 변화가 임계를 넘는 순간 한 번 다시 만든다.

    🔴 임계 무관 대상 둘은 그대로다: 설명 부재(``missing``)·문안 판 변경(``prompt_version``).
    """

    def _bundle(self, size: int) -> _Conn:
        """제주도 메타에 ``size`` 건이 소속된 가짜 DB 를 만든다.

        Args:
            size: 소속 자산 수(1 이상). 자산 id 는 순번 더미다.

        Returns:
            준비된 ``_Conn``.
        """
        conn = _Conn(kind=_kind_row())
        for index in range(size):
            _seed_member(conn, _dummy_asset(index), _ent("제주 오름", "제주도"))
        return conn

    def _describe(self, conn: _Conn, member_count: int,
                  *, prompt_version: str = DESC_PROMPT_VERSION) -> None:
        """제주도 메타에 설명을 저장해 "그 건수로 만든 설명"을 심는다.

        Args:
            conn: 가짜 커넥션.
            member_count: 설명을 만든 시점의 묶음 크기(저장되는 축).
            prompt_version: 저장할 문안 판. 기본은 현행 판.
        """
        upsert_meta_description(conn, "장소", "제주도", description="가 요약",
                                member_count=member_count, prompt_version=prompt_version)

    def _targets(self, conn: _Conn) -> list[dict[str, Any]]:
        """현행 문안 판 기준 재생성 대상 목록.

        Args:
            conn: 가짜 커넥션.

        Returns:
            ``fetch_meta_description_targets`` 결과.
        """
        return fetch_meta_description_targets(conn, prompt_version=DESC_PROMPT_VERSION)

    def test_임계는_0과_1_사이의_비율이다(self) -> None:
        # 1 이상이면 어떤 증가로도 재생성되지 않고(설명이 영구히 낡는다), 0 이면 임계가 없다.
        self.assertGreater(DESC_REGEN_MIN_DELTA_RATIO, 0)
        self.assertLess(DESC_REGEN_MIN_DELTA_RATIO, 1)

    def test_열셋에서_열넷은_대상이_아니다(self) -> None:
        # 7.7% — 인기 개체에 자산 1건이 붙은 경우. 이것이 결함 ③ 의 실제 낭비 지점이었다.
        conn = self._bundle(13)
        self._describe(conn, 13)
        _seed_member(conn, _dummy_asset(13), _ent("제주 오름", "제주도"))
        self.assertEqual(self._targets(conn), [])

    def test_임계로_걸러지면_사유_목록에서_빠진다(self) -> None:
        # 대상 사유 어휘(DESC_TARGET_REASONS)는 그대로지만, 걸러진 케이스는 어떤 사유로도 오르지
        # 않는다 — 리포트가 "member_count 인데 재생성 안 함"이라 적는 반쪽 상태를 막는다.
        conn = self._bundle(13)
        self._describe(conn, 13)
        _seed_member(conn, _dummy_asset(13), _ent("제주 오름", "제주도"))
        self.assertEqual([target["reason"] for target in self._targets(conn)], [])

    def test_열셋에서_열일곱은_대상이다(self) -> None:
        # 30.8% ≥ 20% — 구성이 눈에 띄게 달라졌다.
        conn = self._bundle(13)
        self._describe(conn, 13)
        for index in range(13, 17):
            _seed_member(conn, _dummy_asset(index), _ent("제주 오름", "제주도"))

        targets = self._targets(conn)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["bundle_size"], 17)
        self.assertEqual(targets[0]["reason"], "member_count")

    def test_둘에서_셋은_대상이다(self) -> None:
        # 50% — 작은 묶음은 1건 증가가 곧 큰 변화다(비율 하나로 자연히 잡힌다 · 절대 하한 불필요).
        conn = self._bundle(2)
        self._describe(conn, 2)
        _seed_member(conn, _dummy_asset(2), _ent("제주 오름", "제주도"))

        targets = self._targets(conn)
        self.assertEqual(targets[0]["reason"], "member_count")

    def test_줄어든_구성도_같은_비율로_본다(self) -> None:
        # 13 → 10(23%) — 자산이 빠져도 설명은 낡는다. 방향이 아니라 크기로 본다.
        conn = self._bundle(13)
        self._describe(conn, 13)
        for edge in list(conn.edges.values())[:3]:
            edge["status"] = "rejected"  # 노출 대상 밖 → 묶음 크기에서 빠진다

        targets = self._targets(conn)
        self.assertEqual(targets[0]["bundle_size"], 10)
        self.assertEqual(targets[0]["reason"], "member_count")

    def test_임계_미만이어도_문안_판이_바뀌면_대상이다(self) -> None:
        # 🔴 문안 개정은 임계 무관 — 옛 문장은 새 규칙을 안 지킨다.
        conn = self._bundle(13)
        self._describe(conn, 13, prompt_version="mm_meta.desc.v0")
        _seed_member(conn, _dummy_asset(13), _ent("제주 오름", "제주도"))

        targets = self._targets(conn)
        self.assertEqual(targets[0]["reason"], "prompt_version")

    def test_임계_미만이어도_설명이_없으면_대상이다(self) -> None:
        # 🔴 설명 부재도 임계 무관 — 첫 생성은 비교 대상이 없다(손 SQL 로 설명만 지워진 상태 포함).
        conn = self._bundle(13)
        self._describe(conn, 13)
        conn.entity_nodes[("장소", "제주도")]["canonical"]["description"] = ""
        _seed_member(conn, _dummy_asset(13), _ent("제주 오름", "제주도"))

        targets = self._targets(conn)
        self.assertEqual(targets[0]["reason"], "missing")

    def test_저장_건수가_0이하면_대상이다(self) -> None:
        # 손 SQL 이 남긴 값 — 비율의 분모가 될 수 없다(0 나눗셈). 모호하면 다시 만든다
        # (``parse_member_reason`` 이 구 형식 스탬프를 다루는 것과 같은 보수적 폴백).
        conn = self._bundle(13)
        self._describe(conn, 13)
        for bad in (0, -3):
            with self.subTest(bad=bad):
                conn.entity_nodes[("장소", "제주도")]["canonical"]["desc_member_count"] = bad
                self.assertEqual(self._targets(conn)[0]["reason"], "member_count")

    def test_건수가_같으면_대상이_아니다(self) -> None:
        # 회귀 가드 — 임계 도입이 "최신은 건드리지 않는다"를 흔들지 않았는지 본다.
        conn = self._bundle(13)
        self._describe(conn, 13)
        self.assertEqual(self._targets(conn), [])


class TestVisibleStatusesDrift(unittest.TestCase):
    """🔴 드리프트 가드 — 설명 조회의 상태 필터는 **카드 조회와 같아야** 한다.

    ``graph_query`` 는 자산·묶음 조회에서 ``active``+``proposed`` 를 기본으로 본다(초기에는 전건
    proposed 라 이 기본값이 없으면 기능이 무동작이다 · spec §7). 설명 쪽이 다른 집합을 보면
    카드의 "확인된 N건"과 설명이 말하는 구성이 갈린다 — 사용자는 어느 쪽을 믿을지 알 수 없다.
    """

    def test_묶음_조회_기본값과_같은_집합이다(self) -> None:
        from src.relations.graph_query import _MM_META_DEFAULT_STATUSES

        self.assertEqual(set(MM_META_VISIBLE_STATUSES), set(_MM_META_DEFAULT_STATUSES))


if __name__ == "__main__":
    unittest.main()
