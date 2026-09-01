"""091 T005 — 검색 **결과 내 재검색**(글자 좁히기) 단위 테스트(``src/search/refine.py``).

무엇을 검증하나: 이미 받은 검색 결과를 **글자로 한 번 더 좁히는** 규칙이 설계대로 도는지 본다.
쇼핑몰에서 "노트북" 결과 200개를 받은 뒤 목록 위 칸에 "16인치"를 쳐서 줄이는 그 동작이다.
DB·OpenSearch·LLM 이 필요 없는 순수 함수라 mock 없이 시험한다.

왜 이 규칙인가:
    ① **토큰 분리 AND** — 089 는 통짜 부분 문자열이었고 다어절 질의 30개 중 **29개가 0건**이었다
       (`김치 담그기` 라는 글자가 연속으로 있을 리 없다). 공백으로 쪼개 토큰마다 찾는다.
       다만 089 는 찾아오기(recall)라 OR 였고, 여기는 **골라내기라 AND** 다 — OR 로 좁히면
       좁혀지지 않는다.
    ② **필드별로 따로 본다** — 정규화(``normalize_text_key``)가 **공백을 지우므로** 필드를 이어
       붙이면 경계를 넘어 우연히 걸린다(파일명이 `된장` 으로 끝나고 요약이 `국…` 으로 시작하면
       `된장국` 이 걸린다). 그래서 추출기는 **필드 목록**을 돌려주고 검사도 필드 단위로 한다.
    ③ **원 순서 보존** — 재검색은 필터이지 재랭킹이 아니다. 순위가 바뀌면 사용자가 방금 본
       화면과 달라진다(spec 091 D5 — 미달이면 즉시 되돌림).

⚠️ 픽스처는 실 자산이 아니다 — 실측 코퍼스의 **모양**(파일명·40자 안팎 요약·태그 1~3개)만
더미 값으로 재현한다.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.search.refine import asset_refine_fields, refine_rows, refine_tokens


def _row(name: str, summary: str, tags: list[str]) -> dict[str, Any]:
    return {"file_name": name, "summary": summary, "tags": tags}


class TestRefineTokens(unittest.TestCase):
    """재검색어 → 정규화 토큰."""

    def test_공백으로_쪼갠다(self) -> None:
        self.assertEqual(refine_tokens("김치 담그기"), ("김치", "담그기"))

    def test_정규화는_코어_정본을_쓴다(self) -> None:
        # NFKC → 공백 제거 → casefold. 083 태그 키·089 질의 토큰과 같은 함수여야 한다.
        self.assertEqual(refine_tokens("FIFA 월드컵"), ("fifa", "월드컵"))

    def test_빈_입력은_토큰이_없다(self) -> None:
        for q in (None, "", "   ", "\t\n"):
            with self.subTest(q=q):
                self.assertEqual(refine_tokens(q), ())

    def test_한_글자_토큰을_버리지_않는다(self) -> None:
        # 089 T004 ⑦ 이 실측으로 기각한 사안 — 한 글자를 버리면 `강`·`산` 질의가 토큰 0개가 되어
        # "좁히지 않음"으로 해석된다(좁히기가 고장 난 것처럼 보인다).
        self.assertEqual(refine_tokens("강 산"), ("강", "산"))


class TestRefineRows(unittest.TestCase):
    """결과 좁히기 — AND · 순서 보존 · 순수."""

    _ROWS = [
        _row("김치_담그기_영상.mp4", "배추김치를 담그는 과정을 담은 영상", ["전통음식", "김치"]),
        _row("라면_끓이기.mp4", "라면을 맛있게 끓이는 방법", ["간편식", "라면"]),
        _row("김치찌개_레시피.pdf", "김치찌개 끓이는 법", ["전통음식", "찌개"]),
        _row("된장_담그기.txt", "된장을 담그는 전통 방식", ["전통음식", "된장"]),
    ]

    @staticmethod
    def _refine(q: str | None, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        return refine_rows(rows if rows is not None else TestRefineRows._ROWS, q,
                           fields_of=asset_refine_fields)

    def test_다어절이_전멸하지_않는다(self) -> None:
        """🔴 D2 — 089 가 통짜 매칭으로 겪은 실패(다어절 0%)를 반복하지 않는다.

        `김치 담그기` 라는 글자는 어느 필드에도 **연속으로** 있지 않다(요약은 '배추김치를 담그는').
        통짜였다면 0건이고, 토큰으로 쪼개면 걸린다.
        """
        got = self._refine("김치 담그기")
        self.assertEqual([r["file_name"] for r in got], ["김치_담그기_영상.mp4"])

    def test_AND_다_한_토큰만_맞으면_떨어진다(self) -> None:
        # 089 는 OR(하나만 맞아도 남김)였다. 좁히기에서 OR 를 쓰면 좁혀지지 않는다.
        self.assertEqual(self._refine("김치 라면"), [])

    def test_한_토큰이면_모두_남는다(self) -> None:
        got = self._refine("김치")
        self.assertEqual([r["file_name"] for r in got],
                         ["김치_담그기_영상.mp4", "김치찌개_레시피.pdf"])

    def test_원_순서를_지킨다(self) -> None:
        """🔴 D5 — 필터이지 재랭킹이 아니다. 좁힌 목록은 원 목록의 부분수열이어야 한다."""
        got = self._refine("담그")
        names = [r["file_name"] for r in got]
        original = [r["file_name"] for r in self._ROWS]
        self.assertEqual(names, [n for n in original if n in set(names)])

    def test_위양성이_없다(self) -> None:
        """🔴 D1 — 남은 행은 전부 모든 토큰을 갖는다."""
        for q in ("전통", "담그", "김치", "전통 담그"):
            with self.subTest(q=q):
                for row in self._refine(q):
                    haystack = "".join(asset_refine_fields(row))
                    for token in refine_tokens(q):
                        self.assertIn(token, haystack)

    def test_토큰은_서로_다른_필드에_있어도_된다(self) -> None:
        # `전통음식`(태그) + `배추`(요약) — 한 필드에 다 있을 것을 요구하면 실제로 거의 안 걸린다.
        got = self._refine("전통음식 배추")
        self.assertEqual([r["file_name"] for r in got], ["김치_담그기_영상.mp4"])

    def test_필드_경계를_넘어_걸리지_않는다(self) -> None:
        """🔴 정규화가 공백을 지우므로 필드를 이어붙이면 경계 오염이 생긴다.

        파일명이 `된장` 으로 끝나고 요약이 `국…` 으로 시작할 때 `된장국` 이 걸리면 안 된다 —
        그 자산은 된장국과 무관하다.
        """
        rows = [_row("된장", "국물을 내는 법", ["전통음식"])]
        self.assertEqual(refine_rows(rows, "된장국", fields_of=asset_refine_fields), [])

    def test_빈_재검색어는_원_결과_그대로다(self) -> None:
        """되돌림의 실질 — 칸을 비우면 좁히기 전으로 돌아간다."""
        for q in (None, "", "   "):
            with self.subTest(q=q):
                got = self._refine(q)
                self.assertEqual([r["file_name"] for r in got],
                                 [r["file_name"] for r in self._ROWS])

    def test_같은_입력이면_같은_결과(self) -> None:
        first = self._refine("전통")
        for _ in range(4):
            self.assertEqual(self._refine("전통"), first)

    def test_입력_행을_고치지_않는다(self) -> None:
        rows = [dict(r) for r in self._ROWS]
        before = [dict(r) for r in rows]
        refine_rows(rows, "김치", fields_of=asset_refine_fields)
        self.assertEqual(rows, before)

    def test_돌려주는_행은_사본이다(self) -> None:
        rows = [dict(r) for r in self._ROWS]
        got = refine_rows(rows, "김치", fields_of=asset_refine_fields)
        got[0]["file_name"] = "바뀜"
        self.assertNotEqual(rows[0]["file_name"], "바뀜")

    def test_행이_없으면_빈_결과(self) -> None:
        self.assertEqual(refine_rows([], "김치", fields_of=asset_refine_fields), [])


class TestAssetRefineFields(unittest.TestCase):
    """자산 행 → 검색 대상 필드 목록. 백엔드가 모양을 바꿔도 죽지 않아야 한다."""

    def test_세_축을_모두_싣는다(self) -> None:
        got = asset_refine_fields(_row("a.mp4", "요약문", ["태그1", "태그2"]))
        self.assertEqual(got, ["a.mp4", "요약문", "태그1", "태그2"])

    def test_클립_전_요약을_따로_받는다(self) -> None:
        """화면은 잘린 요약을 보지만 좁히기는 원문을 본다(spec 091 §2-5)."""
        row = _row("a.mp4", "잘린 요약…", ["태그"])
        got = asset_refine_fields(row, summary="잘리지 않은 요약 전문이 여기 있다")
        self.assertIn("잘리지 않은 요약 전문이 여기 있다", got)
        self.assertNotIn("잘린 요약…", got)

    def test_필드가_없거나_None_이어도_죽지_않는다(self) -> None:
        for row in ({}, {"file_name": None, "summary": None, "tags": None},
                    {"file_name": "a", "tags": "문자열이_왔다"}, {"tags": [None, "", "정상"]}):
            with self.subTest(row=row):
                got = asset_refine_fields(row)  # type: ignore[arg-type]
                self.assertIsInstance(got, list)
                self.assertTrue(all(isinstance(x, str) for x in got))

    def test_태그가_문자열이면_글자로_쪼개지_않는다(self) -> None:
        # 배열이 아니면 그 축은 없는 것으로 본다(083 aggregate_tag_facets 와 같은 방어).
        got = asset_refine_fields({"file_name": "a", "summary": "b", "tags": "김치"})
        self.assertEqual(got, ["a", "b"])

    def test_내부키를_쓰지_않는다(self) -> None:
        """🔴 `_kwtext`·`_about`·`_rrtext` 는 응답 전 제거되고 파일명이 이중 계산된다."""
        row = {"file_name": "a.mp4", "summary": "요약", "tags": ["태그"],
               "_kwtext": "내부키에만 있는 글자", "_about": ["개체"], "_rrtext": "리랭커용"}
        got = asset_refine_fields(row)
        self.assertNotIn("내부키에만 있는 글자", got)
        self.assertEqual(got, ["a.mp4", "요약", "태그"])
