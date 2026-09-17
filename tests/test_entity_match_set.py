"""099 G4 — 개체 **집합 판정**(매칭 개체 전량)과 그 경계 기준. 가짜 클라이언트 단위(OS 불필요).

무엇을 봉인하나

① **집합은 두 갈래의 합집합이다**(2026-09-17 사용자 결정 — T018 의 「낱말만」을 **뒤집었다**)::

       집합 = ① BM25 낱말 매칭 전부  ∪  ② (kNN 게이트 통과 시) kNN 창 안 전부

   왜 되살렸나: 낱말만 쓰면 **글자가 없는 매칭**을 통째로 잃는다 — `발효`→김치 0건,
   `도자기`→고려청자 0건. 089 이후 남은 실패 30건이 정확히 그 어휘 불일치였고,
   `090-entity-semantic-search` 스펙 하나가 통째로 그것을 풀려고 있었다. 의미(kNN)를 빼는 것은
   그 스펙을 되돌리는 일이다.
   🔴 ② 는 **새 임계를 만들지 않는다** — 순위 경로가 쓰는 그 게이트(``gate_signal`` +
   ``passes_cutoff(eps=0.15, floor=0)``)를 **그대로** 쓴다. 절대 코사인 하한 하나로는 가를 수
   없기 때문이다(실측: 무의미 질의 `존재하지않는낱말xyz` 1등 0.442 vs 유관 `불교 건축` 0.456 —
   붙어 있다). 자산의 ``SEMANTIC_MIN_COSINE_DEFAULT=0.60`` 을 개체에 쓰면 전 구간이 잘린다.
② **낱말끼리 AND · 한 낱말 안에서 필드끼리 OR** — 091 §2-4 규율이자 G3 파일 경로
   (``file_search.refine_clause``)와 **같은 규칙**이다. 화면마다 다른 규칙을 기억할 이유가 없다
   (spec 099 §3-4 사용자 결정).
   ⚠️ ``multi_match`` + ``operator=and`` 로는 이 규칙을 만들 수 없다 — 그것은 **한 필드 안에**
   모든 낱말이 있기를 요구한다(`전통음식`은 키워드에, `배추`는 요약에 있는 흔한 경우가 전부 탈락).
③ **순위 경로(``search_entities_hybrid``)의 기본값은 그대로다** — 되돌림 경로이므로 손대지 않는다
   (plan 099 §1-④). 이 파일이 그 불변을 함께 지킨다.
④ **kNN 창은 ``candidate_size``(20)로 고정한다** — 창을 키우면 게이트 배경(하위 절반 평균)이
   내려가 게이트가 **반드시 더 관대해진다**(T020 단조성 증명). 창을 고정해야 2026-09-01 보정
   전제가 유지된다. 이 파일이 "상한(``max_hits``)을 키워도 창은 그대로"를 봉인한다.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from src.config import search_constants
from src.search import entity_search_os
from src.search.entity_index import ENTITY_TEXT_FIELDS
from src.search.file_search import SEMANTIC_MIN_COSINE_DEFAULT

# 합집합 시험용 질의 벡터(값은 아무거나 — 가짜 클라이언트는 벡터를 보지 않고 미리 정한 hit 을 준다).
_VEC = [0.1, 0.2, 0.3]


class TestMatchBoundaryDecision(unittest.TestCase):
    """① T018 — 무엇을 매칭으로 볼 것인가."""

    def test_보는_필드는_색인_텍스트_필드_전량이다(self) -> None:
        """색인이 넣은 필드와 판정이 보는 필드가 갈리면 "색인엔 있는데 안 걸리는" 개체가 생긴다.

        🔴 ``member``(구성 자산 요약·키워드)가 빠지면 단어 하나 재현율이 90% → 50% 로 무너진다
        (092 실측 · `member 필드 제외` 스윕).
        """
        self.assertEqual(search_constants.ENTITY_MATCH_FIELDS_DEFAULT, ENTITY_TEXT_FIELDS)

    def test_가중_표기를_섞지_않는다(self) -> None:
        """``name^3`` 은 **순위용** 표기다. ``match`` 절의 필드 이름 자리에 쓰면 그런 이름의 필드를 찾는다."""
        for field in search_constants.ENTITY_MATCH_FIELDS_DEFAULT:
            self.assertNotIn("^", field)

    def test_상한은_현_모수보다_한_자릿수_크다(self) -> None:
        """집합 판정은 "맞는 것 전부"라 상한은 순위 절단이 아니라 **폭주 방지선**이다.

        현 노출 개체는 822개(2026-09-17 실측)라 상한이 그 10배면 실질적으로 절단이 없다.
        """
        self.assertGreaterEqual(search_constants.ENTITY_MATCH_MAX_HITS_DEFAULT, 822 * 10)

    def test_순위_경로_기본값은_그대로다(self) -> None:
        """되돌림 경로 보존(plan §1-④) — 새 경로가 잘못돼도 화면을 옛 경로로 되돌릴 수 있어야 한다."""
        self.assertEqual(search_constants.ENTITY_SEARCH_TOP_N_DEFAULT, 5)
        self.assertEqual(search_constants.ENTITY_BM25_OPERATOR_DEFAULT, "or")
        self.assertEqual(search_constants.ENTITY_SEMANTIC_GATE_EPS_DEFAULT, 0.15)
        self.assertEqual(entity_search_os.DEFAULT_CANDIDATE_SIZE, 20)


class _FakeClient:
    """search 호출 본문을 기억하고 미리 정한 hit 을 준다(실 OS 불필요).

    본문에 ``knn`` 이 있으면 **의미 갈래**, 없으면 **낱말 갈래**로 갈라 답한다 — 한 클라이언트가
    두 갈래를 흉내 내야 합집합을 볼 수 있다.
    """

    def __init__(self, hits: list[dict[str, Any]] | None = None, total: int | None = None,
                 knn_hits: list[dict[str, Any]] | None = None) -> None:
        self._hits = hits or []
        self._total = len(self._hits) if total is None else total
        self._knn_hits = knn_hits or []
        self.bodies: list[dict[str, Any]] = []

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:  # noqa: D102
        self.bodies.append(body)
        if "knn" in json.dumps(body, ensure_ascii=False):
            return {"hits": {"total": {"value": len(self._knn_hits), "relation": "eq"},
                             "hits": self._knn_hits}}
        return {"hits": {"total": {"value": self._total, "relation": "eq"}, "hits": self._hits}}


def _hit(etype: str, uid: str) -> dict[str, Any]:
    return {"_id": f"{etype}/{uid}", "_score": 1.0,
            "_source": {"entity_type": etype, "entity_uid": uid}}


def _knn_hit(etype: str, uid: str, cosine: float) -> dict[str, Any]:
    """코사인을 lucene knn ``_score``(=(1+cos)/2)로 되돌린 hit 을 만든다(환산식 대칭)."""
    return {"_id": f"{etype}/{uid}", "_score": (1.0 + cosine) / 2.0,
            "_source": {"entity_type": etype, "entity_uid": uid}}


# 게이트 통과 표본: 1등이 무리에서 튀어나온 모양(top 0.60 · 배경 0.37 · 격차 0.23 ≥ 0.15).
_KNN_PASS = [_knn_hit("음식", "김치", 0.60), _knn_hit("음식", "된장", 0.40),
             _knn_hit("장소", "제주도", 0.38), _knn_hit("작품", "훈민정음", 0.36)]
# 게이트 차단 표본: 전원이 비슷하게 어중간(top 0.50 · 배경 0.455 · 격차 0.045 < 0.15).
_KNN_BLOCK = [_knn_hit("음식", "김치", 0.50), _knn_hit("음식", "된장", 0.48),
              _knn_hit("장소", "제주도", 0.46), _knn_hit("작품", "훈민정음", 0.45)]


class TestEntityMatchClause(unittest.TestCase):
    """② 절 조립 — 파일 경로(``file_search.refine_clause``)와 같은 규칙."""

    def test_빈_값이면_절이_없다(self) -> None:
        for blank in (None, "", "   ", "\t"):
            self.assertIsNone(entity_search_os.entity_match_clause(blank))

    def test_낱말_하나는_필드끼리_OR(self) -> None:
        clause = entity_search_os.entity_match_clause("김치")
        assert clause is not None
        [word] = clause["bool"]["filter"]
        fields = [next(iter(m["match"])) for m in word["bool"]["should"]]
        self.assertEqual(fields, list(search_constants.ENTITY_MATCH_FIELDS_DEFAULT))
        self.assertEqual(word["bool"]["minimum_should_match"], 1)

    def test_낱말끼리는_AND(self) -> None:
        """🔴 낱말끼리 OR 로 하면 좁혀지지 않는다 — 그건 찾아오기 규칙이다(091 §2-2)."""
        clause = entity_search_os.entity_match_clause("전통음식 배추")
        assert clause is not None
        self.assertEqual(len(clause["bool"]["filter"]), 2)
        queries = [m["match"]["name"]["query"]
                   for word in clause["bool"]["filter"] for m in word["bool"]["should"]
                   if "name" in m["match"]]
        self.assertEqual(queries, ["전통음식", "배추"])

    def test_multi_match_를_쓰지_않는다(self) -> None:
        """⚠️ ``multi_match``+``operator=and`` 는 **한 필드 안에** 모든 낱말이 있기를 요구한다 —
        `전통음식`은 키워드에 `배추`는 요약에 있는 흔한 경우가 통째로 탈락한다(091 §2-4)."""
        clause = entity_search_os.entity_match_clause("전통음식 배추")
        self.assertNotIn("multi_match", json.dumps(clause, ensure_ascii=False))

    def test_정규화를_걸지_않는다(self) -> None:
        """색인과 질의가 **같은 분석기**를 지나야 대칭이 성립한다 — 미리 소문자로 바꾸면 어긋난다(G3 T014 와 같은 규율)."""
        clause = entity_search_os.entity_match_clause("AI")
        assert clause is not None
        self.assertEqual(clause["bool"]["filter"][0]["bool"]["should"][0]["match"]["name"]["query"], "AI")


class TestMatchEntityKeys(unittest.TestCase):
    """③ 집합 판정 진입점 — 순위가 아니라 **개체 키 집합**을 돌려준다."""

    def test_매칭_개체의_키_집합을_돌려준다(self) -> None:
        c = _FakeClient([_hit("작품", "훈민정음"), _hit("장소", "제주도")])
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=None)
        self.assertEqual(got.keys, {("작품", "훈민정음"), ("장소", "제주도")})
        # 순위 목록이 아니라 **집합**임을 계속 못 박는다(넓힌 반환에서도 결과 자리는 집합이다).
        self.assertIsInstance(got.keys, frozenset)

    def test_같은_개체가_여러_번_와도_한_번이다(self) -> None:
        c = _FakeClient([_hit("작품", "훈민정음"), _hit("작품", "훈민정음")])
        self.assertEqual(
            len(entity_search_os.match_entity_keys(
                c, "mm_entities", query="한글", query_vector=None).keys), 1)

    def test_결과가_없으면_빈_집합(self) -> None:
        """🔴 빈 집합은 **0건**이다 — "필터 없음"이 아니다(호출부가 섞으면 검색했는데 전체가 나온다)."""
        self.assertEqual(
            entity_search_os.match_entity_keys(
                _FakeClient([]), "mm_entities", query="없는말", query_vector=None).keys, set())

    def test_빈_질의는_거부한다(self) -> None:
        """빈 질의에 빈 집합을 돌려주면 "0건"과 "안 물어봤다"가 같은 값이 된다 — 교집합에서 전부 사라진다."""
        for blank in (None, "", "   "):
            with self.assertRaises(ValueError):
                entity_search_os.match_entity_keys(_FakeClient([]), "mm_entities", query=blank, query_vector=None)

    def test_낱말_갈래에는_순위_장치가_없다(self) -> None:
        """① 갈래는 "맞았나"만 본다 — 점수 컷·정규화·상위 N 절단이 없고 본문에 벡터도 없다.

        의미(kNN)는 **별도 질의**(② 갈래)로 나가므로 낱말 본문에는 여전히 ``knn`` 이 없다.
        """
        c = _FakeClient([_hit("작품", "훈민정음")])
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=None)
        self.assertEqual(len(c.bodies), 1)
        self.assertNotIn("knn", json.dumps(c.bodies[0], ensure_ascii=False))

    def test_본문은_상한까지_받고_키만_읽는다(self) -> None:
        c = _FakeClient([])
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=None)
        body = c.bodies[0]
        self.assertEqual(body["size"], search_constants.ENTITY_MATCH_MAX_HITS_DEFAULT)
        self.assertEqual(body["_source"], ["entity_type", "entity_uid"])
        self.assertTrue(body["track_total_hits"])

    def test_source_가_없어도_문서_id_로_되살린다(self) -> None:
        """색인 문서 모양이 바뀌어도 검색이 죽지 않게 — 문서 id 규약(``타입/표기``)이 정본이다."""
        c = _FakeClient([{"_id": "작품/훈민정음", "_score": 1.0}])
        self.assertEqual(
            entity_search_os.match_entity_keys(
                c, "mm_entities", query="한글", query_vector=None).keys, {("작품", "훈민정음")})

    def test_상한에_걸리면_경고를_남긴다(self) -> None:
        """조용히 잘리면 "검색했는데 있어야 할 게 없는" 오류가 관측되지 않는다."""
        c = _FakeClient([_hit("작품", "훈민정음")], total=99_999)
        with self.assertLogs("src.search.entity_search_os", level="WARNING") as log:
            entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=None, max_hits=1)
        self.assertIn("99999", " ".join(log.output))

    def test_두_번_불러도_같은_집합(self) -> None:
        """헌법 3조 — 같은 입력이면 같은 결과."""
        hits = [_hit("작품", "훈민정음"), _hit("장소", "제주도")]
        first = entity_search_os.match_entity_keys(
            _FakeClient(hits), "mm_entities", query="한글", query_vector=None).keys
        second = entity_search_os.match_entity_keys(
            _FakeClient(hits), "mm_entities", query="한글", query_vector=None).keys
        self.assertEqual(first, second)

    def test_질의와_좁히기가_같은_함수를_쓴다(self) -> None:
        """🔴 spec §3-2a — ``q`` 와 refine 은 둘 다 「낱말을 던져 매칭 개체 집합을 얻기」다.
        같은 함수라 한쪽만 고쳐지는 사고가 원리상 없다."""
        hits = [_hit("작품", "훈민정음")]
        as_query = entity_search_os.match_entity_keys(
            _FakeClient(hits), "mm_entities", query="한글", query_vector=None).keys
        as_refine = entity_search_os.match_entity_keys(
            _FakeClient(hits), "mm_entities", query="한글", query_vector=None).keys
        self.assertEqual(as_query, as_refine)


class TestSemanticBranch(unittest.TestCase):
    """④ 의미(kNN) 갈래 — 2026-09-17 사용자 결정으로 **되살렸다**(T018 뒤집기).

    비유: 낱말 갈래는 "그 글자가 적혀 있나"를 보는 색인 카드이고, 의미 갈래는 "뜻이 가까운가"를
    보는 사서다. 사서가 아무 근거 없이 아무 책이나 집어 오는 것을 막는 장치가 **게이트**다 —
    "1등이 나머지 무리보다 튀어나왔나"를 묻고, 아니면 사서의 추천을 **통째로** 버린다.
    """

    def test_집합은_낱말과_의미의_합집합이다(self) -> None:
        """🔴 되살린 이유: 낱말만 쓰면 `발효`→김치처럼 **글자가 없는 매칭**을 통째로 잃는다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="발효", query_vector=_VEC)
        self.assertEqual(got.keys, {("작품", "훈민정음"), ("음식", "김치"), ("음식", "된장"),
                                    ("장소", "제주도")})

    def test_질의는_두_번이고_두_번째가_kNN이다(self) -> None:
        """갈래가 둘이라 엔진 왕복도 둘이다. 순서는 낱말 → 의미(낱말 본문이 ``bodies[0]``)."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertEqual(len(c.bodies), 2)
        self.assertNotIn("knn", json.dumps(c.bodies[0], ensure_ascii=False))
        self.assertIn("knn", json.dumps(c.bodies[1], ensure_ascii=False))
        self.assertEqual(c.bodies[1]["query"]["knn"]["vec"]["vector"], _VEC)

    def test_게이트가_막으면_의미는_빠지고_낱말은_그대로_남는다(self) -> None:
        """🔴 게이트는 ② 갈래에만 건다 — ① 낱말 결과를 막으면 재현율이 무너진다(092 실측 90%→50%)."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_BLOCK)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertEqual(got.keys, {("작품", "훈민정음")})

    def test_게이트_차단을_로그로_알린다(self) -> None:
        """조용히 사라지면 "왜 못 찾지"를 추적할 수 없다 — 격차·임계를 함께 남긴다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_BLOCK)
        with self.assertLogs("src.search.entity_search_os", level="WARNING") as log:
            entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        line = " ".join(log.output)
        self.assertIn("게이트", line)
        self.assertIn("0.15", line)

    def test_게이트_차단을_반환값으로도_읽을_수_있다(self) -> None:
        """로그는 사후 추적용이다. 호출부가 화면에 근거를 싣고 싶으면 **값**이 필요하다."""
        blocked = entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=_KNN_BLOCK), "mm_entities", query_vector=_VEC)
        self.assertFalse(blocked.gate_passed)
        self.assertEqual(blocked.keys, frozenset())
        self.assertEqual(blocked.sample_size, 4)

        passed = entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=_KNN_PASS), "mm_entities", query_vector=_VEC)
        self.assertTrue(passed.gate_passed)
        self.assertEqual(len(passed.keys), 4)
        self.assertAlmostEqual(passed.top, 0.60, places=6)
        self.assertAlmostEqual(passed.baseline, 0.37, places=6)

    def test_의미_후보가_0건이면_차단과_다른_문구로_알린다(self) -> None:
        """"게이트가 막았다"와 "애초에 후보가 없다"는 **다른 사건**이다 — 같은 문구면 오진한다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=[])
        with self.assertLogs("src.search.entity_search_os", level="WARNING") as log:
            got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertEqual(got.keys, {("작품", "훈민정음")})
        self.assertIn("후보가 0건", " ".join(log.output))

    def test_벡터가_없으면_낱말_갈래만_쓰고_그_사실을_알린다(self) -> None:
        """🔴 ``query_vector`` 는 **기본값이 없는 필수 인자**다 — 깜빡하면 TypeError 로 즉시 드러나고,
        ``None`` 은 "의미를 일부러 껐다"는 **명시적 선택**이 된다(조용한 재현율 손실 차단)."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        with self.assertLogs("src.search.entity_search_os", level="WARNING") as log:
            got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=None)
        self.assertEqual(got.keys, {("작품", "훈민정음")})
        self.assertEqual(len(c.bodies), 1)
        self.assertIn("벡터", " ".join(log.output))

    def test_kNN_창은_상한이_아니라_candidate_size_다(self) -> None:
        """🔴 이번 설계의 핵심 고정점(T020): 창을 키우면 배경(하위 절반 평균)이 내려가 게이트가
        **반드시 더 관대해진다**. 상한(10,000)을 창으로 쓰면 2026-09-01 보정 전제가 깨진다."""
        c = _FakeClient([], knn_hits=_KNN_PASS)
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC,
                                           max_hits=10_000)
        knn_body = c.bodies[1]
        self.assertEqual(knn_body["size"], entity_search_os.DEFAULT_CANDIDATE_SIZE)
        self.assertEqual(knn_body["query"]["knn"]["vec"]["k"], entity_search_os.DEFAULT_CANDIDATE_SIZE)
        self.assertEqual(c.bodies[0]["size"], 10_000)  # 낱말 갈래는 상한까지 — 여긴 순위가 없다

    def test_창을_바꾸면_size_와_k_가_함께_움직인다(self) -> None:
        """둘이 갈리면 표본(정규화·게이트 모수)과 받은 결과 수가 어긋난다."""
        c = _FakeClient([], knn_hits=_KNN_PASS)
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC,
                                           candidate_size=7)
        self.assertEqual(c.bodies[1]["size"], 7)
        self.assertEqual(c.bodies[1]["query"]["knn"]["vec"]["k"], 7)

    def test_절대_코사인_하한을_두지_않는다(self) -> None:
        """실측(2026-09-17): 무의미 질의 1등 0.442 vs 유관 질의 1등 0.456 — 절대값으로는 못 가른다.
        자산의 ``SEMANTIC_MIN_COSINE_DEFAULT``(0.60)를 개체에 쓰면 전 구간(≤0.64)이 잘린다."""
        low = [_knn_hit("음식", "김치", 0.25), _knn_hit("음식", "된장", 0.05),
               _knn_hit("장소", "제주도", 0.05), _knn_hit("작품", "훈민정음", 0.05)]
        got = entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=low), "mm_entities", query_vector=_VEC)
        self.assertTrue(got.gate_passed)
        self.assertLess(got.top, SEMANTIC_MIN_COSINE_DEFAULT)

    def test_격차가_모자라면_절대값이_높아도_막힌다(self) -> None:
        """게이트는 "1등이 무리에서 튀어나왔나"만 본다 — 반 전체가 60점인데 1등이 62점이면 뜻이 없다."""
        flat = [_knn_hit("음식", "김치", 0.64), _knn_hit("음식", "된장", 0.62),
                _knn_hit("장소", "제주도", 0.61), _knn_hit("작품", "훈민정음", 0.60)]
        got = entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=flat), "mm_entities", query_vector=_VEC)
        self.assertFalse(got.gate_passed)

    def test_게이트_기본값은_순위_경로와_같은_0_15_다(self) -> None:
        """🔴 새 임계를 만들지 않는다 — ``search_entities_hybrid`` 와 **같은 함수·같은 기본값**.
        경계 격차 0.15 는 통과, 0.14 는 차단(``passes_cutoff`` 의 ``>=`` 규약)."""
        self.assertEqual(search_constants.ENTITY_SEMANTIC_GATE_EPS_DEFAULT, 0.15)
        edge = [_knn_hit("음식", "김치", 0.60), _knn_hit("음식", "된장", 0.45),
                _knn_hit("장소", "제주도", 0.45), _knn_hit("작품", "훈민정음", 0.45)]
        self.assertTrue(entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=edge), "mm_entities", query_vector=_VEC).gate_passed)
        under = [_knn_hit("음식", "김치", 0.59), *edge[1:]]
        self.assertFalse(entity_search_os.semantic_entity_keys(
            _FakeClient(knn_hits=under), "mm_entities", query_vector=_VEC).gate_passed)

    def test_두_갈래에_겹쳐도_한_번이다(self) -> None:
        """합집합이므로 같은 개체가 양쪽에 있어도 하나다(순위가 없으니 가중도 없다)."""
        c = _FakeClient([_hit("음식", "김치")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="김치", query_vector=_VEC)
        self.assertEqual(len([k for k in got.keys if k == ("음식", "김치")]), 1)
        self.assertEqual(len(got.keys), 4)

    def test_두_갈래를_써도_두_번_부르면_같은_집합(self) -> None:
        """헌법 3조 — 같은 입력이면 같은 결과(집합이라 순서 자체가 없다)."""
        def run() -> frozenset[tuple[str, str]]:
            return entity_search_os.match_entity_keys(
                _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS),
                "mm_entities", query="한글", query_vector=_VEC).keys
        self.assertEqual(run(), run())

    def test_순위_경로는_그대로다(self) -> None:
        """되돌림 경로 보존 — 집합 경로를 고쳐도 ``search_entities_hybrid`` 는 종전 그대로 동작한다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        rows = entity_search_os.search_entities_hybrid(
            c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertLessEqual(len(rows), search_constants.ENTITY_SEARCH_TOP_N_DEFAULT)
        self.assertEqual(c.bodies[0]["size"], entity_search_os.DEFAULT_CANDIDATE_SIZE)
        self.assertIn("multi_match", json.dumps(c.bodies[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()


class Test낱말안의_형태소는_AND다(unittest.TestCase):
    """🔴 한 낱말이 형태소로 쪼개질 때 **그 조각들은 모두** 있어야 한다(실측 회귀).

    2026-09-17 실 색인 실측: ``operator`` 를 주지 않으면 ``match`` 기본이 **OR** 라
    ``존재하지않는낱말xyz`` 가 nori 로 ``['존재','하','지','않','는','낱','말','xyz']`` 로 쪼개지고
    ``하``·``지``·``말`` 같은 흔한 조각 하나만 걸려도 통과한다 — **822개 중 818개(99.5%)가 매칭**됐다.
    ``숭례문`` 도 92건(→ 고친 뒤 4건), ``석굴암`` 70건(→ 15건)으로 노이즈였다.

    ⚠️ 이것은 필드 간 ``and`` 가 **아니다**(그건 모듈 docstring 이 금지한 것 — `전통음식`+`배추` 가
    서로 다른 필드에 있으면 탈락한다). 낱말 **하나 안에서** 그 낱말의 형태소 조각들이 **한 필드 안에**
    모두 있어야 한다는 뜻이며, 낱말끼리 AND · 필드끼리 OR 라는 계약은 그대로다.
    """

    def test_각_match_에_operator_and_가_붙는다(self) -> None:
        clause = entity_search_os.entity_match_clause("숭례문")
        assert clause is not None
        for per_word in clause["bool"]["filter"]:
            for m in per_word["bool"]["should"]:
                (field, spec), = m["match"].items()
                self.assertEqual(spec.get("operator"), "and", f"{field} 에 operator=and 가 없다")

    def test_필드끼리는_여전히_OR_다(self) -> None:
        clause = entity_search_os.entity_match_clause("전통음식 배추")
        assert clause is not None
        self.assertEqual(len(clause["bool"]["filter"]), 2, "낱말 2개 → 절 2개(낱말 AND)")
        for per_word in clause["bool"]["filter"]:
            self.assertEqual(per_word["bool"]["minimum_should_match"], 1, "필드는 OR 유지")


class Test걸린_이유를_값으로_돌려준다(unittest.TestCase):
    """⑤ 집합 판정이 **어느 갈래로 들어왔는지**를 함께 돌려준다(099 후속 · 2026-09-17 사용자 결정).

    왜 필요한가: 뜻(kNN)으로 걸린 결과는 **화면 어디에도 검색어가 보이지 않는다** — `왕실 무덤` 으로
    찾으면 `영릉` 이 나오는데 그 카드에는 "왕실 무덤" 이라는 글자가 한 자도 없다. 근거를 함께 주지
    않으면 사용자는 "검색이 고장났나"로 읽는다. 089·090·092 가 공들여 만든 설명 가능성이고,
    집합 판정으로 갈아타며(G5) 잃었던 것을 되살린다.

    🔴 **추가 질의는 없다.** 판정은 이미 낱말·의미 **두 갈래로 따로 계산**되고 마지막에 합쳐질
    뿐이라, 버리지 않고 함께 돌려주기만 하면 된다. 이 파일이 "엔진 왕복이 늘지 않았다"를 함께 봉인한다.
    """

    def test_낱말_갈래와_의미_갈래를_따로_돌려준다(self) -> None:
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="발효", query_vector=_VEC)
        self.assertEqual(got.text_keys, frozenset({("작품", "훈민정음")}))
        self.assertEqual(got.semantic_keys,
                         frozenset({("음식", "김치"), ("음식", "된장"), ("장소", "제주도"),
                                    ("작품", "훈민정음")}))
        self.assertTrue(got.semantic_gate_passed)

    def test_합집합은_두_갈래를_합친_것과_같다(self) -> None:
        """🔴 합집합이 **파생값**임을 못 박는다 — 갈래를 따로 돌려줘도 결과 집합은 종전과 같다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="발효", query_vector=_VEC)
        self.assertEqual(got.keys, got.text_keys | got.semantic_keys)

    def test_글자로만_걸린_것과_뜻으로만_걸린_것이_갈린다(self) -> None:
        """이 구분이 화면 문구의 재료다 — `김치` 는 글자가 없이(뜻으로) 걸렸으니 근거를 보여야 한다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="발효", query_vector=_VEC)
        self.assertIn(("음식", "김치"), got.semantic_keys)
        self.assertNotIn(("음식", "김치"), got.text_keys)
        self.assertIn(("작품", "훈민정음"), got.text_keys)

    def test_두_갈래에_겹친_개체는_양쪽_모두에_있다(self) -> None:
        """겹침을 한쪽으로 몰지 않는다 — 합집합에서 한 번인 것과 갈래 표시는 다른 이야기다."""
        c = _FakeClient([_hit("음식", "김치")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="김치", query_vector=_VEC)
        self.assertIn(("음식", "김치"), got.text_keys)
        self.assertIn(("음식", "김치"), got.semantic_keys)
        self.assertEqual(len(got.keys), 4, "합집합은 여전히 한 번씩이다")

    def test_게이트가_막으면_의미_갈래는_비고_그_사실이_값으로_남는다(self) -> None:
        """로그만 두면 화면이 근거를 보일 수 없다 — ``EntitySemanticMatch`` 를 만든 그 이유다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_BLOCK)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertFalse(got.semantic_gate_passed)
        self.assertEqual(got.semantic_keys, frozenset())
        self.assertEqual(got.text_keys, frozenset({("작품", "훈민정음")}))
        self.assertEqual(got.keys, got.text_keys, "① 낱말 갈래는 게이트와 무관하게 남는다")

    def test_벡터가_없으면_의미_갈래가_통째로_비어_있다(self) -> None:
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        with self.assertLogs("src.search.entity_search_os", level="WARNING"):
            got = entity_search_os.match_entity_keys(
                c, "mm_entities", query="한글", query_vector=None)
        self.assertFalse(got.semantic_gate_passed)
        self.assertEqual(got.semantic_keys, frozenset())
        self.assertEqual(got.keys, got.text_keys)

    def test_갈래를_돌려줘도_엔진_왕복은_그대로다(self) -> None:
        """🔴 근거를 실으려고 **질의를 더 던지지 않는다** — 이미 계산된 것을 버리지 않을 뿐이다."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertEqual(len(c.bodies), 2, "낱말 1회 + 의미 1회 — 종전과 같다")

    def test_반환_모양이_EntitySemanticMatch_와_결이_같다(self) -> None:
        """둘 다 NamedTuple 이고 키 집합은 ``frozenset`` 이다 — 호출부가 실수로 고치지 못하게."""
        c = _FakeClient([_hit("작품", "훈민정음")], knn_hits=_KNN_PASS)
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글", query_vector=_VEC)
        self.assertIsInstance(got, entity_search_os.EntityMatchSet)
        self.assertIsInstance(got, tuple)
        for field in (got.keys, got.text_keys, got.semantic_keys):
            self.assertIsInstance(field, frozenset)
