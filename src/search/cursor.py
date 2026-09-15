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

⚠️ 커서를 **신뢰 경계로 쓰지 않는다.** 서명하지 않으므로 위조할 수 있다 — 다만 위조해 봐야
"정렬값이 이상한 자리부터 읽기"일 뿐 권한을 넘지 못한다(권한 가리기는 응답 직전에 따로 걸린다).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

__all__ = ["encode_cursor", "decode_cursor", "CursorError"]


class CursorError(ValueError):
    """커서가 깨졌거나 요청한 정렬과 맞지 않는다 — 호출부가 400 으로 바꾼다."""


def encode_cursor(sort_name: str, sort_values: list[Any]) -> str:
    """마지막 문서의 정렬값을 **불투명 토큰**으로 감싼다(순수 · 같은 입력 → 같은 출력).

    Args:
        sort_name: 정렬 이름(``SORT_OPTIONS`` 의 키). 되읽을 때 대조해 **정렬이 바뀌면 거부**한다.
        sort_values: OpenSearch 응답 hit 의 ``sort`` 배열 그대로. 🔴 **마지막 원소는 고유값**
            (``asset_id``)이어야 한다 — 없으면 같은 정렬값이 쪽 경계에 걸릴 때 건너뛰거나 겹친다.
            이 함수는 그것을 **검사하지 않는다**(호출부의 정렬 정의가 보장한다 · 테스트가 봉인).

    Returns:
        URL 에 실을 수 있는 base64 문자열(패딩 없음).

    Raises:
        CursorError: ``sort_values`` 가 비었을 때(이어 읽을 자리가 없다).
    """
    if not sort_values:
        raise CursorError("정렬값이 비어 커서를 만들 수 없다")
    raw = json.dumps({"o": sort_name, "s": list(sort_values)},
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expect_sort: str) -> list[Any]:
    """커서를 풀어 정렬값을 돌려준다 — **정렬이 다르면 거부**한다.

    Args:
        token: ``encode_cursor`` 가 만든 문자열.
        expect_sort: 이번 요청의 정렬 이름. 커서에 담긴 것과 다르면 ``CursorError``.

    Returns:
        ``search_after`` 에 그대로 넣을 정렬값 목록.

    Raises:
        CursorError: 토큰이 깨졌거나 · 모양이 다르거나 · 정렬이 어긋날 때.
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
    values = body["s"]
    if not isinstance(values, list) or not values:
        raise CursorError("커서에 정렬값이 없다")
    return values
