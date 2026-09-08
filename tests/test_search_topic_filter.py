"""056 G6 — 주제 필터(topic/subtopic terms) 단위 테스트 (FR-503).

전략
    ``SearchFilters`` 의 ``topics``/``subtopics`` 필드와 ``parse_search_filters`` 파라미터,
    그리고 ``filters_to_opensearch_bool`` → OS ``terms`` 절 변환, ``build_bm25_body``/
    ``build_knn_body`` 의 bool ``filter`` 절 삽입을 순수 단위로 단언한다(DB·OS·LLM 0).

    **철회 반영(2026-07-02)**: topics_text(BM25 보강) 필드는 스코프 철회 — 주제는 keyword
    ``terms`` **필터**로만 검색에 반영한다. terms 필터는 결정적이라 랭킹에 영향이 없다. 따라서
    본 테스트는 ``topics_text^boost``/``cross_fields`` 합류를 단언하지 않고, 오히려 **should
    (랭킹) 절이 topic 필터 유무와 무관하게 동일**함을 단언해 랭킹 무영향을 봉인한다.

    **복수화(096)**: 주제·하위주제는 여럿을 받는다(같은 칸에서 여럿 = 「또는」). 종전 단일 이름
    ``topic``/``subtopic`` 은 첫 값을 주는 읽기 전용 속성으로 남아 있어 소비 코드가 깨지지 않는다.
"""
from __future__ import annotations

import unittest

from src.search.opensearch_search import build_bm25_body, build_knn_body
from src.search.search_filters import (
    SearchFilters,
    filters_to_opensearch_bool,
    parse_search_filters,
)


class ParseTopicFilterTest(unittest.TestCase):
    """parse_search_filters(topic=…/subtopic=…) → SearchFilters.topic/subtopic."""

    def test_topic_only(self) -> None:
        sf = parse_search_filters(topic="요리")
        assert sf is not None
        self.assertEqual(sf.topics, ("요리",))
        self.assertEqual(sf.subtopics, ())
        self.assertEqual(sf.topic, "요리")  # 낡은 이름도 아직 읽힌다(096 하위호환)

    def test_subtopic_only(self) -> None:
        sf = parse_search_filters(subtopic="제빵")
        assert sf is not None
        self.assertEqual(sf.subtopics, ("제빵",))
        self.assertEqual(sf.topics, ())
        self.assertIsNone(sf.topic)

    def test_topic_and_subtopic(self) -> None:
        sf = parse_search_filters(topic="요리", subtopic="제빵")
        assert sf is not None
        self.assertEqual(sf.topics, ("요리",))
        self.assertEqual(sf.subtopics, ("제빵",))

    def test_blank_topic_is_ignored(self) -> None:
        # 공백만인 topic 은 필터 비활성 → 다른 필터도 없으면 None.
        self.assertIsNone(parse_search_filters(topic="   "))

    def test_topic_not_casefolded(self) -> None:
        # 주제는 색인된 keyword 원문과 정확 일치해야 하므로 casefold/정규화하지 않는다(strip 만).
        sf = parse_search_filters(topic="  무선충전  ")
        assert sf is not None
        self.assertEqual(sf.topics, ("무선충전",))


class TopicFilterToOpensearchTest(unittest.TestCase):
    """filters_to_opensearch_bool → topics/subtopics terms 절."""

    def test_topic_terms_clause(self) -> None:
        clauses = filters_to_opensearch_bool(SearchFilters(topics=("요리",)))
        self.assertIn({"terms": {"topics": ["요리"]}}, clauses)

    def test_subtopic_terms_clause(self) -> None:
        clauses = filters_to_opensearch_bool(SearchFilters(subtopics=("제빵",)))
        self.assertIn({"terms": {"subtopics": ["제빵"]}}, clauses)

    def test_topic_and_subtopic_both_present(self) -> None:
        clauses = filters_to_opensearch_bool(SearchFilters(topics=("요리",), subtopics=("제빵",)))
        self.assertIn({"terms": {"topics": ["요리"]}}, clauses)
        self.assertIn({"terms": {"subtopics": ["제빵"]}}, clauses)

    def test_no_topic_no_clause(self) -> None:
        self.assertEqual(filters_to_opensearch_bool(SearchFilters()), [])


class BuildBodyTopicFilterTest(unittest.TestCase):
    """build_bm25_body/build_knn_body 가 topic 필터를 bool.filter 절에 넣는다(랭킹 무영향)."""

    def test_bm25_topic_in_filter_clause(self) -> None:
        body = build_bm25_body(
            "레시피", modality_values=["text"], k=10,
            search_filters=SearchFilters(topics=("요리",)),
        )
        filters = body["query"]["bool"]["filter"]
        self.assertIn({"terms": {"topics": ["요리"]}}, filters)

    def test_bm25_subtopic_in_filter_clause(self) -> None:
        body = build_bm25_body(
            "레시피", modality_values=["text"], k=10,
            search_filters=SearchFilters(subtopics=("제빵",)),
        )
        filters = body["query"]["bool"]["filter"]
        self.assertIn({"terms": {"subtopics": ["제빵"]}}, filters)

    def test_bm25_topic_does_not_change_ranking(self) -> None:
        # 결정적 filter 라 랭킹(should) 절은 topic 필터 유무와 무관하게 동일해야 한다(철회·무영향).
        base = build_bm25_body("레시피", modality_values=["text"], k=10)
        with_topic = build_bm25_body(
            "레시피", modality_values=["text"], k=10,
            search_filters=SearchFilters(topics=("요리",)),
        )
        self.assertEqual(
            with_topic["query"]["bool"]["should"], base["query"]["bool"]["should"]
        )

    def test_knn_topic_in_native_filter(self) -> None:
        body = build_knn_body(
            [0.1, 0.2, 0.3], modality_values=["text"], k=10,
            search_filters=SearchFilters(topics=("요리",)),
        )
        native_filter = body["query"]["knn"]["embedding"]["filter"]["bool"]["filter"]
        self.assertIn({"terms": {"topics": ["요리"]}}, native_filter)


class MultiTopicFilterTest(unittest.TestCase):
    """096 복수 선택 — 같은 칸에서 여럿 고르면 「또는」이다.

    비유하면 옷 가게에서 "빨강 **또는** 파랑" 을 고른 것이다 — 둘 중 하나면 남는다. 반면 서로 다른
    칸(주제 + 확장자)은 「그리고」다 — 둘 다 맞아야 남는다. 이 규칙은 태그 필터(083)와 같다.
    """

    def test_parse_accepts_a_list(self) -> None:
        sf = parse_search_filters(topic=["음악", "미술"], subtopic=["가수", "화가"])
        assert sf is not None
        self.assertEqual(sf.topics, ("음악", "미술"))
        self.assertEqual(sf.subtopics, ("가수", "화가"))

    def test_parse_keeps_first_seen_order(self) -> None:
        # set 을 쓰면 순서가 흔들려 같은 요청이 다른 절을 만든다 — 결정성 요구사항 위반.
        for _ in range(20):
            sf = parse_search_filters(topic=["체육", "음악", "미술"])
            assert sf is not None
            self.assertEqual(sf.topics, ("체육", "음악", "미술"))

    def test_parse_drops_duplicates_and_blanks(self) -> None:
        sf = parse_search_filters(topic=["음악", " 음악 ", "", "   ", "미술"])
        assert sf is not None
        self.assertEqual(sf.topics, ("음악", "미술"))

    def test_all_blank_list_is_no_filter(self) -> None:
        self.assertIsNone(parse_search_filters(topic=["", "  "], subtopic=[]))

    def test_terms_clause_carries_every_value(self) -> None:
        sf = parse_search_filters(topic=["음악", "미술"], subtopic=["가수"])
        assert sf is not None
        clauses = filters_to_opensearch_bool(sf)
        self.assertIn({"terms": {"topics": ["음악", "미술"]}}, clauses)
        self.assertIn({"terms": {"subtopics": ["가수"]}}, clauses)

    def test_legacy_names_read_the_first_value(self) -> None:
        sf = parse_search_filters(topic=["음악", "미술"])
        assert sf is not None
        self.assertEqual(sf.topic, "음악")
        self.assertIsNone(sf.subtopic)

    def test_ranking_still_unaffected(self) -> None:
        # 056 의 봉인을 복수 선택에서도 유지한다 — 필터는 랭킹(should)을 건드리지 않는다.
        sf = parse_search_filters(topic=["음악", "미술"])
        with_filter = build_bm25_body("질의", modality_values=["text"], k=10, search_filters=sf)
        without = build_bm25_body("질의", modality_values=["text"], k=10)
        self.assertEqual(with_filter["query"]["bool"]["should"],
                         without["query"]["bool"]["should"])


if __name__ == "__main__":
    unittest.main()
