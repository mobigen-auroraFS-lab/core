"""099 G4 — 개체 **집합 판정**(매칭 개체 전량)과 그 경계 기준. 가짜 클라이언트 단위(OS 불필요).

무엇을 봉인하나

① **경계 기준은 「BM25 낱말 매칭 여부」 하나다**(T018 결정 · 2026-09-17).
   임계(BM25 점수 컷)·kNN 게이트·상위 N 절단을 **쓰지 않는다**. 셋 다 "순위를 매기기 위한"
   장치인데 집합 판정에는 순위가 없기 때문이다 — 정렬은 DB(구성 자산 수 내림차순)가 한다.
   🔴 임계를 고르지 않는 것이 이 결정의 핵심 이득이다: 개체 검색을 잴 골든이 현재 **없어서**
   (092 의 82개 개체 질의셋은 코퍼스 전면 교체로 사망) 보정이 필요한 값을 새로 들이면
   근거 없이 고른 숫자가 된다.
② **낱말끼리 AND · 한 낱말 안에서 필드끼리 OR** — 091 §2-4 규율이자 G3 파일 경로
   (``file_search.refine_clause``)와 **같은 규칙**이다. 화면마다 다른 규칙을 기억할 이유가 없다
   (spec 099 §3-4 사용자 결정).
   ⚠️ ``multi_match`` + ``operator=and`` 로는 이 규칙을 만들 수 없다 — 그것은 **한 필드 안에**
   모든 낱말이 있기를 요구한다(`전통음식`은 키워드에, `배추`는 요약에 있는 흔한 경우가 전부 탈락).
③ **순위 경로(``search_entities_hybrid``)의 기본값은 그대로다** — 되돌림 경로이므로 손대지 않는다
   (plan 099 §1-④). 이 파일이 그 불변을 함께 지킨다.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from src.config import search_constants
from src.search import entity_search_os
from src.search.entity_index import ENTITY_TEXT_FIELDS


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
    """search 호출 본문을 기억하고 미리 정한 hit 을 준다(실 OS 불필요)."""

    def __init__(self, hits: list[dict[str, Any]] | None = None, total: int | None = None) -> None:
        self._hits = hits or []
        self._total = len(self._hits) if total is None else total
        self.bodies: list[dict[str, Any]] = []

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:  # noqa: D102
        self.bodies.append(body)
        return {"hits": {"total": {"value": self._total, "relation": "eq"}, "hits": self._hits}}


def _hit(etype: str, uid: str) -> dict[str, Any]:
    return {"_id": f"{etype}/{uid}", "_score": 1.0,
            "_source": {"entity_type": etype, "entity_uid": uid}}


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
        got = entity_search_os.match_entity_keys(c, "mm_entities", query="한글")
        self.assertEqual(got, {("작품", "훈민정음"), ("장소", "제주도")})
        self.assertIsInstance(got, set)

    def test_같은_개체가_여러_번_와도_한_번이다(self) -> None:
        c = _FakeClient([_hit("작품", "훈민정음"), _hit("작품", "훈민정음")])
        self.assertEqual(len(entity_search_os.match_entity_keys(c, "mm_entities", query="한글")), 1)

    def test_결과가_없으면_빈_집합(self) -> None:
        """🔴 빈 집합은 **0건**이다 — "필터 없음"이 아니다(호출부가 섞으면 검색했는데 전체가 나온다)."""
        self.assertEqual(
            entity_search_os.match_entity_keys(_FakeClient([]), "mm_entities", query="없는말"), set())

    def test_빈_질의는_거부한다(self) -> None:
        """빈 질의에 빈 집합을 돌려주면 "0건"과 "안 물어봤다"가 같은 값이 된다 — 교집합에서 전부 사라진다."""
        for blank in (None, "", "   "):
            with self.assertRaises(ValueError):
                entity_search_os.match_entity_keys(_FakeClient([]), "mm_entities", query=blank)

    def test_kNN_도_게이트도_정규화도_없다(self) -> None:
        """🔴 T018 결정 — 순위 장치를 쓰지 않는다. 질의는 **한 번**이고 본문에 벡터가 없다."""
        c = _FakeClient([_hit("작품", "훈민정음")])
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글")
        self.assertEqual(len(c.bodies), 1)
        self.assertNotIn("knn", json.dumps(c.bodies[0], ensure_ascii=False))

    def test_본문은_상한까지_받고_키만_읽는다(self) -> None:
        c = _FakeClient([])
        entity_search_os.match_entity_keys(c, "mm_entities", query="한글")
        body = c.bodies[0]
        self.assertEqual(body["size"], search_constants.ENTITY_MATCH_MAX_HITS_DEFAULT)
        self.assertEqual(body["_source"], ["entity_type", "entity_uid"])
        self.assertTrue(body["track_total_hits"])

    def test_source_가_없어도_문서_id_로_되살린다(self) -> None:
        """색인 문서 모양이 바뀌어도 검색이 죽지 않게 — 문서 id 규약(``타입/표기``)이 정본이다."""
        c = _FakeClient([{"_id": "작품/훈민정음", "_score": 1.0}])
        self.assertEqual(entity_search_os.match_entity_keys(c, "mm_entities", query="한글"),
                         {("작품", "훈민정음")})

    def test_상한에_걸리면_경고를_남긴다(self) -> None:
        """조용히 잘리면 "검색했는데 있어야 할 게 없는" 오류가 관측되지 않는다."""
        c = _FakeClient([_hit("작품", "훈민정음")], total=99_999)
        with self.assertLogs("src.search.entity_search_os", level="WARNING") as log:
            entity_search_os.match_entity_keys(c, "mm_entities", query="한글", max_hits=1)
        self.assertIn("99999", " ".join(log.output))

    def test_두_번_불러도_같은_집합(self) -> None:
        """헌법 3조 — 같은 입력이면 같은 결과."""
        hits = [_hit("작품", "훈민정음"), _hit("장소", "제주도")]
        first = entity_search_os.match_entity_keys(_FakeClient(hits), "mm_entities", query="한글")
        second = entity_search_os.match_entity_keys(_FakeClient(hits), "mm_entities", query="한글")
        self.assertEqual(first, second)

    def test_질의와_좁히기가_같은_함수를_쓴다(self) -> None:
        """🔴 spec §3-2a — ``q`` 와 refine 은 둘 다 「낱말을 던져 매칭 개체 집합을 얻기」다.
        같은 함수라 한쪽만 고쳐지는 사고가 원리상 없다."""
        hits = [_hit("작품", "훈민정음")]
        as_query = entity_search_os.match_entity_keys(_FakeClient(hits), "mm_entities", query="한글")
        as_refine = entity_search_os.match_entity_keys(_FakeClient(hits), "mm_entities", query="한글")
        self.assertEqual(as_query, as_refine)


if __name__ == "__main__":
    unittest.main()
