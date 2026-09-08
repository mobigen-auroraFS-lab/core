"""096 — 파일 검색 조회(`src.search.file_search`) 단위 테스트(가짜 클라이언트 · 실 IO 0).

무엇을 봉인하나:
① **세는 대상과 보여주는 대상이 같다** — 집계 질의와 순위 질의가 **같은 집합 절**(단어 ∪ 뜻이 임계
   이상, 조건 적용)을 쓴다. 이게 "적힌 숫자 = 누르면 나오는 수"의 구조적 근거다(실측 불일치 0).
② **정렬·개수가 결정적이다** — 칩은 건수 내림차순 → 키 오름차순으로 못 박고, 개수 상한을 넘으면
   "이 수 이상"임을 응답이 밝힌다.
③ **태그 라벨을 대표 문서에서 되찾는다** — 색인에는 정규화 키만 있어 `전통 음식` 이 `전통음식` 으로
   저장돼 있다. 정규화 규칙은 코어 한 곳뿐이라 색인·필터·표시가 갈라지지 않는다.
④ **범위를 넘는 페이지는 예외** — 순위를 매기지 않은 구간을 빈 페이지로 주면 "끝"과 구분되지 않는다.
⑤ **칩은 자기 조건을 뺀 채 센다**(096) — 그래야 고른 뒤에도 다른 값으로 갈아탈 수 있다(087 함정).
⑥ **정렬을 바꿔도 집합은 같다**(096) — 뜻이 집합 판정에 쓰이므로 필드 정렬도 벡터가 필요하다.
   정렬에 따라 개수가 달라지면 화면이 거짓말을 한다.
⑦ **뜻에는 경계가 있다**(096) — 「상위 k개」가 아니라 「코사인 임계 이상」이라 코퍼스에 없는 질의는
   0건이 된다(k 로 청하면 관련 없어도 k 개를 채워 준다 · 실측 컬링 100건).
"""

from __future__ import annotations

import unittest

from src.search.file_search import (
    FACET_FIELDS,
    RANK_DEPTH_DEFAULT,
    SEMANTIC_CAP_DEFAULT,
    SEMANTIC_MIN_COSINE_DEFAULT,
    SORT_DEFAULT,
    SORT_DEPTH_DEFAULT,
    SORT_OPTIONS,
    TOTAL_CAP_DEFAULT,
    WORD_OPERATOR_DEFAULT,
    build_facet_body,
    build_facet_plan,
    build_rank_body,
    build_semantic_body,
    search_files,
)
from src.search.search_filters import parse_search_filters


class _FakeClient:
    """``search``/``msearch`` 호출을 기록하고 준비된 응답을 순서대로 돌려준다.

    묶음 질의(``msearch``)는 준비된 응답에서 **머리줄 수만큼** 꺼내 한 묶음으로 돌려준다 — 실제
    OpenSearch 처럼 계획과 같은 순서로 응답이 온다.
    """

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def search(self, *, index: str, body: dict, params: dict | None = None) -> dict:
        self.calls.append({"index": index, "body": body, "params": params or {}})
        return self._responses.pop(0)

    def msearch(self, *, index: str, body: list, params: dict | None = None) -> dict:
        self.calls.append({"index": index, "msearch": body, "params": params or {}})
        bodies = [x for i, x in enumerate(body) if i % 2 == 1]
        return {"responses": [self._responses.pop(0) for _ in bodies]}


# 가짜 질의 임베딩 — 값은 뜻이 없다(본문 조립만 본다).
VEC: tuple[float, ...] = (0.1, 0.2)
# 뜻으로 걸린 자산 id 대역(값은 뜻이 없다).
SEM: tuple[str, ...] = ("s1", "s2")


def _hit(asset_id: str, score: float, **src) -> dict:
    base = {"asset_id": asset_id, "modality": "text", "fs_uri": f"/x/{asset_id}.txt",
            "file_name": f"{asset_id}.txt", "summary": "요약", "keywords": ["김치"]}
    base.update(src)
    return {"_score": score, "_source": base}


def _rank_resp(hits: list[dict]) -> dict:
    return {"hits": {"hits": hits}}


def _sem_resp(*asset_ids: str) -> dict:
    """의미 id 질의 대역 — 뜻으로 걸린 자산 id 만 돌려준다."""
    return {"hits": {"hits": [{"_source": {"asset_id": a}} for a in asset_ids]}}


def _facet_resp(total: int, *, relation: str = "eq", aggs: dict | None = None) -> dict:
    return {"hits": {"total": {"value": total, "relation": relation}},
            "aggregations": aggs or {}}


def _terms(buckets: list[tuple[str, int]]) -> dict:
    return {"buckets": [{"key": k, "doc_count": n} for k, n in buckets]}


class TestScope(unittest.TestCase):
    """세는 대상 = 필터가 걸리는 대상."""

    def test_both_queries_use_the_same_set(self) -> None:
        # 🔴 세는 질의와 보여주는 질의가 **같은 집합**이어야 한다. 다르면 "적힌 숫자 = 누르면 나오는
        #    수"가 깨진다(2026-09-08 실측 결함이 그것이었다).
        f = parse_search_filters(topic="음식·요리", tag=["김치"], file_ext=["txt"])
        facet = build_facet_body("김치", SEM, filters=f)["query"]["bool"]
        rank = build_rank_body("김치", VEC, semantic_ids=SEM,
                               filters=f)["query"]["hybrid"]["queries"]
        # 순위 질의의 벡터 쪽은 **집합 절 전체**를 필터로 갖는다 → 집합 밖으로 새지 않는다.
        self.assertEqual(rank[1]["knn"]["embedding"]["filter"],
                         build_facet_body("김치", SEM, filters=f)["query"])
        # 단어 절이 같다.
        self.assertEqual(facet["should"][0], rank[0]["bool"]["must"][0])
        # 조건이 양쪽에 같은 것으로 걸린다.
        self.assertTrue(facet["filter"], "집계 질의에 조건이 없다")
        self.assertEqual(facet["filter"], rank[0]["bool"]["filter"])
        self.assertEqual(facet["minimum_should_match"], 1)

    def test_semantic_side_is_a_frozen_id_list(self) -> None:
        # 🔴 뜻으로 걸린 자산은 **id 목록**으로 굳힌다. 조건마다 벡터 검색을 다시 하면 근사 검색이
        #    다른 답을 주어 칩 건수와 클릭 결과가 어긋난다(실측 `등산` 칩 12 대 클릭 13).
        body = build_facet_body("김치", SEM)
        should = body["query"]["bool"]["should"]
        self.assertEqual(should[1], {"terms": {"asset_id": list(SEM)}})
        self.assertNotIn("knn", str(should), "집합 절에 벡터 검색이 남아 있다")
        # 조건이 달라도 뜻 절은 **똑같다** — 그게 칩=클릭의 근거다.
        scoped = build_facet_body("김치", SEM, filters=parse_search_filters(topic="음악"))
        self.assertEqual(scoped["query"]["bool"]["should"][1], should[1])

    def test_no_semantic_hit_means_no_clause(self) -> None:
        # 뜻으로 걸린 것이 없으면 절을 아예 넣지 않는다(빈 terms 는 무의미한 비용이다).
        should = build_facet_body("김치")["query"]["bool"]["should"]
        self.assertEqual(len(should), 1)
        self.assertIn("bool", should[0])

    def test_word_clause_is_the_shared_one(self) -> None:
        # 🔴 단어 절은 멀티모달 검색과 **한 곳에서** 만든다. 각자 만들면 같은 질의가 다른 파일을
        #    찾는다(실측: 파일명 가중치만 달라도 상위 10 중 3건만 겹쳤다).
        from src.search.query_builder import build_word_should

        shared = build_word_should("김치", operator=WORD_OPERATOR_DEFAULT)
        for body in (build_facet_body("김치", VEC)["query"]["bool"]["should"][0],
                     build_rank_body("김치", VEC)["query"]["hybrid"]["queries"][0]["bool"]["must"][0]):
            self.assertEqual(body, {"bool": {"should": shared, "minimum_should_match": 1}})
        # 모든 형태소가 맞아야 한다 — ``or`` 면 「남한산성」이 「산」만 든 파일까지 센다(354건 대 3건).
        self.assertEqual(WORD_OPERATOR_DEFAULT, "and")
        for clause in shared:
            inner = next(iter(clause.values()))
            spec = next(iter(inner.values())) if "term" in clause else inner
            if isinstance(spec, dict) and "operator" in spec:
                self.assertEqual(spec["operator"], "and")

    def test_semantic_query_is_bounded_not_capped_by_k(self) -> None:
        # 🔴 「가까운 순 k개」로 청하면 관련이 없어도 k 개를 채워 준다(실측: 코퍼스에 없는 컬링도
        #    k=100 이면 100건). 그래서 **유사도 하한**으로 청한다 — 그러면 집합에 경계가 생긴다.
        body = build_semantic_body(VEC)
        emb = body["query"]["knn"]["embedding"]
        self.assertNotIn("k", emb, "상위 k개로 청하면 집합에 경계가 없다")
        self.assertAlmostEqual(emb["min_score"], (SEMANTIC_MIN_COSINE_DEFAULT + 1) / 2)
        self.assertEqual(SEMANTIC_MIN_COSINE_DEFAULT, 0.60)
        # 🔴 조건을 걸지 않는다 — 필터 유무로 근사 검색의 답이 달라지기 때문이다.
        self.assertNotIn("filter", emb)
        self.assertEqual(body["_source"], ["asset_id"])
        self.assertEqual(body["size"], SEMANTIC_CAP_DEFAULT)

    def test_rank_body_does_not_count(self) -> None:
        # 개수는 집계 질의가 센다 — 순위 질의가 또 세면 같은 일을 두 번 한다.
        self.assertIs(build_rank_body("김치", [0.0])["track_total_hits"], False)


class TestDeterminism(unittest.TestCase):
    """정렬·상한이 못 박혀 있다."""

    def test_facet_order_is_pinned(self) -> None:
        body = build_facet_body("김치", VEC)
        for axis, field in FACET_FIELDS.items():
            terms = body["aggs"][axis]["terms"]
            self.assertEqual(terms["field"], field)
            self.assertEqual(terms["order"], [{"_count": "desc"}, {"_key": "asc"}])

    def test_only_tag_axis_asks_for_a_sample(self) -> None:
        body = build_facet_body("김치", VEC)
        self.assertIn("sample", body["aggs"]["tag"]["aggs"])
        self.assertNotIn("aggs", body["aggs"]["topic"])

    def test_total_cap_is_bound(self) -> None:
        self.assertEqual(build_facet_body("김치", VEC)["track_total_hits"], TOTAL_CAP_DEFAULT)
        self.assertEqual(build_facet_body("김치", VEC, total_cap=5)["track_total_hits"], 5)

    def test_bad_axis_or_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_facet_body("김치", VEC, axes=("없는축",))
        with self.assertRaises(ValueError):
            build_facet_body("김치", VEC, total_cap=0)
        with self.assertRaises(ValueError):
            build_facet_body("김치", VEC, facet_size=0)


class TestSearchFiles(unittest.TestCase):
    """조회 조립 — 두 질의를 보내고 응답을 화면 모양으로."""

    def _run(self, *, total: int = 3, relation: str = "eq", aggs: dict | None = None,
             semantic: tuple[str, ...] = (), **kw):
        client = _FakeClient([
            _sem_resp(*semantic),
            _facet_resp(total, relation=relation, aggs=aggs),
            _rank_resp([_hit("a1", 1.0), _hit("a2", 0.5, keywords=["전통 음식"])]),
        ])
        return client, search_files(client, "assets", query="김치", query_vector=[0.0], **kw)

    def test_semantic_then_count_then_rank(self) -> None:
        # ① 뜻으로 걸린 id 를 먼저 굳히고 ② 개수를 세고 ③ 그때만 순위를 묻는다.
        #    순위 질의는 결과 끝을 넘는 페이지에 오류를 내므로 총계를 알고 물어야 한다.
        client, _out = self._run(semantic=("s1",))
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0]["body"]["_source"], ["asset_id"])  # 의미 id 질의
        self.assertEqual(client.calls[1]["body"]["size"], 0)                # 집계 질의
        self.assertNotIn("search_pipeline", client.calls[1]["params"])
        # 순위 질의에만 정규화 파이프라인을 붙인다(집계에 붙이면 뜻 없는 비용이다).
        self.assertIn("search_pipeline", client.calls[2]["params"])
        # 굳힌 id 가 집계·순위 질의에 **그대로** 실린다.
        for call in client.calls[1:]:
            self.assertIn('"asset_id": ["s1"]', str(call["body"]).replace("'", '"'))

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
        self.assertEqual((client.calls[2]["body"]["from"], client.calls[2]["body"]["size"]), (40, 20))
        self.assertEqual((out["from"], out["size"]), (40, 20))

    def test_page_past_the_end_is_empty_not_an_error(self) -> None:
        # 끝을 지난 페이지는 오류가 아니다 — 빈 행 + 정상 총계를 준다(순위 질의는 아예 보내지 않는다).
        client = _FakeClient([_sem_resp(),
                              _facet_resp(30, aggs={"topic": _terms([("음악", 30)])})])
        out = search_files(client, "assets", query="김치", query_vector=[0.0], from_=50, size=10)
        self.assertEqual(out["rows"], [])
        self.assertEqual((out["total"], out["from"]), (30, 50))
        self.assertEqual(len(client.calls), 2, "끝을 지났는데 순위를 물었다")
        self.assertEqual(out["facets"]["topic"][0]["count"], 30)  # 칩은 그대로 준다

    def test_last_page_asks_only_for_what_remains(self) -> None:
        # 남은 것보다 더 달라고 하면 엔진이 "끝을 지났다"며 오류를 낸다 — 남은 만큼만 청한다.
        client = _FakeClient([_sem_resp(), _facet_resp(45), _rank_resp([_hit("a1", 1.0)])])
        search_files(client, "assets", query="김치", query_vector=[0.0], from_=40, size=20)
        self.assertEqual(client.calls[2]["body"]["size"], 5)

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


class TestFacetScoping(unittest.TestCase):
    """096 — 칩은 **자기 조건을 뺀 채** 센다(칩으로 갈아탈 수 있어야 한다).

    옷 가게 비유: 「빨강」을 고른 상태에서 색깔 선반에는 파랑·검정이 그대로 보여야 갈아입을 수 있다.
    조건을 전부 적용해 세면 고른 색 하나만 남아 갈아탈 길이 막힌다(087 이 겪은 함정 · 실측 5→1개).
    """

    def _fields_of(self, body: dict) -> list[str]:
        out = []
        for clause in body["query"]["bool"]["filter"]:
            out.extend((clause.get("terms") or clause.get("range") or {}).keys())
        return out

    def test_no_filter_is_one_query(self) -> None:
        plan = build_facet_plan("김치", VEC)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["axes"], ("topic", "subtopic", "tag"))
        self.assertIs(plan[0]["total"], True)

    def test_topic_axis_drops_its_own_filter(self) -> None:
        plan = build_facet_plan("김치", VEC, filters=parse_search_filters(topic=["음악"]))
        self.assertEqual(len(plan), 2)
        # 기본 질의(개수·나머지 축)에는 주제 조건이 그대로 걸린다.
        self.assertEqual(plan[0]["axes"], ("subtopic", "tag"))
        self.assertIn("topics", self._fields_of(plan[0]["body"]))
        # 주제 축은 주제 조건을 뺀 채 센다 → 다른 주제가 칩으로 남는다.
        self.assertEqual(plan[1]["axes"], ("topic",))
        self.assertNotIn("topics", self._fields_of(plan[1]["body"]))
        self.assertIs(plan[1]["total"], False)

    def test_topic_axis_also_drops_subtopic(self) -> None:
        # 하위주제는 주제의 자식이다 — 남겨 두면 「음악 > 가수」에서 주제 축이 다시 음악 하나로 접힌다.
        plan = build_facet_plan("김치", VEC,
                                filters=parse_search_filters(topic=["음악"], subtopic=["가수"]))
        topic_entry = next(e for e in plan if e["axes"] == ("topic",))
        fields = self._fields_of(topic_entry["body"])
        self.assertNotIn("topics", fields)
        self.assertNotIn("subtopics", fields)

    def test_subtopic_axis_keeps_the_topic(self) -> None:
        # 하위주제 칩은 「고른 주제 안에서」 세는 것이 맞다(파고들기).
        plan = build_facet_plan("김치", VEC,
                                filters=parse_search_filters(topic=["음악"], subtopic=["가수"]))
        sub = next(e for e in plan if e["axes"] == ("subtopic",))
        fields = self._fields_of(sub["body"])
        self.assertIn("topics", fields)
        self.assertNotIn("subtopics", fields)

    def test_other_axis_conditions_stay(self) -> None:
        # 주제 축을 셀 때도 태그·확장자·기간 조건은 남는다 — 「그 칩 하나만 골랐을 때」의 수이므로.
        plan = build_facet_plan("김치", VEC, filters=parse_search_filters(
            topic=["음악"], tag=["김치"], file_ext=["txt"]))
        topic_entry = next(e for e in plan if e["axes"] == ("topic",))
        fields = self._fields_of(topic_entry["body"])
        self.assertNotIn("topics", fields)
        self.assertIn("keywords_norm", fields)
        self.assertIn("file_ext", str(topic_entry["body"]))

    def test_axes_with_the_same_drop_share_a_query(self) -> None:
        # 하위주제만 골랐다면 주제·하위주제 축이 뺄 조건이 같다 → 한 질의로 묶어 왕복을 아낀다.
        plan = build_facet_plan("김치", VEC, filters=parse_search_filters(subtopic=["가수"]))
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]["axes"], ("topic", "subtopic"))

    def test_plan_order_is_stable(self) -> None:
        # 계획 순서가 흔들리면 묶음 응답 짝짓기가 어긋나 칩이 엉뚱한 축에 붙는다.
        f = parse_search_filters(topic=["음악"], subtopic=["가수"], tag=["김치"])
        first = [e["axes"] for e in build_facet_plan("김치", VEC, filters=f)]
        for _ in range(20):
            self.assertEqual([e["axes"] for e in build_facet_plan("김치", VEC, filters=f)], first)

    def test_search_files_bundles_and_pairs_responses(self) -> None:
        # 계획이 여러 개면 묶음 질의 한 번으로 보내고, 축마다 자기 응답의 집계를 읽는다.
        base = _facet_resp(12, aggs={"subtopic": _terms([("가수", 5)]),
                                     "tag": _terms([("김치", 3)])})
        scoped = _facet_resp(99, aggs={"topic": _terms([("음악", 12), ("미술", 40)])})
        client = _FakeClient([_sem_resp(), base, scoped, _rank_resp([_hit("a1", 1.0)])])
        out = search_files(client, "assets", query="김치", query_vector=[0.0],
                           filters=parse_search_filters(topic=["음악"]))
        self.assertIn("msearch", client.calls[1])
        self.assertEqual(out["total"], 12, "개수는 조건을 전부 적용한 첫 질의가 센다")
        self.assertEqual([c["key"] for c in out["facets"]["topic"]], ["음악", "미술"])
        self.assertEqual([c["key"] for c in out["facets"]["subtopic"]], ["가수"])

    def test_failed_bundle_member_raises(self) -> None:
        # 묶음 질의는 실패를 예외로 올리지 않는다 — 확인하지 않으면 칩이 조용히 빈 채로 화면에 나간다.
        client = _FakeClient([_sem_resp(), _facet_resp(1),
                              {"error": {"type": "search_phase_execution"}}])
        with self.assertRaises(RuntimeError):
            search_files(client, "assets", query="김치", query_vector=[0.0],
                         filters=parse_search_filters(topic=["음악"]))


class TestSort(unittest.TestCase):
    """096 — 정렬. 필드로 줄 세우면 뜻은 순서에 관여할 이유가 없다."""

    def test_relevance_is_the_default(self) -> None:
        self.assertEqual(SORT_DEFAULT, "relevance")
        body = build_rank_body("김치", [0.0])
        self.assertIn("hybrid", body["query"])
        self.assertNotIn("sort", body)

    def test_field_sort_keeps_the_same_set(self) -> None:
        # 🔴 정렬을 바꿨는데 개수가 달라지면 화면이 거짓말을 한다 — 그래서 필드 정렬도 같은 집합 절을
        #    쓴다(뜻이 집합 판정에 쓰이므로 벡터가 필요하다). 하이브리드만 아니게 된다.
        f = parse_search_filters(topic="음악")
        base = build_facet_body("김치", SEM, filters=f)["query"]
        for name in ("name_asc", "name_desc", "created_desc", "created_asc",
                     "updated_desc", "size_desc"):
            body = build_rank_body("김치", VEC, semantic_ids=SEM, filters=f, sort=name)
            self.assertNotIn("hybrid", body["query"], f"{name}: 필드 정렬인데 하이브리드다")
            self.assertEqual(body["query"], base, f"{name}: 집합이 관련도순과 다르다")
            self.assertIn("asset_id", str(body["query"]), f"{name}: 뜻 절이 빠졌다")

    def test_field_sort_has_a_tie_breaker(self) -> None:
        # 값이 같은 행의 순서가 흔들리면 페이지를 넘길 때 같은 파일이 두 번 보이거나 아예 빠진다.
        for name in ("name_asc", "name_desc", "created_desc", "created_asc"):
            self.assertEqual(build_rank_body("김치", VEC, sort=name)["sort"][-1],
                             {"asset_id": "asc"}, f"{name}: 동률 기준이 없다")

    def test_sort_fields_are_the_indexed_ones(self) -> None:
        # 색인에 없는 필드로 정렬하면 엔진이 오류를 낸다 — 매핑(``build_index_body``)에 실제로 있는
        # 필드만 쓴다. 🔴 이름은 화면 표시명 필드다(검색용 ``file_name.raw`` 로 세우면 화면의
        # 84.5%가 제자리에 오지 않는다 · 실측 1,526건).
        from src.search.opensearch_sync import build_index_body

        props = build_index_body(dim=8)["mappings"]["properties"]
        for name, order in SORT_OPTIONS.items():
            for clause in order or ():
                for field in clause:
                    head, _, sub = field.partition(".")
                    self.assertIn(head, props, f"{name}: 색인에 없는 필드 {field}")
                    if sub:
                        self.assertIn(sub, props[head]["properties"], f"{name}: {field}")
        self.assertEqual(SORT_OPTIONS["name_asc"][0], {"file_name_sort": "asc"})

    def test_conditions_still_apply_when_sorting(self) -> None:
        f = parse_search_filters(topic=["음악", "미술"], file_ext=["txt"])
        body = build_rank_body("김치", VEC, filters=f, sort="name_asc")
        self.assertIn({"terms": {"topics": ["음악", "미술"]}}, body["query"]["bool"]["filter"])

    def test_unknown_sort_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_rank_body("김치", VEC, sort="크기순")
        with self.assertRaises(ValueError):
            search_files(_FakeClient([]), "assets", query="김치", query_vector=[0.0], sort="크기순")

    def test_field_sort_skips_the_pipeline(self) -> None:
        # 하이브리드가 아니면 정규화할 것이 없다 — 파이프라인을 붙이면 뜻 없는 비용이다.
        client = _FakeClient([_sem_resp(), _facet_resp(3), _rank_resp([_hit("a1", 0.0)])])
        out = search_files(client, "assets", query="김치", query_vector=VEC, sort="name_asc")
        self.assertEqual(client.calls[2]["params"], {})
        self.assertEqual(out["sort"], "name_asc")

    def test_field_sort_pages_deeper(self) -> None:
        # 이웃 탐색이 없어 깊이 제약이 색인 결과창뿐이다 → 관련도보다 깊이 넘길 수 있다.
        deep = RANK_DEPTH_DEFAULT + 100
        client = _FakeClient([_sem_resp(), _facet_resp(deep + 50), _rank_resp([])])
        search_files(client, "assets", query="김치", query_vector=VEC, sort="created_desc",
                     from_=deep, size=10)
        self.assertEqual(client.calls[2]["body"]["from"], deep)
        with self.assertRaises(ValueError):  # 그 창도 넘으면 막는다
            search_files(_FakeClient([]), "assets", query="김치", query_vector=VEC,
                         sort="created_desc", from_=SORT_DEPTH_DEFAULT, size=10)

    def test_relevance_depth_limit_is_unchanged(self) -> None:
        with self.assertRaises(ValueError):
            search_files(_FakeClient([]), "assets", query="김치", query_vector=[0.0],
                         from_=RANK_DEPTH_DEFAULT, size=10)


if __name__ == "__main__":
    unittest.main()
