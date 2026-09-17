"""099 G7 — 커서의 **조건 지문**(무엇을 훑고 있었나) · 순수 단위(엔진·DB 불필요).

무엇을 막나: 커서는 "여기까지 읽었다"를 적어 둔 책갈피다. 그런데 종전 커서에는 **어느 책을 읽던
책갈피인지**가 없었다. 그래서 다른 조건(다른 검색어·다른 필터)의 요청에 끼워 넣어도 서버가 200 으로
이어 주고, 사용자는 **자료가 빠진 줄 모른다**.

🔴 실측(2026-09-17 · 실 DB·실 OS):

    개체 q='사찰' 커서를 q='석탑' 에 사용 → 200
        석탑 1쪽(정상): 경주시 · 국립중앙박물관 · 법화경 · 석굴암 · 부여군
        석탑+딴커서   : 운문사 · 대흥사 · 불국사 · 선암사 · 화엄사   ← 석굴암 등이 통째로 누락
    파일 전체 훑기 커서를 modality=text 에 사용 → 200, 첫 건 누락

여기서 봉인하는 것 넷:
    ① 같은 조건이면 왕복이 성립한다(지문이 생겨도 종전 쓰임이 죽지 않는다).
    ② 조건이 다르면 **거부**한다(``CursorError`` → 호출부가 400).
    ③ **지문이 없는 옛 토큰도 거부**한다 — 안전한 쪽을 고른다(깨는 변경 · CHANGELOG 명시).
    ④ 지문은 **불투명**하다 — 조건 원문이 토큰에 그대로 실리면 그것이 곧 계약이 되어
       나중에 내부 파라미터를 못 고친다(097 이 날것 ``sort`` 배열을 감싼 것과 같은 이유).
"""

from __future__ import annotations

import base64
import json
import unittest

from src.search.cursor import CursorError, decode_cursor, encode_cursor

_SCOPE_A = 'q=김치|refine=|modality=["video"]'
_SCOPE_B = 'q=김치|refine=|modality=["text"]'


def _forge(body: dict) -> str:
    """검증을 건너뛰고 임의 본문을 담은 토큰을 만든다(구버전·위조 흉내).

    Args:
        body: 토큰에 담을 dict(그대로 직렬화한다).

    Returns:
        base64 커서 문자열.
    """
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class 지문_왕복(unittest.TestCase):
    """① 같은 조건이면 종전처럼 이어 읽는다."""

    def test_같은_지문이면_정렬값을_그대로_돌려준다(self) -> None:
        values = ["2026-09-11T17:46:00", "018f0000-0000-7000-8000-000000000001"]
        token = encode_cursor("created_desc", values, scope=_SCOPE_A)
        self.assertEqual(
            decode_cursor(token, expect_sort="created_desc", expect_scope=_SCOPE_A), values)

    def test_같은_입력은_같은_토큰이다(self) -> None:
        """헌법 3조(결정 재현성) — 지문이 생겨도 같은 입력이면 같은 문자열이어야 한다."""
        a = encode_cursor("name_asc", ["가.mp4", "01a0"], scope=_SCOPE_A)
        b = encode_cursor("name_asc", ["가.mp4", "01a0"], scope=_SCOPE_A)
        self.assertEqual(a, b)

    def test_지문이_있어도_패딩_없는_URL_토큰이다(self) -> None:
        token = encode_cursor("size_asc", [123, "01a0"], scope=_SCOPE_A)
        self.assertNotIn("=", token)

    def test_개수_검사와_함께_써도_통과한다(self) -> None:
        """arity 검사(099 T005)와 지문 검사는 **둘 다** 돈다 — 한쪽만 통과해선 안 된다."""
        token = encode_cursor("created_desc", ["2026-09-11", "01a0"], scope=_SCOPE_A)
        self.assertEqual(
            decode_cursor(token, expect_sort="created_desc", expect_arity=2,
                          expect_scope=_SCOPE_A),
            ["2026-09-11", "01a0"])


class 지문_거부(unittest.TestCase):
    """② 조건이 다르면 거부 · ③ 지문 없는 옛 토큰도 거부."""

    def test_조건이_다르면_거부한다(self) -> None:
        """🔴 실측 결함 A — 조용히 이어 주면 자료가 통째로 빠진다."""
        token = encode_cursor("created_desc", ["2026-09-11", "01a0"], scope=_SCOPE_A)
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="created_desc", expect_scope=_SCOPE_B)

    def test_지문이_없는_옛_토큰을_거부한다(self) -> None:
        """097 커서는 머지됐지만 프론트가 아직 배선 전이라 실사용자가 없다 — 안전한 쪽을 고른다."""
        token = _forge({"o": "created_desc", "s": ["2026-09-11", "01a0"]})
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="created_desc", expect_scope=_SCOPE_A)

    def test_지문이_빈_문자열이어도_조건이_다르면_거부한다(self) -> None:
        """조건이 하나도 없는 조회("전체 훑기")도 **그 사실 자체**가 지문이다."""
        token = encode_cursor("created_desc", ["2026-09-11", "01a0"], scope="")
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="created_desc", expect_scope=_SCOPE_B)
        # 같은 "조건 없음"끼리는 통한다.
        self.assertEqual(
            decode_cursor(token, expect_sort="created_desc", expect_scope=""),
            ["2026-09-11", "01a0"])

    def test_지문이_망가진_토큰을_거부한다(self) -> None:
        """위조 — 지문 자리에 엉뚱한 타입을 넣어도 통과하면 안 된다."""
        for bad in (123, None, ["x"], {"a": 1}):
            with self.subTest(bad=bad):
                token = _forge({"o": "created_desc", "s": ["2026-09-11", "01a0"], "f": bad})
                with self.assertRaises(CursorError):
                    decode_cursor(token, expect_sort="created_desc", expect_scope=_SCOPE_A)

    def test_정렬이_같아도_조건이_다르면_거부한다(self) -> None:
        """정렬 검사만으로는 못 잡는다 — 실측 결함은 **정렬이 같은** 요청에서 났다."""
        token = encode_cursor("confirmed_count_desc", [1, 5, "사찰"], scope="q=사찰")
        with self.assertRaises(CursorError):
            decode_cursor(token, expect_sort="confirmed_count_desc", expect_scope="q=석탑")


class 지문_불투명성(unittest.TestCase):
    """④ 조건 원문이 토큰으로 새면 그것이 계약이 된다."""

    def _body(self, token: str) -> dict:
        """토큰을 풀어 본문 dict 를 돌려준다(테스트 전용 — 계약이 아니다).

        Args:
            token: 커서 문자열.

        Returns:
            토큰에 담긴 dict.
        """
        pad = "=" * (-len(token) % 4)
        return json.loads(base64.urlsafe_b64decode(token + pad).decode("utf-8"))

    def test_조건_원문이_토큰에_실리지_않는다(self) -> None:
        scope = 'q=비밀낱말|modality=["video"]'
        token = encode_cursor("created_desc", ["2026-09-11", "01a0"], scope=scope)
        self.assertNotIn("비밀낱말", base64.urlsafe_b64decode(token + "==").decode("utf-8", "ignore"))
        body = self._body(token)
        self.assertNotIn(scope, json.dumps(body, ensure_ascii=False))

    def test_지문은_고정_길이_16자리_16진수다(self) -> None:
        """길이가 조건 길이에 따라 흔들리면 그것만으로도 정보가 샌다."""
        short = self._body(encode_cursor("created_desc", [1, "a"], scope="q=가"))["f"]
        long = self._body(
            encode_cursor("created_desc", [1, "a"], scope="q=" + "가" * 500))["f"]
        self.assertEqual(len(short), 16)
        self.assertEqual(len(long), 16)
        self.assertRegex(short, r"^[0-9a-f]{16}$")

    def test_조건이_다르면_지문도_다르다(self) -> None:
        a = self._body(encode_cursor("created_desc", [1, "a"], scope=_SCOPE_A))["f"]
        b = self._body(encode_cursor("created_desc", [1, "a"], scope=_SCOPE_B))["f"]
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
