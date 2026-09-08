"""096 — 파일 검색 조회(`src.search.file_search`) 단위 테스트(가짜 클라이언트 · 실 IO 0).

무엇을 봉인하나:
① **세는 대상이 필터가 걸리는 대상과 같다** — 집계 질의에 벡터가 없고, 순위 질의의 벡터 쪽에도 같은
   단어·조건 필터가 걸린다. 이게 "적힌 숫자 = 누르면 나오는 수"의 구조적 근거다(실측 410건 불일치 0).
② **정렬·개수가 결정적이다** — 칩은 건수 내림차순 → 키 오름차순으로 못 박고, 개수 상한을 넘으면
   "이 수 이상"임을 응답이 밝힌다.
③ **태그 라벨을 대표 문서에서 되찾는다** — 색인에는 정규화 키만 있어 `전통 음식` 이 `전통음식` 으로
   저장돼 있다. 정규화 규칙은 코어 한 곳뿐이라 색인·필터·표시가 갈라지지 않는다.
④ **범위를 넘는 페이지는 예외** — 순위를 매기지 않은 구간을 빈 페이지로 주면 "끝"과 구분되지 않는다.
"""

from __future__ import annotations

import unittest

from src.search.file_search import (
    FACET_FIELDS,
    RANK_DEPTH_DEFAULT,
    TOTAL_CAP_DEFAULT,
    build_facet_body,
    build_rank_body,
    search_files,
)
from src.search.search_filters import parse_search_filters


class _FakeClient:
    """``search`` 호출을 기록하고 준비된 응답을 순서대로 돌려준다."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def search(self, *, index: str, body: dict, params: dict | None = None) -> dict:
        self.calls.append({"index": index, "body": body, "params": params or {}})
        return self._responses.pop(0)


def _hit(asset_id: str, score: float, **src) -> dict:
    base = {"asset_id": asset_id, "modality": "text", "fs_uri": f"/x/{asset_id}.txt",
            "file_name": f"{asset_id}.txt", "summary": "요약", "keywords": ["김치"]}
    base.update(src)
    return {"_score": score, "_source": base}


def _rank_resp(hits: list[dict]) -> dict:
    return {"hits": {"hits": hits}}


def _facet_resp(total: int, *, relation: str = "eq", aggs: dict | None = None) -> dict:
    return {"hits": {"total": {"value": total, "relation": relation}},
            "aggregations": aggs or {}}


def _terms(buckets: list[tuple[str, int]]) -> dict:
    return {"buckets": [{"key": k, "doc_count": n} for k, n in buckets]}


class TestScope(unittest.TestCase):
    """세는 대상 = 필터가 걸리는 대상."""

    def test_facet_body_has_no_vector(self) -> None:
        # 벡터를 넣으면 뜻으로만 가까운 문서까지 세어져 클릭 결과와 어긋난다(실제로 겪은 결함).
        body = build_facet_body("김치")
        self.assertNotIn("knn", str(body))
        self.assertEqual(body["size"], 0)
        self.assertEqual(body["query"]["bool"]["must"][0]["multi_match"]["query"], "김치")

    def test_rank_body_filters_the_vector_side_too(self) -> None:
        body = build_rank_body("김치", [0.1, 0.2])
        queries = body["query"]["hybrid"]["queries"]
        knn_filter = queries[1]["knn"]["embedding"]["filter"]["bool"]["filter"]
        # 벡터 쪽 필터에 **단어 절**이 들어 있어야 집합이 넓어지지 않는다.
        self.assertIn({"multi_match": {"query": "김치", "fields": list(body_fields := [
            "file_name^2", "summary", "keywords"])}}, knn_filter)
        self.assertEqual(queries[0]["bool"]["must"][0]["multi_match"]["fields"], body_fields)

    def test_conditions_reach_both_sides(self) -> None:
        f = parse_search_filters(topic="음식·요리", tag=["김치"], file_ext=["txt"])
        body = build_rank_body("김치", [0.0], filters=f)
        queries = body["query"]["hybrid"]["queries"]
        word_side = queries[0]["bool"]["filter"]
        vector_side = queries[1]["knn"]["embedding"]["filter"]["bool"]["filter"]
        self.assertTrue(word_side, "단어 쪽에 조건이 없다")
        for clause in word_side:
            self.assertIn(clause, vector_side, "조건이 벡터 쪽에 빠졌다")
        facet = build_facet_body("김치", filters=f)
        self.assertEqual(facet["query"]["bool"]["filter"], word_side)

    def test_rank_body_does_not_count(self) -> None:
        # 개수는 집계 질의가 센다 — 순위 질의가 또 세면 같은 일을 두 번 한다.
        self.assertIs(build_rank_body("김치", [0.0])["track_total_hits"], False)


class TestDeterminism(unittest.TestCase):
    """정렬·상한이 못 박혀 있다."""

    def test_facet_order_is_pinned(self) -> None:
        body = build_facet_body("김치")
        for axis, field in FACET_FIELDS.items():
            terms = body["aggs"][axis]["terms"]
            self.assertEqual(terms["field"], field)
            self.assertEqual(terms["order"], [{"_count": "desc"}, {"_key": "asc"}])

    def test_only_tag_axis_asks_for_a_sample(self) -> None:
        body = build_facet_body("김치")
        self.assertIn("sample", body["aggs"]["tag"]["aggs"])
        self.assertNotIn("aggs", body["aggs"]["topic"])

    def test_total_cap_is_bound(self) -> None:
        self.assertEqual(build_facet_body("김치")["track_total_hits"], TOTAL_CAP_DEFAULT)
        self.assertEqual(build_facet_body("김치", total_cap=5)["track_total_hits"], 5)

    def test_bad_axis_or_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_facet_body("김치", axes=("없는축",))
        with self.assertRaises(ValueError):
            build_facet_body("김치", total_cap=0)
        with self.assertRaises(ValueError):
            build_facet_body("김치", facet_size=0)


class TestSearchFiles(unittest.TestCase):
    """조회 조립 — 두 질의를 보내고 응답을 화면 모양으로."""

    def _run(self, *, total: int = 3, relation: str = "eq", aggs: dict | None = None, **kw):
        client = _FakeClient([
            _facet_resp(total, relation=relation, aggs=aggs),
            _rank_resp([_hit("a1", 1.0), _hit("a2", 0.5, keywords=["전통 음식"])]),
        ])
        return client, search_files(client, "assets", query="김치", query_vector=[0.0], **kw)

    def test_counts_first_then_ranks(self) -> None:
        # 개수를 먼저 센다 — 순위 질의는 결과 끝을 넘는 페이지에 오류를 내므로 총계를 알고 물어야 한다.
        client, _out = self._run()
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["body"]["size"], 0)  # 집계 질의가 먼저
        self.assertNotIn("search_pipeline", client.calls[0]["params"])
        # 순위 질의에만 정규화 파이프라인을 붙인다(집계에 붙이면 뜻 없는 비용이다).
        self.assertIn("search_pipeline", client.calls[1]["params"])

    def test_rows_and_total(self) -> None:
        _client, out = self._run(total=24)
        self.assertEqual([r["asset_id"] for r in out["rows"]], ["a1", "a2"])
        self.assertEqual(out["rows"][0]["score"], 1.0)
        self.assertEqual(out["rows"][0]["file_name"], "a1.txt")
        self.assertEqual((out["total"], out["total_capped"]), (24, False))

    def test_capped_total_is_flagged(self) -> None:
        _client, out = self._run(total=10_000, relation="gte")
        self.assertEqual(out["total"], 10_000)
        self.assertIs(out["total_capped"], True)

    def test_tag_label_comes_from_the_sample_doc(self) -> None:
        aggs = {
            "topic": _terms([("음식·요리", 23)]),
            "subtopic": _terms([]),
            # 색인 키는 붙여쓴 형태다 — 원문은 대표 문서에서 되찾는다.
            "tag": {"buckets": [{
                "key": "전통음식", "doc_count": 6,
                "sample": {"hits": {"hits": [{"_source": {"keywords": ["전통 음식", "김치"]}}]}},
            }]},
        }
        _client, out = self._run(aggs=aggs)
        self.assertEqual(out["facets"]["topic"],
                         [{"key": "음식·요리", "label": "음식·요리", "count": 23}])
        self.assertEqual(out["facets"]["tag"],
                         [{"key": "전통음식", "label": "전통 음식", "count": 6}])
        self.assertEqual(out["facets"]["subtopic"], [])

    def test_tag_label_falls_back_to_key(self) -> None:
        # 대표 문서에서 되찾지 못해도 키로 보인다 — 누르면 서버가 다시 정규화하므로 동작은 한다.
        aggs = {"tag": {"buckets": [{"key": "김치", "doc_count": 5,
                                     "sample": {"hits": {"hits": []}}}]},
                "topic": _terms([]), "subtopic": _terms([])}
        _client, out = self._run(aggs=aggs)
        self.assertEqual(out["facets"]["tag"], [{"key": "김치", "label": "김치", "count": 5}])

    def test_paging_is_passed_through(self) -> None:
        client, out = self._run(total=500, from_=40, size=20)
        self.assertEqual((client.calls[1]["body"]["from"], client.calls[1]["body"]["size"]), (40, 20))
        self.assertEqual((out["from"], out["size"]), (40, 20))

    def test_page_past_the_end_is_empty_not_an_error(self) -> None:
        # 끝을 지난 페이지는 오류가 아니다 — 빈 행 + 정상 총계를 준다(순위 질의는 아예 보내지 않는다).
        client = _FakeClient([_facet_resp(30, aggs={"topic": _terms([("음악", 30)])})])
        out = search_files(client, "assets", query="김치", query_vector=[0.0], from_=50, size=10)
        self.assertEqual(out["rows"], [])
        self.assertEqual((out["total"], out["from"]), (30, 50))
        self.assertEqual(len(client.calls), 1, "끝을 지났는데 순위를 물었다")
        self.assertEqual(out["facets"]["topic"][0]["count"], 30)  # 칩은 그대로 준다

    def test_last_page_asks_only_for_what_remains(self) -> None:
        # 남은 것보다 더 달라고 하면 엔진이 "끝을 지났다"며 오류를 낸다 — 남은 만큼만 청한다.
        client = _FakeClient([_facet_resp(45), _rank_resp([_hit("a1", 1.0)])])
        search_files(client, "assets", query="김치", query_vector=[0.0], from_=40, size=20)
        self.assertEqual(client.calls[1]["body"]["size"], 5)

    def test_beyond_rank_depth_is_rejected(self) -> None:
        # 순위를 매기지 않은 구간이다 — 빈 페이지를 주면 "끝"과 구분되지 않는다.
        with self.assertRaises(ValueError):
            search_files(_FakeClient([]), "assets", query="김치", query_vector=[0.0],
                         from_=RANK_DEPTH_DEFAULT, size=10)

    def test_empty_query_is_rejected(self) -> None:
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                search_files(_FakeClient([]), "assets", query=bad, query_vector=[0.0])

    def test_bad_page_range_is_rejected(self) -> None:
        for kw in ({"size": 0}, {"from_": -1}):
            with self.assertRaises(ValueError):
                search_files(_FakeClient([]), "assets", query="김치", query_vector=[0.0], **kw)


if __name__ == "__main__":
    unittest.main()
