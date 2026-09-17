"""099 G3 — 파일 **결과 내 재검색(refine)을 검색 엔진 질의 절로** 옮긴 것 봉인(실 IO 0).

무엇이 바뀌었나: 091 은 결과를 받아 온 **뒤 파이썬에서 글자로** 걸렀다. 그래서 좁히기가 닿는 곳이
「이번 페이지」뿐이었다 — 50건을 받아 왔으면 나머지 수천 건은 처음부터 좁히기 대상이 아니었다.
099 는 그 판정을 **검색 엔진에게 맡긴다**: refine 낱말을 질의의 AND 절로 넣으면 엔진이 색인 전체를
보고 거르므로, 2쪽·30쪽에만 있던 자산도 좁히기로 찾아온다(SC-004).

🔴 **매칭 방식이 바뀐다**(spec 099 §3-4 · 2026-09-17 사용자 결정). 색인 텍스트 필드는 형태소
분석기(``nori_user``)라 **낱말 단위**로 쪼개져 있다. 파이썬 부분 문자열로는 `치찌` 가 `김치찌개` 에
걸렸지만(엉뚱한 매칭) 엔진은 걸지 않는다. 대체로 엔진 쪽이 정확하며, 이 변화는 **의도된 것**이다.

무엇을 봉인하나

① **낱말끼리 AND · 한 낱말 안에서 필드끼리 OR**(091 §2-2·§2-4 의 규율을 그대로 옮긴다) —
   `전통음식 배추` 처럼 한 낱말은 태그에 다른 낱말은 요약에 있는 경우가 흔해서, 한 필드에 전부
   있기를 요구하면 실제로 거의 걸리지 않는다.
② **빈 값이면 절을 만들지 않는다** — 칸을 비우면 좁히기 전으로 돌아간다(되돌림의 실질).
③ 🔴 **두 경로가 같은 부품을 쓴다**(plan 099 §1-⑤) — 랭킹(``build_rank_body``)과 커서
   (``build_browse_body``)가 각자 절을 만들면 **한쪽만 고쳐져 경로에 따라 결과가 갈린다**.
④ **세는 질의에도 같은 절이 걸린다** — "적힌 숫자 = 누르면 나오는 수"(096 원칙). 세는 대상과
   보여주는 대상이 어긋나면 화면이 거짓말을 한다.
⑤ **refine 이 없으면 본문이 종전과 한 글자도 다르지 않다**(동작 보존 · 되돌림 경로).

⚠️ 여기 가짜 엔진(``_TinyIndex``)은 **형태소 분해를 흉내내지 않는다** — 공백으로 쪼갠 낱말의 완전
일치만 본다. 여기서 보는 것은 **절의 조합 규칙**(낱말 AND · 필드 OR · 절이 걸리는 자리)이지
형태소 동작이 아니다. 형태소 쪽은 실 엔진 재측정(SC-008)이 맡는다.
"""

from __future__ import annotations

import re
import unittest
from typing import Any

from src.search.cursor import CursorError, encode_cursor
from src.search.file_search import (
    REFINE_FIELDS,
    browse_files,
    build_browse_body,
    build_facet_body,
    build_facet_plan,
    build_rank_body,
    refine_clause,
    search_files,
)

# 가짜 질의 임베딩 — 값에 뜻이 없다(본문 조립만 본다).
VEC: tuple[float, ...] = (0.1, 0.2)


def _flatten(node: Any) -> list[Any]:
    """중첩 dict/list 를 전부 편다 — 절이 **어디에 있든** 찾아내기 위한 검사 보조.

    Args:
        node: 질의 본문의 임의 조각.

    Returns:
        자기 자신을 포함한 모든 하위 노드 목록.
    """
    out: list[Any] = [node]
    if isinstance(node, dict):
        for v in node.values():
            out.extend(_flatten(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_flatten(v))
    return out


def _refine_clauses_in(body: Any) -> list[Any]:
    """본문 안에 들어 있는 refine 절을 전부 찾는다(자리와 무관).

    Args:
        body: 검색 본문(또는 그 조각).

    Returns:
        refine 절과 같은 모양인 노드들.
    """
    target = refine_clause("배추")
    assert target is not None
    return [n for n in _flatten(body) if n == target]


class TestRefineClause(unittest.TestCase):
    """절 조립 부품 자체 — 낱말 AND · 필드 OR · 빈 값은 절 없음."""

    def test_빈_값이면_절을_만들지_않는다(self) -> None:
        """칸을 비우면 좁히기 전으로 돌아간다 — 절이 없어야 집합이 그대로다."""
        for q in (None, "", "   ", "\t\n"):
            with self.subTest(q=q):
                self.assertIsNone(refine_clause(q))

    def test_낱말마다_AND_절이_하나씩_생긴다(self) -> None:
        clause = refine_clause("전통음식 배추")
        self.assertIsNotNone(clause)
        assert clause is not None
        self.assertEqual(len(clause["bool"]["filter"]), 2)

    def test_한_낱말_안에서는_필드끼리_OR_다(self) -> None:
        """`전통음식` 은 태그에, `배추` 는 요약에 있는 일이 흔하다(091 §2-4)."""
        clause = refine_clause("배추")
        assert clause is not None
        per_token = clause["bool"]["filter"][0]["bool"]
        self.assertEqual(per_token["minimum_should_match"], 1)
        self.assertEqual(len(per_token["should"]), len(REFINE_FIELDS))

    def test_보는_필드는_파일명_요약_키워드다(self) -> None:
        """091 파이썬판(``asset_refine_fields``)이 보던 축 그대로 — 화면에 보이는 값이다."""
        self.assertEqual(tuple(REFINE_FIELDS), ("file_name", "summary", "keywords"))
        clause = refine_clause("배추")
        assert clause is not None
        fields = [next(iter(c["match"])) for c in clause["bool"]["filter"][0]["bool"]["should"]]
        self.assertEqual(fields, list(REFINE_FIELDS))

    def test_낱말_단위_절이지_부분_문자열_절이_아니다(self) -> None:
        """🔴 전환의 핵심 — ``wildcard``/``regexp``/``prefix`` 로 흉내내지 않는다(spec §3-4)."""
        clause = refine_clause("배추 김치")
        for node in _flatten(clause):
            if isinstance(node, dict):
                for key in ("wildcard", "regexp", "prefix", "query_string"):
                    self.assertNotIn(key, node)

    def test_같은_입력이면_같은_절이다(self) -> None:
        """순수 함수 — 같은 요청이 실행마다 다른 집합을 내면 안 된다(헌법 3조)."""
        self.assertEqual(refine_clause("배추 김치"), refine_clause("배추 김치"))


class TestBothPathsShareTheClause(unittest.TestCase):
    """🔴 plan §1-⑤ — 랭킹과 커서가 **같은 부품**을 쓴다."""

    def test_랭킹과_커서가_같은_집합_절을_만든다(self) -> None:
        """절이 갈리면 경로에 따라 다른 파일이 나온다 — 화면은 그 차이를 설명할 길이 없다."""
        rank = build_rank_body("한복", VEC, semantic_ids=["s1"], sort="created_desc",
                               refine="배추 김치")
        browse = build_browse_body(query="한복", semantic_ids=["s1"], sort="created_desc",
                                   refine="배추 김치")
        self.assertEqual(rank["query"], browse["query"])

    def test_관련도_정렬은_두_서브질의_모두에_걸린다(self) -> None:
        """하이브리드는 **합집합**이라 한쪽만 걸면 좁히지 않은 문서가 그리로 샌다."""
        body = build_rank_body("한복", VEC, semantic_ids=["s1"], sort="relevance", refine="배추")
        subs = body["query"]["hybrid"]["queries"]
        self.assertEqual(len(subs), 2)
        for i, sub in enumerate(subs):
            with self.subTest(sub=i):
                self.assertTrue(_refine_clauses_in(sub), "이 서브질의에 refine 절이 없다")

    def test_refine_이_없으면_본문이_종전과_같다(self) -> None:
        """동작 보존 — 파라미터를 주지 않은 것과 빈 값이 **바이트 동일**해야 되돌림이 성립한다."""
        for q in (None, "", "   "):
            with self.subTest(q=q):
                self.assertEqual(build_rank_body("한복", VEC, sort="created_desc", refine=q),
                                 build_rank_body("한복", VEC, sort="created_desc"))
                self.assertEqual(build_rank_body("한복", VEC, sort="relevance", refine=q),
                                 build_rank_body("한복", VEC, sort="relevance"))
                self.assertEqual(build_browse_body(query="한복", sort="created_desc", refine=q),
                                 build_browse_body(query="한복", sort="created_desc"))
                self.assertEqual(build_facet_body("한복", refine=q), build_facet_body("한복"))
                self.assertEqual(build_facet_plan("한복", refine=q), build_facet_plan("한복"))


class TestCounting(unittest.TestCase):
    """세는 대상 = 보여주는 대상 + 모수 두 개(spec §3-6)."""

    def test_집계도_같은_절로_센다(self) -> None:
        """좁힌 뒤의 수를 세야 "적힌 숫자 = 누르면 나오는 수"가 성립한다(096 원칙)."""
        body = build_facet_body("한복", refine="배추")
        self.assertTrue(_refine_clauses_in(body["query"]))

    def test_refine_이_있으면_좁히기_이전_모수_질의가_하나_더_생긴다(self) -> None:
        """`scope_total`("지우면 N건")은 refine **없는** 집합의 크기라 따로 세야 한다."""
        plan = build_facet_plan("한복", refine="배추")
        base = build_facet_plan("한복")
        self.assertEqual(len(plan), len(base) + 1)
        scope = [e for e in plan if e.get("scope_total")]
        self.assertEqual(len(scope), 1)
        self.assertFalse(_refine_clauses_in(scope[0]["body"]), "모수 질의에는 refine 이 없어야 한다")
        self.assertTrue(_refine_clauses_in(plan[0]["body"]), "본 질의에는 refine 이 있어야 한다")

    def test_모수_질의는_계획_끝에_붙는다(self) -> None:
        """응답 짝짓기가 순서로 맞춰지므로 기존 항목의 자리를 밀면 안 된다."""
        plan = build_facet_plan("한복", refine="배추")
        self.assertTrue(plan[0]["total"])
        self.assertTrue(plan[-1].get("scope_total"))


class _TinyIndex:
    """OpenSearch 를 **아주 작게** 흉내내는 가짜 엔진 — 절의 조합 규칙만 본다.

    무엇을 흉내내나: ``bool``(should·filter·must·minimum_should_match) · ``match``(낱말 완전
    일치) · ``terms`` · ``match_all`` · 필드 정렬 · ``search_after`` · ``from``/``size`` · 총계.
    ⚠️ 형태소 분해도 조사 제거도 하지 않는다(모듈 docstring 참조). ``knn`` 은 "가까운 것 없음"으로
    답한다 — 뜻 갈래를 빼고 **글자·조건 갈래만** 보기 위해서다.
    """

    _SORT_FIELD = {"file_name_sort": "file_name", "filter_date.created_at": "created_at",
                   "filter_date.updated_at": "updated_at", "file_size": "file_size",
                   "asset_id": "asset_id"}

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        """
        Args:
            docs: 색인에 들어 있다고 볼 문서들(``_source`` 모양 그대로).
        """
        self.docs = list(docs)
        self.bodies: list[dict[str, Any]] = []

    @staticmethod
    def _words(value: Any) -> set[str]:
        """필드 값을 낱말 집합으로(배열이면 원소를 이어 붙인다).

        Args:
            value: 문서 필드 값(문자열·배열·그 밖).

        Returns:
            낱말 집합(소문자).
        """
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        return set(re.findall(r"[0-9A-Za-z가-힣]+", str(value or "").lower()))

    def _matches(self, doc: dict[str, Any], clause: Any) -> bool:
        """문서 하나가 절에 맞는지 본다(재귀).

        Args:
            doc: 대상 문서.
            clause: 질의 절.

        Returns:
            맞으면 참.

        Raises:
            AssertionError: 흉내내지 않는 절이 오면 — 조용히 통과시키면 가짜 성공이 된다.
        """
        if "match_all" in clause:
            return True
        if "match" in clause:
            field, inner = next(iter(clause["match"].items()))
            want = self._words(inner["query"] if isinstance(inner, dict) else inner)
            return bool(want) and want <= self._words(doc.get(field))
        if "multi_match" in clause:
            inner = clause["multi_match"]
            want = self._words(inner["query"])
            hay: set[str] = set()
            for field in inner["fields"]:                 # `summary^3` 처럼 가중치가 붙어 온다
                hay |= self._words(doc.get(field.split("^")[0]))
            return bool(want) and want <= hay
        if "terms" in clause:
            field, values = next(iter(clause["terms"].items()))
            got = doc.get(field)
            got_set = {str(v) for v in got} if isinstance(got, (list, tuple)) else {str(got)}
            return bool(got_set & {str(v) for v in values})
        if "term" in clause:
            field, inner = next(iter(clause["term"].items()))
            want = inner["value"] if isinstance(inner, dict) else inner
            return str(doc.get(field)) == str(want)
        if "bool" in clause:
            b = clause["bool"]
            if not all(self._matches(doc, c) for c in b.get("must", ())):
                return False
            if not all(self._matches(doc, c) for c in b.get("filter", ())):
                return False
            if any(self._matches(doc, c) for c in b.get("must_not", ())):
                return False
            should = b.get("should")
            if should is not None and int(b.get("minimum_should_match", 0)) > 0:
                hit = sum(1 for c in should if self._matches(doc, c))
                return hit >= int(b["minimum_should_match"])
            return True
        raise AssertionError(f"가짜 엔진이 모르는 절이다: {sorted(clause)}")

    def search(self, *, index: str, body: dict[str, Any],
               params: dict[str, Any] | None = None) -> dict[str, Any]:
        """검색 한 번.

        Args:
            index: 색인 이름(쓰지 않는다 — 기록만).
            body: 검색 본문.
            params: 요청 파라미터(쓰지 않는다).

        Returns:
            OpenSearch 응답 모양의 dict.
        """
        self.bodies.append(body)
        query = body.get("query") or {"match_all": {}}
        if "knn" in query:                       # 뜻 갈래는 "가까운 것 없음"으로 답한다
            return {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}
        picked = [d for d in self.docs if self._matches(d, query)]
        order = [(next(iter(s)), next(iter(s.values()))) for s in body.get("sort", [])]
        for field, direction in reversed(order):
            picked.sort(key=lambda d, f=field: d.get(self._SORT_FIELD.get(f, f)),
                        reverse=(direction == "desc"))
        total = len(picked)
        after = body.get("search_after")
        if after is not None:
            vals = [[d.get(self._SORT_FIELD.get(f, f)) for f, _ in order] for d in picked]
            idx = next((i for i, v in enumerate(vals) if v == list(after)), None)
            picked = picked[idx + 1:] if idx is not None else []
        start = int(body.get("from", 0))
        picked = picked[start:start + int(body.get("size", 10))]
        hits = [{"_score": 1.0, "_source": d,
                 "sort": [d.get(self._SORT_FIELD.get(f, f)) for f, _ in order]} for d in picked]
        return {"hits": {"total": {"value": total, "relation": "eq"}, "hits": hits},
                "aggregations": {}}

    def msearch(self, *, index: str, body: list[Any],
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        """묶음 검색 — 머리줄/본문이 번갈아 온다.

        Args:
            index: 색인 이름.
            body: ``[머리줄, 본문, …]``.
            params: 요청 파라미터(쓰지 않는다).

        Returns:
            ``{"responses": [...]}``.
        """
        bodies = [x for i, x in enumerate(body) if i % 2 == 1]
        return {"responses": [self.search(index=index, body=b) for b in bodies]}


def _doc(n: int, *, summary: str, keywords: list[str]) -> dict[str, Any]:
    """가짜 색인 문서 하나.

    Args:
        n: 일련번호(자산 id·등록 시각을 이것으로 만든다).
        summary: 요약 원문.
        keywords: 태그들.

    Returns:
        ``_source`` 모양의 dict.
    """
    return {"asset_id": f"a{n:02d}", "modality": "text", "domain_label": "general",
            "file_name": f"문서{n:02d}.txt", "fs_uri": f"/x/문서{n:02d}.txt",
            "file_name_sort": f"문서{n:02d}.txt", "summary": summary,
            "keywords": keywords, "topics": [], "subtopics": [], "topic_pairs": [],
            "created_at": f"2026-09-{n:02d}", "file_size": n}


# 7건 중 **맨 뒤 한 건**만 `배추` 를 갖는다 — 쪽 크기 3이면 3쪽에 있다(1쪽에 없다).
CORPUS: list[dict[str, Any]] = [
    _doc(1, summary="한복 저고리 이야기", keywords=["한복"]),
    _doc(2, summary="한복 치마 이야기", keywords=["한복"]),
    _doc(3, summary="한복 두루마기", keywords=["한복"]),
    _doc(4, summary="한복 버선", keywords=["한복"]),
    _doc(5, summary="한복 노리개", keywords=["한복"]),
    _doc(6, summary="한복 갓", keywords=["한복"]),
    _doc(7, summary="한복 과 배추 이야기", keywords=["한복", "전통음식"]),
]


class TestSetEquivalence(unittest.TestCase):
    """🔴 SC-004 · plan §1-⑤ — 두 경로가 **같은 문서 집합**을 내고, 뒷쪽 자산이 좁히기로 나온다."""

    def test_두_경로가_같은_문서_집합을_낸다(self) -> None:
        """랭킹(offset)과 커서가 같은 입력에 다른 집합을 내면 화면이 경로마다 달라진다."""
        a = search_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                         sort="created_desc", size=10, refine="배추")
        b = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                         sort="created_desc", size=10, refine="배추")
        self.assertEqual([r["asset_id"] for r in a["rows"]], [r["asset_id"] for r in b["rows"]])
        self.assertEqual(a["total"], b["total"])

    def test_뒷쪽에만_있는_자산이_좁히기로_첫_쪽에_나온다(self) -> None:
        """091 에서는 원리상 불가능했다 — 좁히기가 **받아 온 50건 안**에서만 돌았기 때문이다."""
        # 등록일 **오름차순** 이라 `배추` 를 가진 a07 은 맨 뒤(3쪽)에 있다 — 1쪽엔 없다.
        plain = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                             sort="created_asc", size=3)
        first_page = [r["asset_id"] for r in plain["rows"]]
        self.assertEqual(first_page, ["a01", "a02", "a03"])
        self.assertNotIn("a07", first_page, "이 자산은 1쪽에 없어야 시험이 성립한다")

        narrowed = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                                sort="created_asc", size=3, refine="배추")
        self.assertEqual([r["asset_id"] for r in narrowed["rows"]], ["a07"])
        self.assertEqual(narrowed["total"], 1)

    def test_낱말_AND_는_두_낱말이_다른_필드에_있어도_걸린다(self) -> None:
        """`전통음식` 은 태그에 `배추` 는 요약에 있다 — 091 D2(다어절) 규율 그대로."""
        got = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                           sort="created_desc", size=10, refine="전통음식 배추")
        self.assertEqual([r["asset_id"] for r in got["rows"]], ["a07"])

    def test_한_낱말이라도_없으면_빠진다(self) -> None:
        got = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                           sort="created_desc", size=10, refine="배추 없는말")
        self.assertEqual(got["rows"], [])
        self.assertEqual(got["total"], 0)


class TestScopeTotal(unittest.TestCase):
    """건수 두 개의 뜻(spec §3-6) — ``scope_total`` 은 좁히기 **이전**, ``total`` 은 **이후**."""

    def test_커서_경로가_두_모수를_함께_돌려준다(self) -> None:
        got = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                           sort="created_desc", size=3, refine="배추")
        self.assertEqual(got["scope_total"], 7)   # 지우면 7건
        self.assertEqual(got["total"], 1)         # 좁히면 1건

    def test_랭킹_경로도_두_모수를_함께_돌려준다(self) -> None:
        got = search_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                           sort="created_desc", size=3, refine="배추")
        self.assertEqual(got["scope_total"], 7)
        self.assertEqual(got["total"], 1)

    def test_좁히기가_없으면_두_값이_같다(self) -> None:
        """항상 내보내는 값이라 refine 이 없을 때도 뜻이 있어야 한다(FR-006)."""
        got = browse_files(_TinyIndex(CORPUS), "idx", query="한복", query_vector=VEC,
                           sort="created_desc", size=10)
        self.assertEqual(got["scope_total"], got["total"])
        self.assertEqual(got["total"], 7)


class TestCursorArity(unittest.TestCase):
    """G1 이월 ③ — 개수가 틀린 커서는 **엔진에 닿기 전에** 끊는다."""

    def test_개수가_틀린_커서는_엔진에_닿기_전에_끊긴다(self) -> None:
        """🔴 통과시키면 엔진이 400 을 내는데 그 예외는 CursorError 가 아니라 **HTTP 500** 이 된다."""
        forged = encode_cursor("created_desc", ["2026-09-05"])   # 정렬 키는 2개인데 1개만
        client = _TinyIndex(CORPUS)
        with self.assertRaises(CursorError):
            browse_files(client, "idx", query="", sort="created_desc", cursor=forged, size=3)
        self.assertEqual(client.bodies, [], "엔진에 질의가 나가면 안 된다")

    def test_개수가_맞는_커서는_그대로_이어_읽는다(self) -> None:
        first = browse_files(_TinyIndex(CORPUS), "idx", query="", sort="created_desc", size=3)
        self.assertIsNotNone(first["next_cursor"])
        second = browse_files(_TinyIndex(CORPUS), "idx", query="", sort="created_desc", size=3,
                              cursor=first["next_cursor"])
        self.assertEqual([r["asset_id"] for r in first["rows"]], ["a07", "a06", "a05"])
        self.assertEqual([r["asset_id"] for r in second["rows"]], ["a04", "a03", "a02"])


if __name__ == "__main__":
    unittest.main()
