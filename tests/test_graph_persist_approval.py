"""081 승인·영속화 게이트가 `sync_graph_edges` 에 배선됐는지 — 🔴 프로덕션 동작 변경 지점.

**무엇을 검증하는가**
1. 자동승인 kind 제외: 신뢰도가 아무리 높아도 제외 종류는 ``proposed`` 다.
2. 영속화 폐기 게이트: 유사도 계열 저신뢰는 **INSERT 가 아예 일어나지 않는다**(숨기는 게 아니다).
3. **게이트를 끈 설정에서 기존 동작과 완전히 같다** — 롤백 경로 증명(정지점 요구사항).
4. 폐기 건수를 보고한다 — 조용히 버리면 "커버리지가 왜 줄었나"를 추적할 수 없다.

**접근**: `fetch_relation_kind`·`ensure_asset_node` 는 목으로 세우고 커서만 가짜로 둔다. SQL 전체를
흉내내면 테스트가 SQL 문법과 씨름하게 되고, 정작 검증 대상(게이트 판정)이 흐려진다.
"""
from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import src.relations.graph_persist as gp


class _Cur:
    """실행된 SQL 을 모아 두는 가짜 커서. graph_edge upsert 의 RETURNING 한 행을 돌려준다."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append(" ".join(str(sql).split()))

    def fetchone(self) -> tuple[str, str]:
        # (edge_id, status) 형태를 기대하는 호출부가 있으므로 2원소로 돌려준다.
        return ("018f0000-0000-7000-8000-000000000251", "proposed")

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _Conn:
    def __init__(self) -> None:
        self.log: list[str] = []

    def cursor(self, **_k: Any) -> _Cur:
        return _Cur(self.log)


_KIND_ROWS = {
    "same_domain": {"relation_kind_id": "k-sd", "is_symmetric": True},
    "duplicate_near": {"relation_kind_id": "k-dn", "is_symmetric": True},
    "references": {"relation_kind_id": "k-rf", "is_symmetric": False},
}


def _run(edges: list[dict], **kw: Any) -> tuple[_Conn, tuple[int, int], dict[str, int]]:
    """게이트 인자를 주어 sync_graph_edges 를 돌리고 (커넥션, 반환값, 통계) 를 준다.

    Args:
        edges: LLM 제안 엣지 목록.
        **kw: `sync_graph_edges` 로 그대로 넘길 추가 인자(게이트 설정 등).

    Returns:
        ``(가짜 커넥션, (upserted, skipped), stats)``.
    """
    conn = _Conn()
    stats: dict[str, int] = {}
    targets = frozenset(str(e["target_media_item_id"]) for e in edges)
    with mock.patch.object(gp, "ensure_asset_node", side_effect=lambda _c, a: f"node-{a}"), \
         mock.patch.object(gp, "fetch_relation_kind",
                           side_effect=lambda _c, *, kind_code, status: _KIND_ROWS.get(kind_code)), \
         mock.patch.object(gp, "_topic_canonicalize_enabled", return_value=False):
        result = gp.sync_graph_edges(
            conn, source_asset_id="018f0000-0000-7000-8000-000000000253",
            edges=edges, allowed_target_ids=targets, stats=stats, **kw)
    return conn, result, stats


def _edge(kind: str, conf: float | None, tid: str = "018f0000-0000-7000-8000-000000000257") -> dict:
    return {"target_media_item_id": tid, "relation_type_code": kind, "confidence": conf}


def _inserted(conn: _Conn) -> int:
    return sum(1 for s in conn.log if "INSERT INTO graph_edge" in s)


class TestDecideStatusKindGate(unittest.TestCase):
    def test_제외_kind는_신뢰도가_높아도_proposed다(self):
        self.assertEqual(
            gp._decide_status(0.99, 0.99, 0.9, 0.0, kind_code="same_domain",
                              auto_approve_exclude_kinds=frozenset({"same_domain"})),
            "proposed")

    def test_그_외_kind는_기존대로_active다(self):
        self.assertEqual(
            gp._decide_status(0.99, 0.99, 0.9, 0.0, kind_code="duplicate_near",
                              auto_approve_exclude_kinds=frozenset({"same_domain"})),
            "active")

    def test_제외_목록이_비면_기존_동작과_같다(self):
        self.assertEqual(
            gp._decide_status(0.99, 0.99, 0.9, 0.0, kind_code="same_domain",
                              auto_approve_exclude_kinds=frozenset()),
            "active")

    def test_신뢰도_관문은_그대로_동작한다(self):
        # kind 게이트를 통과해도 신뢰도가 낮으면 proposed 여야 한다(두 관문은 AND).
        self.assertEqual(
            gp._decide_status(0.5, 0.99, 0.9, 0.0, kind_code="duplicate_near",
                              auto_approve_exclude_kinds=frozenset()),
            "proposed")


class TestPersistGate(unittest.TestCase):
    def test_유사도_계열_저신뢰는_INSERT_되지_않는다(self):
        conn, (upserted, skipped), stats = _run(
            [_edge("same_domain", 0.6)], persist_min_conf_similarity=0.75)
        self.assertEqual(_inserted(conn), 0)
        self.assertEqual(upserted, 0)
        self.assertEqual(skipped, 1)

    def test_유사도_계열_고신뢰는_INSERT_된다(self):
        conn, (upserted, _), _ = _run(
            [_edge("duplicate_near", 0.9)], persist_min_conf_similarity=0.75)
        self.assertEqual(_inserted(conn), 1)
        self.assertEqual(upserted, 1)

    def test_명시적_계열_저신뢰는_INSERT_된다(self):
        # path_signal 부활 시 저신뢰 명시적 제안이 정당하게 나온다.
        conn, (upserted, _), _ = _run(
            [_edge("references", 0.3)], persist_min_conf_similarity=0.75)
        self.assertEqual(_inserted(conn), 1)
        self.assertEqual(upserted, 1)

    def test_폐기_건수를_보고한다(self):
        _, _, stats = _run(
            [_edge("same_domain", 0.6, tid="018f0000-0000-7000-8000-000000000254"),
             _edge("duplicate_near", 0.7, tid="018f0000-0000-7000-8000-000000000255"),
             _edge("references", 0.2, tid="018f0000-0000-7000-8000-000000000256")],
            persist_min_conf_similarity=0.75)
        self.assertEqual(stats["gated_low_conf"], 2)

    def test_폐기된_엣지는_노드를_만들지_않는다(self):
        # ensure_asset_node 를 게이트보다 먼저 부르면 엣지 없는 고아 노드가 남는다.
        conn = _Conn()
        created: list[str] = []
        with mock.patch.object(gp, "ensure_asset_node",
                               side_effect=lambda _c, a: created.append(a) or f"node-{a}"), \
             mock.patch.object(gp, "fetch_relation_kind",
                               side_effect=lambda _c, *, kind_code, status: _KIND_ROWS.get(kind_code)), \
             mock.patch.object(gp, "_topic_canonicalize_enabled", return_value=False):
            gp.sync_graph_edges(
                conn, source_asset_id="018f0000-0000-7000-8000-000000000253",
                edges=[_edge("same_domain", 0.6)],
                allowed_target_ids=frozenset({"018f0000-0000-7000-8000-000000000257"}),
                persist_min_conf_similarity=0.75)
        # 소스 노드 1건만 — 타깃 노드는 만들지 않았다.
        self.assertEqual(created, ["018f0000-0000-7000-8000-000000000253"])


class TestPromptExcludedKindGate(unittest.TestCase):
    """084 방어 ② — 프롬프트 제외 종류는 **엣지가 되지 않는다**(이중 방어 2단).

    1단은 프롬프트 카탈로그 제외(``fetch_active_relation_kinds``)이고, 2단이 여기다. 왜 2단이
    필요한가: LLM 은 카탈로그에 없는 코드도 지어낼 수 있다. ``mm_member`` 가 **active** 로
    등록돼 있으므로(소속 엣지가 되려면 active 여야 한다) 기존 검사(active 인가?)만으로는 자산↔자산
    쌍에 그 종류가 붙는 것을 막지 못한다 — 그러면 관계 화면에 "멀티모달 메타 소속"이라는 이름표가
    달린 자산 쌍이 생기고, 소속 조회(dst=entity 가정)와 불변식이 함께 깨진다.

    🔴 회귀: 현 카탈로그 5종은 전부 그대로 통과해야 한다(조이기가 기존 관계 경로를 건드리지 않음).
    """

    def test_제외_종류_제안은_엣지가_되지_않는다(self):
        from src.relations.schema import MM_MEMBER_KIND_CODE

        # 카탈로그에 active 로 있어도(소속 kind 는 active 다) 관계 경로에서는 기각된다.
        with mock.patch.dict(_KIND_ROWS,
                             {MM_MEMBER_KIND_CODE: {"relation_kind_id": "k-mm",
                                                    "is_symmetric": False}}):
            conn, (upserted, skipped), _ = _run([_edge(MM_MEMBER_KIND_CODE, 0.9)])
        self.assertEqual(_inserted(conn), 0)
        self.assertEqual((upserted, skipped), (0, 1))

    def test_레거시_도메인_코드도_같은_기준으로_막힌다(self):
        # 기준을 PROMPT_EXCLUDED_KIND_CODES 하나로 모은 결과 — 사본 없이 같은 값을 쓴다.
        with mock.patch.dict(_KIND_ROWS,
                             {"medical": {"relation_kind_id": "k-md", "is_symmetric": True}}):
            conn, (upserted, skipped), _ = _run([_edge("medical", 0.9)])
        self.assertEqual(_inserted(conn), 0)
        self.assertEqual((upserted, skipped), (0, 1))

    def test_제외_종류는_노드도_만들지_않는다(self):
        # 게이트가 ensure_asset_node 보다 뒤에 있으면 엣지 없는 고아 노드가 남는다.
        from src.relations.schema import MM_MEMBER_KIND_CODE

        conn = _Conn()
        created: list[str] = []
        with mock.patch.object(gp, "ensure_asset_node",
                               side_effect=lambda _c, a: created.append(a) or f"node-{a}"), \
             mock.patch.object(gp, "fetch_relation_kind",
                               side_effect=lambda _c, *, kind_code, status: {
                                   "relation_kind_id": "k-mm", "is_symmetric": False}), \
             mock.patch.object(gp, "_topic_canonicalize_enabled", return_value=False):
            gp.sync_graph_edges(
                conn, source_asset_id="018f0000-0000-7000-8000-000000000253",
                edges=[_edge(MM_MEMBER_KIND_CODE, 0.9)],
                allowed_target_ids=frozenset({"018f0000-0000-7000-8000-000000000257"}))
        # 소스 노드 1건만(그 호출은 게이트보다 앞이다) — 타깃 노드는 만들지 않았다.
        self.assertEqual(created, ["018f0000-0000-7000-8000-000000000253"])

    def test_카탈로그_5종은_그대로_통과한다(self):
        # 🔴 회귀 증명 — 조이기 전후 동작이 같다(제외 집합에 든 코드만 달라진다).
        conn, (upserted, skipped), _ = _run(
            [_edge("same_domain", 0.9, tid="018f0000-0000-7000-8000-000000000261"),
             _edge("duplicate_near", 0.9, tid="018f0000-0000-7000-8000-000000000262"),
             _edge("references", 0.9, tid="018f0000-0000-7000-8000-000000000263")])
        self.assertEqual(_inserted(conn), 3)
        self.assertEqual((upserted, skipped), (3, 0))

    def test_제외_종류는_kind_조회조차_하지_않는다(self):
        # 조회 전에 걸러야 "active 로 등록된 종류"라는 사실이 판정에 끼어들지 않는다(순서가 계약).
        from src.relations.schema import MM_MEMBER_KIND_CODE

        conn = _Conn()
        looked: list[str] = []
        with mock.patch.object(gp, "ensure_asset_node", side_effect=lambda _c, a: f"node-{a}"), \
             mock.patch.object(gp, "fetch_relation_kind",
                               side_effect=lambda _c, *, kind_code, status: (
                                   looked.append(kind_code) or _KIND_ROWS.get(kind_code))), \
             mock.patch.object(gp, "_topic_canonicalize_enabled", return_value=False):
            gp.sync_graph_edges(
                conn, source_asset_id="018f0000-0000-7000-8000-000000000253",
                edges=[_edge(MM_MEMBER_KIND_CODE, 0.9)],
                allowed_target_ids=frozenset({"018f0000-0000-7000-8000-000000000257"}))
        self.assertEqual(looked, [])


class TestGateOffIsUnchanged(unittest.TestCase):
    """🔴 정지점 요구: 게이트를 끈 설정에서 기존 동작과 **완전히 같다**(롤백 경로 증명)."""

    _EDGES = [_edge("same_domain", 0.1, tid="018f0000-0000-7000-8000-000000000258"),
              _edge("duplicate_near", None, tid="018f0000-0000-7000-8000-000000000259"),
              _edge("references", 0.99, tid="018f0000-0000-7000-8000-000000000260")]

    def test_게이트_인자를_아예_주지_않으면_전건_INSERT다(self):
        conn, (upserted, skipped), _ = _run(list(self._EDGES))
        self.assertEqual(_inserted(conn), 3)
        self.assertEqual((upserted, skipped), (3, 0))

    def test_게이트를_끈_설정도_같다(self):
        conn, result, stats = _run(
            list(self._EDGES),
            persist_min_conf_similarity=0.0,
            auto_approve_exclude_kinds=frozenset())
        self.assertEqual(_inserted(conn), 3)
        self.assertEqual(result, (3, 0))
        self.assertEqual(stats.get("gated_low_conf", 0), 0)

    def test_켠_설정과_끈_설정의_SQL_이_다르다(self):
        # 이 테스트가 없으면 위 두 테스트는 "게이트가 아예 배선 안 됐어도" 통과한다.
        on, _, _ = _run(list(self._EDGES), persist_min_conf_similarity=0.75)
        off, _, _ = _run(list(self._EDGES), persist_min_conf_similarity=0.0)
        self.assertLess(_inserted(on), _inserted(off))


if __name__ == "__main__":
    unittest.main()
