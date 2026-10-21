"""045 Phase B — SearchFilters · OS bool.filter 변환."""

from __future__ import annotations

import unittest
from datetime import date

from src.search.search_filters import (
    SearchFilters,
    filters_to_opensearch_bool,
    parse_search_filters,
)


class FiltersToOpensearchBoolTest(unittest.TestCase):
    def test_empty_filters(self) -> None:
        self.assertEqual(filters_to_opensearch_bool(None), [])
        self.assertEqual(filters_to_opensearch_bool(SearchFilters()), [])

    def test_file_ext_clause(self) -> None:
        clauses = filters_to_opensearch_bool(SearchFilters(file_exts=("pdf", "txt")))
        self.assertEqual(clauses, [{"terms": {"filter_kw.file_ext": ["pdf", "txt"]}}])

    def test_created_at_range(self) -> None:
        clauses = filters_to_opensearch_bool(
            SearchFilters(
                created_from=date(2026, 1, 1),
                created_to=date(2026, 6, 30),
            )
        )
        self.assertEqual(
            clauses,
            [
                {"range": {"filter_date.created_at": {"gte": "2026-01-01"}}},
                {"range": {"filter_date.created_at": {"lte": "2026-06-30"}}},
            ],
        )

    def test_combined_clauses(self) -> None:
        clauses = filters_to_opensearch_bool(
            SearchFilters(file_exts=("mp3",), created_from=date(2026, 1, 1))
        )
        self.assertEqual(len(clauses), 2)


class ParseSearchFiltersTest(unittest.TestCase):
    def test_none_when_empty(self) -> None:
        self.assertIsNone(parse_search_filters())

    def test_normalizes_ext(self) -> None:
        sf = parse_search_filters(file_ext=[".PDF", "txt"])
        assert sf is not None
        self.assertEqual(sf.file_exts, ("pdf", "txt"))


MB = 1024 * 1024


class FileSizeRangesTest(unittest.TestCase):
    """크기 필터(spec 111) — 바이트 ``(이상, 미만)`` 범위. 비어 있으면 절이 없어 기존 질의가 그대로다.

    백엔드가 아직 이 값을 넘기지 않아도(크기 칸에 501) 코어를 먼저 반영할 수 있어야 한다 — 하위 호환을 함께 본다.
    """

    def test_fields_keep_order_and_add_size_last(self) -> None:
        import dataclasses

        names = [f.name for f in dataclasses.fields(SearchFilters)]
        self.assertEqual(names[:7], ["file_exts", "created_from", "created_to", "topics", "subtopics", "tags",
                                     "modalities"])
        self.assertEqual(names[-1], "file_size_ranges")
        self.assertEqual(SearchFilters().file_size_ranges, ())

    def test_existing_bodies_are_unchanged_without_size(self) -> None:
        f = parse_search_filters(file_ext=["TXT"], topic="음식·요리", tag=["김치"], modality="image",
                                 created_from="2026-09-01")
        self.assertEqual(f.file_size_ranges, ())
        self.assertEqual(filters_to_opensearch_bool(f), [
            {"terms": {"filter_kw.file_ext": ["txt"]}},
            {"range": {"filter_date.created_at": {"gte": "2026-09-01"}}},
            {"terms": {"modality": ["image"]}},
            {"terms": {"topics": ["음식·요리"]}},
            {"terms": {"keywords_norm": ["김치"]}},
        ])
        self.assertIsNone(parse_search_filters(file_size_ranges=None))
        self.assertIsNone(parse_search_filters(file_size_ranges=[]))

    def test_size_alone_is_a_filter(self) -> None:
        f = parse_search_filters(file_size_ranges=[(None, MB)])
        self.assertIsNotNone(f)
        self.assertEqual(f.file_size_ranges, ((None, MB),))

    def test_single_range_clause_is_gte_lt(self) -> None:
        """경계는 이상 · 미만 — 크기 칩을 세는 range 집계(from 포함 · to 미포함)와 같아야 칩 = 클릭 결과다."""
        cases = [((None, MB), {"lt": MB}), ((MB, 10 * MB), {"gte": MB, "lt": 10 * MB}),
                 ((10 * MB, None), {"gte": 10 * MB})]
        for rng, want in cases:
            with self.subTest(rng=rng):
                f = parse_search_filters(file_size_ranges=[rng])
                self.assertEqual(filters_to_opensearch_bool(f), [{"range": {"file_size": want}}])

    def test_several_ranges_are_or(self) -> None:
        f = parse_search_filters(file_size_ranges=[(10 * MB, None), (None, MB)])
        self.assertEqual(filters_to_opensearch_bool(f), [{"bool": {"should": [
            {"range": {"file_size": {"lt": MB}}},
            {"range": {"file_size": {"gte": 10 * MB}}},
        ], "minimum_should_match": 1}}])

    def test_ranges_are_deduped_and_sorted(self) -> None:
        """같은 조건이면 같은 바디 — 고른 순서 · 중복이 달라도 결과가 같다(결정적 · 캐시 친화)."""
        a = parse_search_filters(file_size_ranges=[(MB, 10 * MB), (None, MB), (MB, 10 * MB)])
        b = parse_search_filters(file_size_ranges=[(None, MB), (MB, 10 * MB)])
        self.assertEqual(a.file_size_ranges, ((None, MB), (MB, 10 * MB)))
        self.assertEqual(a, b)
        self.assertEqual(filters_to_opensearch_bool(a), filters_to_opensearch_bool(b))

    def test_invalid_ranges_raise(self) -> None:
        for bad in ([(-1, MB)], [(MB, MB)], [(10 * MB, MB)], [(None, None)], [(1.5, MB)], [("1", MB)],
                    [(True, MB)], [(None, MB, 3)], [MB], [(0, 2**63)], [(None, 2**63)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_search_filters(file_size_ranges=bad)

    def test_boundaries_zero_and_long_max(self) -> None:
        """0 은 허용한다 — ``(0, None)`` 은 「크기 값이 있는 문서 전부」(값 없는 문서는 빠진다). 색인 long 의 끝값까지
        받는다. 열린 아래끝(None)은 0 보다 앞에 선다(같은 조건이면 같은 바디)."""
        f = parse_search_filters(file_size_ranges=[(0, None)])
        self.assertEqual(filters_to_opensearch_bool(f), [{"range": {"file_size": {"gte": 0}}}])
        top = parse_search_filters(file_size_ranges=[(None, 2**63 - 1)])
        self.assertEqual(filters_to_opensearch_bool(top), [{"range": {"file_size": {"lt": 2**63 - 1}}}])
        order = parse_search_filters(file_size_ranges=[(0, 5), (None, 5), (0, 1)])
        self.assertEqual(order.file_size_ranges, ((None, 5), (0, 1), (0, 5)))

    def test_size_clause_comes_last_and_survives_replace(self) -> None:
        """크기 절은 기존 절들 뒤에 붙는다(기존 순서 불변). 다른 축을 ``replace`` 로 빼도 크기 조건은 남는다
        (백엔드 칩 계산이 쓰는 방식) — 크기 칩은 ``replace(filters, file_size_ranges=())`` 로 자기 조건을 뺀다."""
        import dataclasses

        f = parse_search_filters(file_ext=["jpg"], file_size_ranges=[(MB, None)])
        clauses = filters_to_opensearch_bool(f)
        self.assertEqual(clauses[-1], {"range": {"file_size": {"gte": MB}}})
        no_ext = dataclasses.replace(f, file_exts=())
        self.assertEqual(filters_to_opensearch_bool(no_ext), [{"range": {"file_size": {"gte": MB}}}])
        no_size = dataclasses.replace(f, file_size_ranges=())
        self.assertEqual(filters_to_opensearch_bool(no_size), [{"terms": {"filter_kw.file_ext": ["jpg"]}}])


class AppliedDateBoundsTest(unittest.TestCase):
    """되돌림 값은 검색 절에 **실제로 들어가는 값**과 같아야 한다(리뷰 2026-09-09)."""

    def test_same_as_the_clause(self) -> None:
        from src.search.search_filters import applied_date_bounds

        f = parse_search_filters(created_from="2026-01-01T15:30:00+09:00", created_to="2026-02-01")
        assert f is not None
        bounds = applied_date_bounds(f)
        clauses = filters_to_opensearch_bool(f)
        rng = [c["range"]["filter_date.created_at"] for c in clauses if "range" in c]
        self.assertEqual(bounds, (rng[0]["gte"], rng[1]["lte"]))
        self.assertEqual(bounds[0], "2026-01-01", "시각은 버리고 날짜만 — 절과 같은 계산")

    def test_none_when_unset(self) -> None:
        from src.search.search_filters import applied_date_bounds

        self.assertEqual(applied_date_bounds(None), (None, None))
        f = parse_search_filters(created_from="   ", topic="요리")
        assert f is not None
        self.assertEqual(applied_date_bounds(f), (None, None), "공백은 필터가 아니다")


if __name__ == "__main__":
    unittest.main()
