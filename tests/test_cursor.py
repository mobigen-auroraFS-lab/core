"""커서(책갈피) 규약 — 097 G1.

여기서 막는 것은 **조용한 오동작**이다: 커서가 엉뚱한 자리에서 이어지면 목록에 구멍이 나는데
오류가 없어 아무도 모른다. 그래서 왕복뿐 아니라 **거부해야 할 경우**를 촘촘히 봉인한다.
"""

from __future__ import annotations

import unittest

from src.search.cursor import CursorError, decode_cursor, encode_cursor
from src.search.file_search import SORT_OPTIONS, STABLE_SORTS


class 커서_왕복(unittest.TestCase):
    def test_감싸고_풀면_같은_값이_나온다(self):
        values = ["2026-09-11T17:46:00", "018f0000-0000-7000-8000-000000000001"]
        token = encode_cursor("created_desc", values)
        self.assertEqual(decode_cursor(token, expect_sort="created_desc"), values)

    def test_같은_입력은_같은_토큰이다(self):
        """순수 함수 — 결정성이 없으면 화면이 같은 쪽을 두 번 받을 수 있다."""
        a = encode_cursor("name_asc", ["가.mp4", "01a0"])
        b = encode_cursor("name_asc", ["가.mp4", "01a0"])
        self.assertEqual(a, b)

    def test_한글과_특수문자가_깨지지_않는다(self):
        """파일 이름에 전각 따옴표·공백이 흔하다(실측 코퍼스)."""
        values = ["＂폭싹 속았수다＂ 리뷰 2탄.mp4", "01a0-abc"]
        token = encode_cursor("name_asc", values)
        self.assertEqual(decode_cursor(token, expect_sort="name_asc"), values)

    def test_토큰에_패딩이_없다(self):
        """URL 질의값으로 실으므로 `=` 가 없어야 다루기 쉽다."""
        self.assertNotIn("=", encode_cursor("size_asc", [123, "01a0"]))


class 커서_거부(unittest.TestCase):
    def test_정렬이_다르면_거부한다(self):
        """🔴 조용히 이어 주면 엉뚱한 자리에서 읽는다 — 사용자는 목록이 틀린 줄 모른다."""
        token = encode_cursor("created_desc", ["2026-09-11", "01a0"])
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="name_asc")

    def test_깨진_토큰을_거부한다(self):
        for bad in ("!!!!", "zzzz", "eyJ4Ijoi"):
            with self.subTest(bad=bad), self.assertRaises(CursorError):
                decode_cursor(bad, expect_sort="created_desc")

    def test_빈_토큰을_거부한다(self):
        with self.assertRaises(CursorError):
            decode_cursor("", expect_sort="created_desc")

    def test_정렬값이_비면_만들지_않는다(self):
        """이어 읽을 자리가 없는 커서는 만들어 봐야 다음 쪽이 처음으로 돌아간다."""
        with self.assertRaises(CursorError):
            encode_cursor("created_desc", [])

    def test_모양이_다른_JSON_을_거부한다(self):
        import base64
        import json
        raw = json.dumps({"엉뚱": 1}).encode()
        token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="created_desc")


class 정렬_규약(unittest.TestCase):
    def test_모든_정렬이_두_번째_키로_asset_id_를_갖는다(self):
        """🔴 고유값이 없으면 같은 정렬값이 쪽 경계에 걸릴 때 **건너뛰거나 겹친다**.

        오류가 나지 않아 아무도 모른다 — 이 테스트가 유일한 방어선이다(097 §2-2).
        """
        for name, order in SORT_OPTIONS.items():
            if order is None:      # relevance 는 필드 정렬이 아니다
                continue
            with self.subTest(sort=name):
                self.assertGreaterEqual(len(order), 2, f"{name}: 정렬 키가 하나뿐이다")
                self.assertIn("asset_id", order[-1], f"{name}: 마지막 키가 asset_id 가 아니다")

    def test_안정_정렬_집합이_실재하는_정렬만_담는다(self):
        for name in STABLE_SORTS:
            with self.subTest(sort=name):
                self.assertIn(name, SORT_OPTIONS)

    def test_수정시각과_관련도는_안정_정렬이_아니다(self):
        """`updated_*` 는 순회 중 값이 바뀌고 `relevance` 는 이어받을 기준값이 없다."""
        for name in ("updated_asc", "updated_desc", "relevance"):
            with self.subTest(sort=name):
                self.assertNotIn(name, STABLE_SORTS)


if __name__ == "__main__":
    unittest.main()


class 훑기_질의(unittest.TestCase):
    """``build_browse_body``·``browse_scope_clause`` — OS 없이 본문만 본다."""

    def test_검색어가_없으면_조건에_맞는_전부다(self):
        """🔴 `_scope_clause` 는 빈 질의에서 0건을 낸다(should 하나 + minimum_should_match 1).

        검색이라면 그것이 옳지만 **첫 화면은 훑기**라 전부가 맞다(실측: 고치기 전 0건).
        """
        from src.search.file_search import browse_scope_clause
        clause = browse_scope_clause("")["bool"]
        self.assertEqual(clause["must"], [{"match_all": {}}])
        self.assertNotIn("minimum_should_match", clause)

    def test_검색어가_있으면_검색과_같은_집합을_쓴다(self):
        """집합이 갈리면 "적힌 숫자 = 누르면 나오는 수"가 깨진다(096 원칙)."""
        from src.search.file_search import _scope_clause, browse_scope_clause
        self.assertEqual(browse_scope_clause("김치"), _scope_clause("김치"))

    def test_첫_쪽에는_search_after_가_없다(self):
        from src.search.file_search import build_browse_body
        self.assertNotIn("search_after", build_browse_body(sort="created_desc"))

    def test_이어_읽기는_search_after_를_싣는다(self):
        from src.search.file_search import build_browse_body
        body = build_browse_body(sort="created_desc", after=["2026-09-11", "018f0000"])
        self.assertEqual(body["search_after"], ["2026-09-11", "018f0000"])

    def test_관련도_정렬은_커서로_넘길_수_없다(self):
        """하이브리드 점수는 상위 rank_depth 개만 계산돼 이어받을 기준값이 없다(097 §2-5)."""
        from src.search.file_search import build_browse_body
        with self.assertRaises(ValueError) as ctx:
            build_browse_body(sort="relevance")
        self.assertIn("이름순", str(ctx.exception))   # 막다른 길이 아니라 갈림길로 안내한다

    def test_모르는_정렬과_size_범위를_막는다(self):
        from src.search.file_search import build_browse_body
        with self.assertRaises(ValueError):
            build_browse_body(sort="없는정렬")
        with self.assertRaises(ValueError):
            build_browse_body(sort="created_desc", size=0)

    def test_정렬_키는_언제나_asset_id_로_끝난다(self):
        from src.search.file_search import build_browse_body
        for name in ("created_desc", "name_asc", "size_desc"):
            with self.subTest(sort=name):
                self.assertIn("asset_id", build_browse_body(sort=name)["sort"][-1])
