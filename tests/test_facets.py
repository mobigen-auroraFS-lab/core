"""093 2단계 — 축과 무관한 패싯 집계 정본 ``src.search.facets.aggregate_facets`` 단위 테스트.

무엇을 봉인하나: 세는 규칙 하나(단위당 1회 계수 · 빈 키 버림 · 하한 · 상위 N · 결정적 정렬 · 최빈
표기)와, 태그 축 래퍼(``aggregate_tag_facets``)가 이 정본을 부른 결과와 **정확히 같음**을 본다.
후자가 중요한 이유: 래퍼로 바꾸기 전 083 의 태그 패싯 동작이 한 글자도 달라지지 않았음을 증명한다
(사용자 요구 — "책임 분리 후 모든 기능은 이전과 같아야 한다").

DB·OpenSearch·LLM 없음(순수).
"""

from __future__ import annotations

import random
import unittest
from collections.abc import Iterator, Mapping
from typing import Any

from src.search.facets import aggregate_facets, display_label
from src.search.tag_facets import aggregate_tag_facets, normalize_tag_key


def _topic_keys(row: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """주제 축의 묶는 법 — 값이 곧 키·라벨(닫힌 어휘)."""
    for t in row.get("topics") or []:
        yield str(t), str(t)


def _tag_keys(row: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """태그 축의 묶는 법 — 정규화 키로 묶고 원문을 라벨 후보로 낸다(래퍼와 같은 규칙)."""
    tags = row.get("tags")
    if not isinstance(tags, (list, tuple)):
        return
    for raw in tags:
        if isinstance(raw, str):
            yield normalize_tag_key(raw), raw


class TestCounting(unittest.TestCase):
    """계수 단위 — 기본은 행 하나, ``unit_of`` 를 주면 그 id 로 묶는다."""

    def test_행_하나가_한_단위다(self) -> None:
        rows = [{"topics": ["한식"]}, {"topics": ["한식", "한식"]}, {"topics": ["양식"]}]
        out = aggregate_facets(rows, keys_of=_topic_keys)
        self.assertEqual(out["items"], [{"label": "한식", "count": 2}, {"label": "양식", "count": 1}])

    def test_unit_of_를_주면_같은_자산은_한_번만_센다(self) -> None:
        # 같은 자산이 text·video 두 버킷에 있어도 주제 건수는 1 — 화면 건수 = "누르면 몇 건" 을 지킨다.
        rows = [
            {"asset_id": "a1", "topics": ["한식"]},
            {"asset_id": "a1", "topics": ["한식"]},
            {"asset_id": "a2", "topics": ["한식"]},
        ]
        out = aggregate_facets(rows, keys_of=_topic_keys, unit_of=lambda r: str(r.get("asset_id") or ""))
        self.assertEqual(out["items"], [{"label": "한식", "count": 2}])

    def test_단위_id_가_빈_행은_세지_않는다(self) -> None:
        rows = [{"asset_id": "", "topics": ["한식"]}, {"topics": ["한식"]}]
        out = aggregate_facets(rows, keys_of=_topic_keys, unit_of=lambda r: str(r.get("asset_id") or ""))
        self.assertEqual(out["items"], [])

    def test_빈_키는_버린다(self) -> None:
        out = aggregate_facets([{"topics": ["", "한식"]}], keys_of=_topic_keys)
        self.assertEqual(out["items"], [{"label": "한식", "count": 1}])
        self.assertNotIn("", out["label_by_key"])

    def test_행_안_같은_짝은_한_번만_센다(self) -> None:
        # 한 행에 같은 태그 원문이 두 번 있어도 계수 1·라벨 투표 1회.
        out = aggregate_facets([{"tags": ["김치", "김치"]}], keys_of=_tag_keys)
        self.assertEqual(out["items"], [{"label": "김치", "count": 1}])

    def test_매핑이_아닌_행은_건너뛴다(self) -> None:
        out = aggregate_facets(["문자열", None, 3, {"topics": ["한식"]}], keys_of=_topic_keys)
        self.assertEqual(out["items"], [{"label": "한식", "count": 1}])


class TestThresholdAndOrder(unittest.TestCase):
    """하한·상위 N·정렬 — 동률까지 고정."""

    def _rows(self) -> list[dict[str, Any]]:
        return (
            [{"topics": ["가"]}] * 3 + [{"topics": ["나"]}] * 3 + [{"topics": ["다"]}] * 2 + [{"topics": ["라"]}]
        )

    def test_건수_내림차순_동률은_라벨_코드포인트_오름차순(self) -> None:
        out = aggregate_facets(self._rows(), keys_of=_topic_keys)
        self.assertEqual([i["label"] for i in out["items"]], ["가", "나", "다", "라"])

    def test_min_count_미만은_감춘다(self) -> None:
        out = aggregate_facets(self._rows(), keys_of=_topic_keys, min_count=2)
        self.assertEqual([i["label"] for i in out["items"]], ["가", "나", "다"])

    def test_top_n_none_이면_전부_노출하고_has_more_는_거짓(self) -> None:
        out = aggregate_facets(self._rows(), keys_of=_topic_keys, top_n=None)
        self.assertEqual(len(out["items"]), 4)
        self.assertFalse(out["has_more"])

    def test_top_n_으로_잘리면_has_more_참(self) -> None:
        out = aggregate_facets(self._rows(), keys_of=_topic_keys, top_n=2)
        self.assertEqual([i["label"] for i in out["items"]], ["가", "나"])
        self.assertTrue(out["has_more"])

    def test_label_by_key_는_잘린_키까지_담는다(self) -> None:
        out = aggregate_facets(self._rows(), keys_of=_topic_keys, top_n=1, min_count=3)
        self.assertEqual(set(out["label_by_key"]), {"가", "나", "다", "라"})

    def test_입력_순서를_섞어도_결과가_같다(self) -> None:
        rows = [{"tags": [t]} for t in ["전통음식", "전통 음식", "김치", "전통음식", "떡", "김치"]]
        base = aggregate_facets(rows, keys_of=_tag_keys)
        for seed in range(20):
            shuffled = rows[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(aggregate_facets(shuffled, keys_of=_tag_keys), base)

    def test_범위_오류는_예외(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_facets([], keys_of=_topic_keys, top_n=0)
        with self.assertRaises(ValueError):
            aggregate_facets([], keys_of=_topic_keys, min_count=0)


class TestLabel(unittest.TestCase):
    """표시 라벨 — 기본은 최빈 표기, 축이 원하면 ``label_of`` 로 바꾼다."""

    def test_기본은_최빈_표기_동률은_코드포인트_오름차순(self) -> None:
        rows = [{"tags": ["전통음식"]}, {"tags": ["전통 음식"]}, {"tags": ["전통 음식"]}]
        out = aggregate_facets(rows, keys_of=_tag_keys)
        self.assertEqual(out["items"], [{"label": "전통 음식", "count": 3}])
        self.assertEqual(display_label(["b", "a"]), "a")

    def test_label_of_를_주면_그_규칙을_쓴다(self) -> None:
        rows = [{"topics": [" 한식 "]}, {"topics": [" 한식 "]}]
        # 닫힌 어휘 축: 키가 곧 라벨 — 공백을 다듬지 않고 원문 그대로 둔다.
        out = aggregate_facets(rows, keys_of=_topic_keys, label_of=lambda o: o[0])
        self.assertEqual(out["items"], [{"label": " 한식 ", "count": 2}])


class TestTagWrapperParity(unittest.TestCase):
    """태그 래퍼 = 정본 호출. 무작위 입력 전수에서 결과가 정확히 같다."""

    def test_무작위_입력에서_래퍼와_정본이_같다(self) -> None:
        rng = random.Random(93)
        vocab = ["전통음식", "전통 음식", "김치", "Kimchi", "kimchi", "떡", " ", "", "검은색", "검은 색"]
        for _ in range(300):
            rows: list[Any] = []
            for _r in range(rng.randint(0, 8)):
                kind = rng.random()
                if kind < 0.1:
                    rows.append({"tags": "문자열"})  # 배열 아님 → 태그 없음
                elif kind < 0.15:
                    rows.append({"no_tags": True})
                else:
                    rows.append({"tags": [rng.choice(vocab) for _ in range(rng.randint(0, 5))] + [7]})
            top_n = rng.randint(1, 4)
            min_count = rng.randint(1, 3)
            self.assertEqual(
                aggregate_tag_facets(rows, top_n=top_n, min_count=min_count),
                aggregate_facets(rows, keys_of=_tag_keys, top_n=top_n, min_count=min_count),
            )

    def test_래퍼의_범위_검사는_그대로다(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_tag_facets([], top_n=0, min_count=2)
        with self.assertRaises(ValueError):
            aggregate_tag_facets([], top_n=12, min_count=0)


if __name__ == "__main__":
    unittest.main()
