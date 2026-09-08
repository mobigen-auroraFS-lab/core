"""083 T105 — 태그 필터(``SearchFilters.tags`` → ``keywords_norm`` terms) 단위 테스트.

무엇을 봉인하는가
    (1) **파싱**: 반복 파라미터 ``tag`` 가 ``SearchFilters.tags`` 로 들어오고, 🔴 **tags 만 지정된
        요청이 ``None`` 으로 무시되지 않는다**(``parse_search_filters`` 의 "아무 필터도 없음" 판정에
        tags 를 빼먹으면 태그 단독 필터가 통째로 사라진다 — 083 tasks T105 의 붉은 함정).
    (2) **절 변환**: 태그는 색인 키(``keywords_norm``)와 **같은 정규화 함수**를 거친 뒤 한 ``terms``
        절에 담긴다(terms = 태그 간 OR). 빈 키는 버린다.
    (3) 🔴 **동작 불변**: 태그를 지정하지 않으면 절 자체가 생기지 않아 **기존 질의 바디와 완전히
        동일**하다(태그 기능 추가가 기존 검색을 건드리지 않는다는 증거 · SC-04 의 코드측 근거).

전부 순수 단위다 — DB·OS·LLM 을 부르지 않는다.
"""
from __future__ import annotations

import json
import unittest
from datetime import date

from src.search.opensearch_search import build_bm25_body, build_knn_body
from src.search.search_filters import (
    SearchFilters,
    filters_to_opensearch_bool,
    parse_search_filters,
)


class ParseTagFilterTest(unittest.TestCase):
    """parse_search_filters(tag=[…]) → SearchFilters.tags(원문 보존·순서 보존)."""

    def test_tag_only_is_not_none(self) -> None:
        # 🔴 핵심 봉인: 태그만 준 요청이 "필터 없음"(None)으로 삼켜지면 태그 필터가 무력화된다.
        sf = parse_search_filters(tag=["전통음식"])
        self.assertIsNotNone(sf)
        assert sf is not None
        self.assertEqual(sf.tags, ("전통음식",))

    def test_default_is_empty_tuple(self) -> None:
        # 하위호환: 태그를 안 주면 빈 튜플(기존 호출부는 그대로 동작).
        sf = parse_search_filters(file_ext=["txt"])
        assert sf is not None
        self.assertEqual(sf.tags, ())

    def test_multiple_tags_keep_input_order(self) -> None:
        sf = parse_search_filters(tag=["자연", "검은색"])
        assert sf is not None
        self.assertEqual(sf.tags, ("자연", "검은색"))

    def test_blank_tags_ignored(self) -> None:
        # 공백뿐인 태그는 필터가 아니다 → 다른 필터도 없으면 None(= 필터 없음).
        self.assertIsNone(parse_search_filters(tag=["  ", ""]))

    def test_original_notation_preserved_not_normalized(self) -> None:
        # 정규화는 **절을 만들 때** 한다. 여기서는 앞뒤 공백만 잘라 원문 표기를 보존한다 —
        # 서비스가 사용자에게 "선택한 태그"를 되돌려 보여줄 수 있어야 하기 때문이다.
        sf = parse_search_filters(tag=["  전통 음식  "])
        assert sf is not None
        self.assertEqual(sf.tags, ("전통 음식",))

    def test_duplicate_tags_deduped_in_order(self) -> None:
        sf = parse_search_filters(tag=["자연", "자연", "숲"])
        assert sf is not None
        self.assertEqual(sf.tags, ("자연", "숲"))

    def test_tag_with_other_filters(self) -> None:
        sf = parse_search_filters(tag=["자연"], file_ext=["jpg"], topic="여행")
        assert sf is not None
        self.assertEqual(sf.tags, ("자연",))
        self.assertEqual(sf.file_exts, ("jpg",))
        self.assertEqual(sf.topic, "여행")


class TagFilterToOpensearchTest(unittest.TestCase):
    """filters_to_opensearch_bool → keywords_norm terms 절(정규화·OR·빈 키 드롭)."""

    def test_single_tag_terms_clause_uses_normalized_key(self) -> None:
        # 색인(asset_to_doc)이 넣은 키와 같은 규칙(공백 제거·casefold)으로 맞춘다.
        clauses = filters_to_opensearch_bool(SearchFilters(tags=("전통 음식",)))
        self.assertIn({"terms": {"keywords_norm": ["전통음식"]}}, clauses)

    def test_multiple_tags_are_or_in_one_terms(self) -> None:
        # 태그 여럿은 **한 terms 절**(= OR). 값은 정렬해 같은 선택이면 항상 같은 바디가 되게 한다.
        clauses = filters_to_opensearch_bool(SearchFilters(tags=("자연", "검은색")))
        self.assertEqual(clauses, [{"terms": {"keywords_norm": ["검은색", "자연"]}}])

    def test_tag_order_does_not_change_clause(self) -> None:
        # 사용자가 고른 순서가 달라도 질의 바디는 동일(OR 이라 결과도 같다 · 결정적·캐시 친화).
        a = filters_to_opensearch_bool(SearchFilters(tags=("자연", "검은색")))
        b = filters_to_opensearch_bool(SearchFilters(tags=("검은색", "자연")))
        self.assertEqual(a, b)

    def test_notation_variants_merge_into_one_key(self) -> None:
        # '전통 음식'·'전통음식' 은 한 키 → terms 값 1개(중복 없음).
        clauses = filters_to_opensearch_bool(SearchFilters(tags=("전통 음식", "전통음식")))
        self.assertEqual(clauses, [{"terms": {"keywords_norm": ["전통음식"]}}])

    def test_blank_key_dropped_and_clause_omitted(self) -> None:
        # 정규화 후 빈 키만 남으면 절 자체를 만들지 않는다(모든 문서를 배제하는 빈 terms 방지).
        self.assertEqual(filters_to_opensearch_bool(SearchFilters(tags=("  ", "　"))), [])

    def test_blank_key_dropped_but_valid_kept(self) -> None:
        clauses = filters_to_opensearch_bool(SearchFilters(tags=("  ", "자연")))
        self.assertEqual(clauses, [{"terms": {"keywords_norm": ["자연"]}}])

    def test_no_tag_no_clause_bodies_identical(self) -> None:
        # 🔴 동작 불변: 태그 미지정이면 절 목록이 태그 도입 전과 완전히 같다.
        self.assertEqual(filters_to_opensearch_bool(SearchFilters()), [])
        self.assertEqual(
            filters_to_opensearch_bool(
                SearchFilters(file_exts=("txt",), created_from=date(2026, 1, 1), topics=("요리",))
            ),
            [
                {"terms": {"filter_kw.file_ext": ["txt"]}},
                {"range": {"filter_date.created_at": {"gte": "2026-01-01"}}},
                {"terms": {"topics": ["요리"]}},
            ],
        )


class BuildBodyTagFilterTest(unittest.TestCase):
    """build_bm25_body/build_knn_body 에 태그 필터가 bool.filter 로 실린다(랭킹 무영향)."""

    def test_bm25_tag_in_filter_clause(self) -> None:
        body = build_bm25_body(
            "한식", modality_values=["text"], k=10,
            search_filters=SearchFilters(tags=("전통 음식",)),
        )
        self.assertIn(
            {"terms": {"keywords_norm": ["전통음식"]}}, body["query"]["bool"]["filter"]
        )

    def test_knn_tag_in_native_filter(self) -> None:
        body = build_knn_body(
            [0.1, 0.2, 0.3], modality_values=["text"], k=10,
            search_filters=SearchFilters(tags=("전통 음식",)),
        )
        native_filter = body["query"]["knn"]["embedding"]["filter"]["bool"]["filter"]
        self.assertIn({"terms": {"keywords_norm": ["전통음식"]}}, native_filter)

    def test_tag_filter_does_not_change_ranking_clauses(self) -> None:
        # filter 절은 점수에 기여하지 않는다 — should(랭킹) 절이 태그 유무와 무관하게 동일.
        base = build_bm25_body("한식", modality_values=["text"], k=10)
        with_tag = build_bm25_body(
            "한식", modality_values=["text"], k=10,
            search_filters=SearchFilters(tags=("전통 음식",)),
        )
        self.assertEqual(with_tag["query"]["bool"]["should"], base["query"]["bool"]["should"])

    def test_bodies_untouched_when_no_tag(self) -> None:
        # 🔴 동작 불변의 직접 증거: 태그를 안 쓰는 질의 바디에는 keywords_norm 이 **어디에도** 없다.
        bm25 = build_bm25_body(
            "한식", modality_values=["text"], k=10,
            search_filters=SearchFilters(file_exts=("txt",)),
        )
        knn = build_knn_body(
            [0.1, 0.2], modality_values=["text"], k=10,
            search_filters=SearchFilters(file_exts=("txt",)),
        )
        for body in (bm25, knn):
            self.assertNotIn("keywords_norm", json.dumps(body, ensure_ascii=False))

    def test_empty_filters_body_equals_none_filters_body(self) -> None:
        # 빈 SearchFilters 는 필터 없음(None)과 같은 바디 — tags 필드 추가로도 변하지 않는다.
        self.assertEqual(
            build_bm25_body("한식", modality_values=["text"], k=10,
                            search_filters=SearchFilters()),
            build_bm25_body("한식", modality_values=["text"], k=10, search_filters=None),
        )


class TagKeyRoundTripTest(unittest.TestCase):
    """🔴 세 지점의 키가 **한 값으로 만난다**: 색인(asset_to_doc) · 집계(패싯) · 필터(terms).

    왜 이 테스트가 필요한가: 태그 기능은 세 곳에서 같은 정규화를 거쳐야 성립한다. 한 곳만
    규칙이 갈라지면 "화면에 12건이라고 떠 있는 태그를 눌렀는데 0건"이 된다(083 SC-01·SC-02).
    각 지점을 따로 단언하는 위 테스트들과 달리, 여기서는 **화면 라벨을 그대로 필터로 되돌려**
    색인된 키와 만나는지를 한 흐름으로 확인한다.
    """

    def test_index_key_equals_filter_key_for_displayed_label(self) -> None:
        from src.search.opensearch_sync import asset_to_doc
        from src.search.tag_facets import aggregate_tag_facets

        # ① 색인: 표기가 다른 두 자산이 같은 키로 색인된다.
        docs = [
            asset_to_doc(
                {
                    "asset_id": f"a{i}", "modality": "text", "domain_label": "general",
                    "fs_path": f"/data/{i}.txt",
                    "ext_meta": {"summary": "한식", "keywords": [kw]},
                    "emb": "[0.1,0.2]",
                },
                channel="st",
            )
            for i, kw in enumerate(("전통 음식", "전통음식"))
        ]
        self.assertEqual([d["keywords_norm"] for d in docs], [["전통음식"], ["전통음식"]])

        # ② 집계: 두 자산이 한 항목 2건으로 합쳐지고 화면 라벨이 하나 정해진다.
        rows = [{"tags": d["keywords"]} for d in docs]
        facets = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(len(facets["items"]), 1)
        self.assertEqual(facets["items"][0]["count"], 2)
        label = facets["items"][0]["label"]

        # ③ 필터: 그 라벨을 그대로 태그 필터로 되돌리면 ①의 색인 키와 일치한다(클릭이 헛돌지 않음).
        clauses = filters_to_opensearch_bool(SearchFilters(tags=(label,)))
        self.assertEqual(clauses, [{"terms": {"keywords_norm": ["전통음식"]}}])
        self.assertEqual(clauses[0]["terms"]["keywords_norm"], docs[0]["keywords_norm"])


if __name__ == "__main__":
    unittest.main()
