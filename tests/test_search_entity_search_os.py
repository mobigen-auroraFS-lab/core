"""092 T009 — 개체 OpenSearch 융합 검색(``src/search/entity_search_os.py``) 단위 테스트.

무엇을 검증하나: BM25(형태소)와 kNN(임베딩)을 섞는 규칙이다. 실 OS 없이 **가짜 클라이언트**로
계약만 본다(실 OS 재현은 tasks G3 T010 · 실 서버 판정은 G5).

🔴 **게이트는 kNN 에만 건다.** BM25 결과까지 막으면 단어 하나 재현율이 **90% → 50%** 로 무너진다
(착수 전 실측) — BM25 로는 명확히 걸리는 질의인데 벡터 신호가 약한 경우가 많기 때문이다.
자산 검색의 lexical rescue("게이트 실패해도 어휘 증거가 있으면 회수")와 같은 취지다.

🔴 **operator 는 ``or``**(자산은 ``and``). 개체는 텍스트가 짧아 "모든 낱말이 다 있어야 한다"가
가혹하다 — 실측 다어절 재현율 73.3%(and) vs **86.7%(or)**.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.config import search_constants
from src.search.entity_search_os import search_entities_hybrid


def _hit(uid: str, score: float, etype: str = "작품") -> dict[str, Any]:
    return {"_id": f"{etype}/{uid}", "_score": score,
            "_source": {"entity_type": etype, "entity_uid": uid, "name": uid}}


class _FakeClient:
    """search 호출을 기억하고 미리 정한 응답을 준다."""

    def __init__(self, bm25: list, knn: list) -> None:
        self._bm25, self._knn = bm25, knn
        self.bodies: list[dict[str, Any]] = []

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:  # noqa: D102
        self.bodies.append(body)
        hits = self._knn if "knn" in str(body.get("query", {})) else self._bm25
        return {"hits": {"hits": hits}}


def _cos_to_score(cos: float) -> float:
    """코사인 → lucene knn _score((1+cos)/2). 테스트가 의도한 코사인을 만들기 위한 역산."""
    return (1.0 + cos) / 2.0


class TestQueryShape(unittest.TestCase):
    """OS 에 보내는 질의 모양 — 상수를 실제로 쓰는지 본다."""

    def test_필드_가중과_operator_를_상수에서_읽는다(self) -> None:
        c = _FakeClient([], [])
        search_entities_hybrid(c, "mm_entities", query="한글", query_vector=[0.1] * 1536)
        mm = c.bodies[0]["query"]["multi_match"]
        self.assertEqual(mm["fields"], list(search_constants.ENTITY_BM25_FIELDS_DEFAULT))
        self.assertEqual(mm["operator"], search_constants.ENTITY_BM25_OPERATOR_DEFAULT)
        self.assertEqual(mm["operator"], "or")      # 자산(and)과 다르다는 것을 못 박는다

    def test_kNN_질의에_벡터를_싣는다(self) -> None:
        c = _FakeClient([], [])
        vec = [0.2] * 1536
        search_entities_hybrid(c, "mm_entities", query="한글", query_vector=vec)
        knn = c.bodies[1]["query"]["knn"]["vec"]
        self.assertEqual(knn["vector"], vec)


class TestFusion(unittest.TestCase):
    """융합 — 두 신호를 정규화해 섞고, 게이트는 kNN 에만 건다."""

    def test_양쪽에서_걸린_것이_위로_온다(self) -> None:
        # 훈민정음은 BM25·kNN 둘 다, 한라산은 kNN 만 → 훈민정음이 위여야 한다.
        c = _FakeClient(
            bm25=[_hit("훈민정음", 12.0)],
            knn=[_hit("훈민정음", _cos_to_score(0.46)), _hit("한라산", _cos_to_score(0.42)),
                 _hit("한강", _cos_to_score(0.20)), _hit("김치", _cos_to_score(0.15))])
        got = search_entities_hybrid(c, "mm_entities", query="한글", query_vector=[0.1] * 1536)
        self.assertEqual(got[0]["entity_uid"], "훈민정음")

    def test_게이트가_막으면_BM25_결과만_남는다(self) -> None:
        """🔴 이것이 lexical rescue 다 — 벡터 신호가 약해도 글자가 맞으면 내보낸다."""
        # 코사인이 평평하다(0.40~0.39) → (top − baseline) < 0.15 → kNN 은 버려진다.
        c = _FakeClient(
            bm25=[_hit("훈민정음", 12.0)],
            knn=[_hit("한라산", _cos_to_score(0.40)), _hit("한강", _cos_to_score(0.395)),
                 _hit("김치", _cos_to_score(0.39)), _hit("된장", _cos_to_score(0.39))])
        got = search_entities_hybrid(c, "mm_entities", query="한글", query_vector=[0.1] * 1536)
        self.assertEqual([r["entity_uid"] for r in got], ["훈민정음"])

    def test_BM25가_0건이어도_의미로_찾는다(self) -> None:
        # 글자가 하나도 없는 개념 질의(`발효 식품`→김치)가 이 경로로 걸린다.
        c = _FakeClient(
            bm25=[],
            knn=[_hit("김치", _cos_to_score(0.60)), _hit("된장", _cos_to_score(0.25)),
                 _hit("한강", _cos_to_score(0.20)), _hit("NASA", _cos_to_score(0.18))])
        got = search_entities_hybrid(c, "mm_entities", query="발효 식품", query_vector=[0.1] * 1536)
        self.assertEqual(got[0]["entity_uid"], "김치")

    def test_상위_N_만_돌려준다(self) -> None:
        c = _FakeClient(bm25=[_hit(f"e{i}", 10.0 - i) for i in range(6)], knn=[])
        self.assertEqual(len(search_entities_hybrid(
            c, "mm_entities", query="q", query_vector=[0.1] * 1536, top_n=3)), 3)

    def test_같은_입력이면_같은_순서(self) -> None:
        """결정성(E5) — 동점이면 문서 id 오름차순으로 고정한다."""
        c = _FakeClient(bm25=[_hit("나", 10.0), _hit("가", 10.0)], knn=[])
        first = [r["entity_uid"] for r in search_entities_hybrid(
            c, "mm_entities", query="q", query_vector=[0.1] * 1536)]
        for _ in range(4):
            c2 = _FakeClient(bm25=[_hit("나", 10.0), _hit("가", 10.0)], knn=[])
            self.assertEqual([r["entity_uid"] for r in search_entities_hybrid(
                c2, "mm_entities", query="q", query_vector=[0.1] * 1536)], first)

    def test_걸린_이유를_싣는다(self) -> None:
        # 화면이 "글자가 맞았다"와 "뜻이 가깝다"를 갈라 보여줄 수 있어야 신뢰가 생긴다(090 관례).
        c = _FakeClient(
            bm25=[_hit("훈민정음", 12.0)],
            knn=[_hit("세종대왕", _cos_to_score(0.55)), _hit("한강", _cos_to_score(0.20)),
                 _hit("김치", _cos_to_score(0.15)), _hit("된장", _cos_to_score(0.10))])
        got = {r["entity_uid"]: r for r in search_entities_hybrid(
            c, "mm_entities", query="한글", query_vector=[0.1] * 1536, top_n=5)}
        self.assertTrue(got["훈민정음"]["by_text"])
        self.assertTrue(got["세종대왕"]["by_semantic"])


class TestRobustness(unittest.TestCase):
    """OS 응답이 비거나 깨져도 죽지 않는다 — 검색이 500 이 되면 089 로 되던 것까지 잃는다."""

    def test_빈_응답이면_빈_결과(self) -> None:
        c = _FakeClient([], [])
        self.assertEqual(search_entities_hybrid(
            c, "mm_entities", query="q", query_vector=[0.1] * 1536), [])

    def test_source_가_없어도_id_로_되살린다(self) -> None:
        c = _FakeClient(bm25=[{"_id": "작품/훈민정음", "_score": 5.0}], knn=[])
        got = search_entities_hybrid(c, "mm_entities", query="q", query_vector=[0.1] * 1536)
        self.assertEqual(got[0]["entity_type"], "작품")
        self.assertEqual(got[0]["entity_uid"], "훈민정음")


class TestTopNDefault(unittest.TestCase):
    """🔴 기본 상위 N 은 **5**(090 의 3이 아니다) — 정답이 4~5위에 있는 경우가 많았다."""

    def test_기본값이_상수와_같다(self) -> None:
        c = _FakeClient(bm25=[_hit(f"e{i}", 10.0 - i) for i in range(8)], knn=[])
        got = search_entities_hybrid(c, "mm_entities", query="q", query_vector=[0.1] * 1536)
        self.assertEqual(len(got), search_constants.ENTITY_SEARCH_TOP_N_DEFAULT)
        self.assertEqual(search_constants.ENTITY_SEARCH_TOP_N_DEFAULT, 5)
