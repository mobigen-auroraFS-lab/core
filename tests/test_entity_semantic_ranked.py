"""의미 갈래가 **순위**도 함께 돌려준다(2026-09-21).

왜 필요한가: 집합(``frozenset``)에는 순서가 없다. 그래서 화면이 "뜻으로 상위인 것을 앞에 세우자"
고 해도 **무엇이 상위인지 알 방법이 없었다.** 실제로 「남자 배우」에서 kNN 1등은 하정우인데
화면 1위는 채원빈이었다(구성 자산 수가 순서를 지배 · 하정우는 자산이 적어 뒤로 밀림).

무엇을 돌려주나: ``ranked`` — 코사인 내림차순 **튜플**. 몇 개를 앞세울지는 **화면 정책**이라
백엔드가 정한다(093 경계 — 코어는 손잡이만 준다).

🔴 ``keys`` 와 ``ranked`` 는 **같은 것을 담는다**(순서만 다르다). 하나가 거르고 다른 하나가 안
거르면 화면과 총계가 어긋난다 — 그 일치를 여기서 봉인한다.
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from src.search import entity_search_os


class _Client:
    """kNN 본문에만 답하는 최소 대역(낱말 갈래는 빈 결과)."""

    def __init__(self, knn_hits: list[dict[str, Any]]) -> None:
        self._knn = knn_hits

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "knn" in json.dumps(body, ensure_ascii=False):
            return {"hits": {"total": {"value": len(self._knn), "relation": "eq"},
                             "hits": self._knn}}
        return {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}


def _knn(uid: str, cosine: float) -> dict[str, Any]:
    return {"_id": f"인물/{uid}", "_score": (1.0 + cosine) / 2.0,
            "_source": {"entity_type": "인물", "entity_uid": uid}}


# 색인이 돌려주는 순서 그대로(코사인 내림차순). 뒤 둘은 하한(0.44) 아래라 결과에서 빠진다.
_ROWS = [_knn("하정우", 0.507), _knn("정해인", 0.505), _knn("이제훈", 0.504),
         _knn("채원빈", 0.461), _knn("김고은", 0.430), _knn("박보영", 0.400)]


class TestSemanticRanked(unittest.TestCase):
    def test_순위를_코사인_내림차순으로_돌려준다(self) -> None:
        got = entity_search_os.semantic_entity_keys(
            _Client(_ROWS), "idx", query_vector=[0.1] * 4)
        self.assertEqual(got.ranked,
                         (("인물", "하정우"), ("인물", "정해인"),
                          ("인물", "이제훈"), ("인물", "채원빈")))

    def test_순위와_집합이_같은_것을_담는다(self) -> None:
        """🔴 한쪽만 거르면 화면(순서)과 총계(집합)가 어긋난다."""
        got = entity_search_os.semantic_entity_keys(
            _Client(_ROWS), "idx", query_vector=[0.1] * 4)
        self.assertEqual(set(got.ranked), set(got.keys))

    def test_막히면_순위도_비어_있다(self) -> None:
        low = [_knn("김고은", 0.30), _knn("박보영", 0.20)]
        got = entity_search_os.semantic_entity_keys(
            _Client(low), "idx", query_vector=[0.1] * 4)
        self.assertEqual(got.ranked, ())
        self.assertEqual(got.keys, frozenset())

    def test_같은_입력이면_같은_순위다(self) -> None:
        """헌법 3조 — 튜플이라 순서까지 같아야 한다(집합과 달리 순서가 계약이다)."""
        runs = [entity_search_os.semantic_entity_keys(
            _Client(_ROWS), "idx", query_vector=[0.1] * 4).ranked for _ in range(3)]
        self.assertEqual(len(set(runs)), 1)

    def test_합집합_결과에도_순위가_실린다(self) -> None:
        """화면은 ``match_entity_keys`` 만 부른다 — 여기서 못 받으면 쓸 수가 없다."""
        got = entity_search_os.match_entity_keys(
            _Client(_ROWS), "idx", query="배우", query_vector=[0.1] * 4)
        self.assertEqual(got.semantic_ranked[0], ("인물", "하정우"))
        self.assertEqual(set(got.semantic_ranked), set(got.semantic_keys))

    def test_의미를_껐으면_순위도_비어_있다(self) -> None:
        got = entity_search_os.match_entity_keys(
            _Client(_ROWS), "idx", query="배우", query_vector=None)
        self.assertEqual(got.semantic_ranked, ())


if __name__ == "__main__":
    unittest.main()
