"""커서(책갈피) 인코딩 — **1만 건을 넘어 이어 읽기** 위한 불투명 토큰(097).

왜 커서인가: OpenSearch 는 ``from + size <= 10,000`` 을 넘으면 거부한다(실측 2026-09-14 ·
색인 16,864건). ``from`` 은 "앞에서부터 N 개를 꺼내 버린다"는 뜻이라 깊어질수록 비싸지기 때문이다.
``search_after`` 는 **직전 쪽 마지막 문서의 정렬값 다음부터** 라서 앞을 세지 않는다 — 실측으로
16,864건을 338쪽에 0.8초로 완주했고, 깊어져도 비용이 일정했다(1쪽 46ms · 338쪽 7ms).

왜 감싸나(날것 ``sort`` 배열을 그대로 내보내지 않는 이유):

1. **내부 정렬 키가 API 계약이 된다** — 한 번 내보내면 정렬 필드를 바꿀 수 없다.
2. **정렬이 바뀐 커서를 걸러야 한다** — 이름순으로 받은 책갈피를 최신순 요청에 쓰면 엉뚱한 자리에서
   이어진다. 🔴 **조용히 이어 주는 것이 가장 나쁘다**: 사용자는 목록이 틀린 줄 모르고, 나중에
   "그 파일이 왜 없지"로 나타난다. 그래서 정렬 이름을 함께 담아 **다르면 거부**한다.

🔴 **무엇을 훑던 책갈피인지도 함께 담는다**(099 G7 · 조건 지문). 정렬만 대조하면 **정렬이 같고
조건만 다른** 요청을 막지 못한다 — 실측(2026-09-17 · 실 DB·실 OS)에서 `q=사찰` 로 받은 커서를
`q=석탑` 요청에 넣자 서버가 200 으로 이어 주었고, 정상 1쪽에 있던 석굴암·경주시가 **통째로 빠졌다**.
오류가 나지 않으므로 사용자는 자료가 빠진 줄 모른다. 그래서 "이번 조회를 정의하는 것 전부"를 호출부가
문자열 하나(``scope``)로 모아 주면, 이 모듈은 그것의 **지문(해시)만** 담고 되읽을 때 대조한다.

왜 원문이 아니라 지문인가: ① 조건 원문을 토큰에 실으면 **내부 파라미터가 곧 계약**이 되어 나중에
바꿀 수 없다(위 2번과 같은 이유) ② 토큰이 조건 길이만큼 길어진다 ③ 질의어가 주소창에 그대로 보인다.
지문은 sha256 앞 16자리(64비트)라 **길이가 늘 같고 원문을 되돌릴 수 없다**. 비유하면 책갈피에 책
제목을 적는 대신 **도장**을 찍어 두고, 다음에 꺼낼 때 도장이 같은지만 보는 것이다.

🔴 **지문이 없는 옛 토큰은 거부한다**(깨는 변경 · CHANGELOG 명시). "없으면 통과"로 두면 옛 토큰
하나로 검사 전체를 우회할 수 있어 장치가 없는 것과 같다. 097 커서는 머지됐지만 프론트가 아직 배선
전이라 실사용자가 없어, 지금이 깰 수 있는 유일한 시점이다.

⚠️ 커서를 **신뢰 경계로 쓰지 않는다.** 서명하지 않으므로 위조할 수 있다 — 다만 위조해 봐야
"정렬값이 이상한 자리부터 읽기"일 뿐 권한을 넘지 못한다(권한 가리기는 응답 직전에 따로 걸린다).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

__all__ = ["encode_cursor", "decode_cursor", "CursorError"]


class CursorError(ValueError):
    """커서가 깨졌거나 요청한 정렬과 맞지 않는다 — 호출부가 400 으로 바꾼다."""


# 조건 지문의 길이(16진수 글자 수). sha256 앞 **64비트**만 쓴다 — 우연히 겹칠 확률이 사실상 0
# (서로 다른 조건 1억 가지를 견줘도 충돌 기대값이 1 미만)이면서 토큰이 짧게 유지된다.
# 🔴 지문은 **길이가 늘 같아야 한다**. 조건 길이에 따라 흔들리면 그 길이만으로 정보가 샌다.
_SCOPE_FINGERPRINT_LEN = 16


def _scope_fingerprint(scope: str) -> str:
    """조건 문자열을 **되돌릴 수 없는 고정 길이 도장**으로 바꾼다(순수 · 결정적).

    Args:
        scope: 호출부가 모은 "이번 조회를 정의하는 것 전부"(질의·좁히기·필터·임계 등).
            이 모듈은 내용을 해석하지 않는다 — **무엇을 넣을지는 호출부(화면 계층)의 책임**이며,
            코어가 화면 파라미터를 알면 경계가 무너진다(093 책무 경계).

    Returns:
        16자리 소문자 16진수. 같은 문자열이면 언제나 같고, 한 글자만 달라도 완전히 달라진다.
    """
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:_SCOPE_FINGERPRINT_LEN]


def encode_cursor(sort_name: str, sort_values: list[Any], *, scope: str) -> str:
    """마지막 문서의 정렬값을 **불투명 토큰**으로 감싼다(순수 · 같은 입력 → 같은 출력).

    Args:
        sort_name: 정렬 이름(``SORT_OPTIONS`` 의 키). 되읽을 때 대조해 **정렬이 바뀌면 거부**한다.
        sort_values: OpenSearch 응답 hit 의 ``sort`` 배열 그대로. 🔴 **마지막 원소는 고유값**
            (``asset_id``)이어야 한다 — 없으면 같은 정렬값이 쪽 경계에 걸릴 때 건너뛰거나 겹친다.
            이 함수는 그것을 **검사하지 않는다**(호출부의 정렬 정의가 보장한다 · 테스트가 봉인).
        scope: 이번 조회를 정의하는 것 전부를 호출부가 모은 문자열(099 G7). 토큰에는 원문이 아니라
            **지문**만 실린다. 🔴 **기본값을 두지 않는다** — 깜빡 빠뜨리면 검사가 조용히 사라져
            "조건이 다른 커서로 이어 읽기"(자료 누락)가 되살아난다. 조건이 하나도 없는 조회는
            빈 문자열을 주면 되고, 그것도 "조건 없음"이라는 **하나의 지문**이다.

    Returns:
        URL 에 실을 수 있는 base64 문자열(패딩 없음).

    Raises:
        CursorError: ``sort_values`` 가 비었을 때(이어 읽을 자리가 없다).
    """
    if not sort_values:
        raise CursorError("정렬값이 비어 커서를 만들 수 없다")
    raw = json.dumps({"o": sort_name, "s": list(sort_values), "f": _scope_fingerprint(scope)},
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expect_sort: str, expect_arity: int | None = None,
                  expect_scope: str) -> list[Any]:
    """커서를 풀어 정렬값을 돌려준다 — **정렬·개수·타입이 어긋나면 거부**한다.

    🔴 개수·타입을 여기서 보는 이유(099 T005 · 코드리뷰 2026-09-16): 통과시키면 그 값이 그대로
    ``search_after`` 로 흘러 검색 엔진이 400 을 내는데, 그 예외는 ``CursorError`` 가 아니라서
    호출부가 400 으로 바꾸지 못하고 **HTTP 500** 이 된다. 사용자가 주소창의 커서 한 글자를 고친 것뿐인데
    "서버 오류"가 뜨는 셈이다. 개체 목록(099)처럼 정렬 키 수가 다른 쓰임이 늘면 구버전 토큰으로도 같은
    일이 나므로, 쓰기 전에 문 앞에서 막는다.

    Args:
        token: ``encode_cursor`` 가 만든 문자열.
        expect_sort: 이번 요청의 정렬 이름. 커서에 담긴 것과 다르면 ``CursorError``.
        expect_arity: 이번 정렬이 쓰는 **정렬값 개수**(예: ``(수정일, asset_id)`` 면 2).
            개수가 다르면 ``CursorError``. ``None``(기본)이면 개수를 따지지 않는다 —
            종전 호출부를 깨지 않기 위한 하위호환이다. **타입(스칼라) 검사는 이 값과 무관하게 늘 돈다.**
        expect_scope: 이번 조회를 정의하는 것 전부(``encode_cursor`` 에 준 것과 **같은 문자열**).
            지문이 다르면 ``CursorError`` — 조건이 바뀐 커서로 이어 읽으면 **자료가 조용히 빠진다**
            (모듈 docstring 의 실측 예). 🔴 **기본값을 두지 않는다**(깜빡 빠뜨림 = 검사 소멸).

    Returns:
        ``search_after`` 에 그대로 넣을 정렬값 목록.

    Raises:
        CursorError: 토큰이 깨졌거나 · 모양이 다르거나 · 정렬이 어긋나거나 ·
            정렬값 개수가 ``expect_arity`` 와 다르거나 · 원소가 스칼라가 아니거나 ·
            **조건 지문이 없거나 다를 때**(099 G7 · 지문 없는 옛 토큰은 거부).
    """
    if not token:
        raise CursorError("커서가 비었다")
    pad = "=" * (-len(token) % 4)          # rstrip 한 패딩을 되돌린다
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        body = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CursorError(f"커서를 읽을 수 없다: {exc}") from exc
    if not isinstance(body, dict) or "o" not in body or "s" not in body:
        raise CursorError("커서 모양이 아니다")
    got = body["o"]
    if got != expect_sort:
        # 🔴 여기서 막지 않으면 엉뚱한 자리에서 조용히 이어진다(모듈 docstring 2).
        raise CursorError(f"커서의 정렬({got!r})이 요청({expect_sort!r})과 다르다 — 처음부터 다시 받아야 한다")
    # 🔴 지문 대조 — 정렬 검사만으로는 **정렬이 같고 조건만 다른** 요청을 못 막는다(실측 결함 A).
    #    "없으면 통과"로 두면 옛 토큰 하나로 검사 전체를 우회하므로, **없는 것도 거부**한다.
    got_fp = body.get("f")
    want_fp = _scope_fingerprint(expect_scope)
    if not isinstance(got_fp, str) or not got_fp:
        raise CursorError("커서에 조건 지문이 없다 — 처음부터 다시 받아야 한다(옛 토큰)")
    if got_fp != want_fp:
        raise CursorError("커서가 가리키는 조회 조건이 이번 요청과 다르다 — 처음부터 다시 받아야 한다")
    values = body["s"]
    if not isinstance(values, list) or not values:
        raise CursorError("커서에 정렬값이 없다")
    if expect_arity is not None and len(values) != expect_arity:
        # 구버전·위조 토큰. 개수가 맞지 않으면 엔진이 거부하거나 **엉뚱한 자리**에서 이어진다.
        raise CursorError(
            f"커서의 정렬값 개수({len(values)})가 요청({expect_arity})과 다르다 — 처음부터 다시 받아야 한다")
    for v in values:
        # 엔진의 ``search_after`` 가 받는 것은 스칼라뿐이다(bool 은 int 의 하위형이라 함께 통과).
        if not isinstance(v, (str, int, float)) and v is not None:
            raise CursorError(f"커서의 정렬값이 스칼라가 아니다: {type(v).__name__}")
    return values
