"""수치 정화 — 어떤 값이든 **유한한 실수**로 바꾸는 규칙 하나(순수 · 의존 0).

왜 별도 모듈인가(093 1단계): 같은 로직이 코어 ``search.fusion._safe_float`` 와 백엔드 ``search_group``·
``routes_search`` 에 **세 벌** 있었다. 백엔드가 코어 것을 안 쓴 이유는 ``fusion`` 을 import 하면 검색·임베딩
모듈이 딸려 와 가벼운 모듈이 무거워지기 때문이었다. 그래서 규칙만 떼어 **표준 라이브러리만 쓰는** 여기에
두고, ``fusion`` 도 백엔드도 이 하나를 가져다 쓴다.

왜 필요한 규칙인가: NaN·무한대는 비교가 비결정적이다 — 정렬 키에 섞이면 같은 질의가 실행마다 다른 순서를
낸다(헌법 3조 결정 재현성). 그래서 정렬 전에 전부 걸러 낸다.
"""
from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """어떤 값이든 **유한한 실수**로 바꾼다(결정적·순수).

    Args:
        value: 숫자·문자열·``None`` 무엇이든. ``float()`` 로 바뀌지 않으면 ``default``.
        default: 변환 실패나 비유한 값(NaN·±inf)일 때 쓸 대체값.

    Returns:
        유한 실수.
    """
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


__all__ = ["safe_float"]
