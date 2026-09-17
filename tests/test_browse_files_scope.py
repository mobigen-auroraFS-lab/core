"""099 G7 — 파일 훑기(``browse_files``)가 **조건 지문**을 커서에 싣고 되읽을 때 대조한다.

왜 코어가 지문을 만들지 않고 **받아만** 두나: 코어는 화면 파라미터를 알면 안 된다(093 책무 경계).
"이번 조회를 정의하는 것 전부"가 무엇인지는 화면(백엔드)이 안다 — 검색어·좁히기·칩 필터·기간처럼
화면이 늘리면 함께 늘어나는 목록이기 때문이다. 그래서 백엔드가 문자열 하나로 모아 넘기고, 코어는
그것을 토큰에 **도장으로 찍어 두었다가** 다음 쪽에서 같은 도장인지만 본다.

여기서 봉인하는 것 셋:
    ① 같은 조건이면 종전처럼 이어 읽는다(회귀).
    ② 조건이 다르면 **엔진에 닿기 전에** ``CursorError`` 로 끊는다(호출부가 400 으로 바꾼다).
    ③ 지문이 없는 옛 토큰도 끊는다 — 실측 결함 A(자료 누락)를 되살리지 않는다.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.search.cursor import CursorError, encode_cursor
from src.search.file_search import browse_files

_SCOPE_ALL = "q=|refine=|modality=[]"
_SCOPE_TEXT = 'q=|refine=|modality=["text"]'


class _StubEngine:
    """검색 엔진 대역 — 무엇을 묻든 같은 쪽을 돌려준다(여기서 보는 것은 **커서 검사**뿐).

    실제 매칭·집계 규칙은 ``tests/test_file_search_refine_clause.py`` 가 가짜 색인으로 본다.
    """

    def __init__(self) -> None:
        self.calls = 0

    def _page(self) -> dict[str, Any]:
        """고정 응답 한 쪽(문서 1건 · 총계 1건).

        Returns:
            OpenSearch 응답 모양의 dict.
        """
        return {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{"_source": {"asset_id": "a01", "file_name": "가.txt"},
                          "sort": ["2026-09-11", "a01"]}],
            },
            "aggregations": {},
        }

    def search(self, index: str, body: dict[str, Any], params: dict[str, Any] | None = None):
        """단건 질의 대역.

        Args:
            index: 색인 이름(쓰지 않는다).
            body: 질의 본문(쓰지 않는다).
            params: 검색 파라미터(쓰지 않는다).

        Returns:
            고정 응답.
        """
        self.calls += 1
        return self._page()

    def msearch(self, index: str, body: list[dict[str, Any]]):
        """묶음 질의 대역 — 계획 수만큼 같은 응답을 돌려준다.

        Args:
            index: 색인 이름(쓰지 않는다).
            body: 머리줄·본문이 번갈아 든 목록.

        Returns:
            ``{"responses": [...]}``.
        """
        self.calls += 1
        return {"responses": [self._page() for _ in range(len(body) // 2)]}


def _browse(client: Any, *, scope: str, cursor: str | None = None) -> dict[str, Any]:
    """대역 엔진으로 한 쪽을 훑는다(인자 반복을 줄이기 위한 소도구).

    Args:
        client: 엔진 대역.
        scope: 조건 지문 재료.
        cursor: 이어 읽기 커서(없으면 첫 쪽).

    Returns:
        ``browse_files`` 반환값.
    """
    return browse_files(client, "idx", query="", sort="created_desc", size=1,
                        cursor=cursor, scope=scope)


class 훑기_지문_왕복(unittest.TestCase):
    """① 조건이 같으면 종전처럼 이어진다."""

    def test_같은_조건이면_다음_쪽이_이어진다(self) -> None:
        engine = _StubEngine()
        first = _browse(engine, scope=_SCOPE_ALL)
        self.assertIsNotNone(first["next_cursor"], "꽉 찬 쪽이면 책갈피를 준다")
        second = _browse(engine, scope=_SCOPE_ALL, cursor=first["next_cursor"])
        self.assertEqual([r["asset_id"] for r in second["rows"]], ["a01"])


class 훑기_지문_거부(unittest.TestCase):
    """② 조건이 바뀐 커서 · ③ 지문 없는 옛 토큰."""

    def test_조건이_바뀐_커서는_엔진에_닿기_전에_끊긴다(self) -> None:
        """🔴 실측 결함 A — 전체 훑기 커서를 ``modality=text`` 에 쓰자 첫 건이 통째로 빠졌다."""
        engine = _StubEngine()
        token = _browse(engine, scope=_SCOPE_ALL)["next_cursor"]
        after_first = engine.calls
        with self.assertRaises(CursorError):
            _browse(engine, scope=_SCOPE_TEXT, cursor=token)
        self.assertEqual(engine.calls, after_first, "거부는 엔진 왕복 전에 나야 한다")

    def test_지문이_없는_옛_토큰을_거부한다(self) -> None:
        engine = _StubEngine()
        import base64
        import json
        raw = json.dumps({"o": "created_desc", "s": ["2026-09-11", "a01"]}).encode()
        old = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        with self.assertRaises(CursorError):
            _browse(engine, scope=_SCOPE_ALL, cursor=old)

    def test_개수가_틀린_커서는_지문이_맞아도_거부한다(self) -> None:
        """지문 검사가 생겨도 arity 검사(099 T005)가 살아 있어야 한다."""
        forged = encode_cursor("created_desc", ["2026-09-11"], scope=_SCOPE_ALL)
        with self.assertRaises(CursorError):
            _browse(_StubEngine(), scope=_SCOPE_ALL, cursor=forged)


if __name__ == "__main__":
    unittest.main()
