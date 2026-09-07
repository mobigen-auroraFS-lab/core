"""검색 결과-스코프 **패싯 집계** 순수 함수 — 축과 무관한 "세는 규칙" 하나.

무엇을 하는 모듈인가: 지금 보이는 검색 결과 안에서 "어떤 값이 몇 건인가"를 세어 목록으로 만든다.
쇼핑몰 왼쪽 필터 목록("브랜드 A 12건 · 브랜드 B 7건")을 만드는 계산이다. 태그 축·주제 축·분류 스킬
축은 **무엇으로 묶는지**만 다르고 세는 규칙은 같아서, 규칙을 한 곳에 두고 호출부가 "묶는 법"
(``keys_of``)만 넘긴다. 어느 축을 보여줄지·상위 몇 개인지는 호출부(백엔드)가 정한다.

왜 한 곳인가(093 책무 경계 · 규칙 ①): 어디서 세어도 같은 건수·같은 순서가 나와야 한다. 세는 규칙
사본이 두 벌이면 한쪽만 고쳤을 때 화면 두 곳의 건수가 조용히 어긋난다(2026-09-02 감사 B1 — 백엔드
주제 패싯이 자기 규칙을 갖고 있었다).

세는 규칙(083 spec §③ · 헌법 3조 결정 재현성):
  - 한 **단위**는 한 키에 1로만 센다. 단위는 기본 "행 하나"이고, ``unit_of`` 를 주면 그 id(예: 자산 id)
    로 묶어 같은 자산이 여러 버킷에 있어도 1건이다.
  - 빈 키는 버린다. ``min_count`` 미만은 감춘다.
  - 정렬은 **건수 내림차순 → 표시 라벨 코드포인트 오름차순**(동률 tie-break 까지 고정).
  - 표시 라벨은 같은 키로 묶인 원문 중 최빈값(동률은 코드포인트 오름차순 첫 값). 축이 원하면
    ``label_of`` 로 바꿀 수 있다(닫힌 어휘 축은 키가 곧 라벨이라 투표가 필요 없다).

전부 순수 함수다 — DB·OpenSearch·LLM 을 부르지 않으며 같은 입력이면 언제나 같은 출력이다.
축별 특화(태그 정규화 등)는 ``tag_facets`` 같은 래퍼가 맡는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

__all__ = ["aggregate_facets", "display_label"]


def display_label(originals: list[str]) -> str:
    """같은 키로 묶인 원문들 중 **화면에 보여줄 표기 하나**를 고른다.

    규칙은 "가장 많이 쓰인 표기". 예를 들어 ``전통음식`` 10건·``전통 음식`` 6건이면 화면에는
    ``전통음식`` 로 뜬다(둘은 한 항목 16건으로 합쳐진다). 빈도가 같으면 **유니코드 코드포인트
    오름차순** 첫 값을 쓴다 — 사람이 보기엔 임의지만 **매번 같은 값**이 나와야 하기 때문이다
    (083 spec §SC 각주: "사전순"은 코드포인트 오름차순으로 정의한다).

    앞뒤 공백은 표기 차이로 보지 않는다(``"전통 음식 "`` 과 ``"전통 음식"`` 은 같은 후보로 센다) —
    화면에서 구분되지 않는 차이로 최빈 판정이 흔들리는 것을 막는다.

    Args:
        originals: 한 키 그룹에 속한 원문 문자열들(같은 값이 여러 번 들어온다 — 그 횟수가 빈도다).
            문자열이 아닌 값·공백뿐인 값은 후보에서 제외한다.

    Returns:
        표시 라벨. 후보가 하나도 없으면 ``""``(정상 경로에서는 호출부가 빈 키를 이미 배제한다).
    """
    counts = Counter(
        stripped for o in originals if isinstance(o, str) and (stripped := o.strip())
    )
    if not counts:
        return ""
    # -빈도 먼저, 그다음 라벨 자체 — 파이썬 문자열 비교가 곧 코드포인트 순서다.
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def aggregate_facets(
    rows: Iterable[Any],
    *,
    keys_of: Callable[[Mapping[str, Any]], Iterable[tuple[str, str]]],
    top_n: int | None = None,
    min_count: int = 1,
    unit_of: Callable[[Mapping[str, Any]], str] | None = None,
    label_of: Callable[[list[str]], str] = display_label,
) -> dict[str, Any]:
    """결과 행들에서 한 축의 패싯을 집계한다(결과-스코프 · 코퍼스 전체가 아니다).

    비유하면 장바구니에 담긴 물건들의 라벨을 세는 일이다 — 창고 전체 재고표가 아니라 **눈앞의
    결과**만 센다. 그래서 화면의 건수가 "누르면 몇 건이 될지"와 같아진다(083 spec §③·SC-02).

    Args:
        rows: 검색 결과 행들. dict 같은 매핑만 센다 — 매핑이 아닌 값이 섞여 오면 그 행은 건너뛴다
            (검색 백엔드가 예상과 다른 모양을 주더라도 집계가 죽지 않게).
        keys_of: 행 하나에서 ``(키, 원문 라벨)`` 짝들을 뽑는 함수 — "무엇으로 묶는가"가 이것이다.
            태그 축은 ``(정규화 키, 태그 원문)``, 주제 축은 ``(주제, 주제)`` 처럼 준다. 빈 키는 버리고,
            한 행 안에서 같은 짝이 반복돼도 1회로 본다(행 하나가 2건으로 부풀지 않게).
        top_n: 목록에 노출할 상위 개수(1 이상). ``None`` 이면 전부 노출하고 ``has_more`` 는 항상 거짓.
        min_count: 노출 하한 건수(1 이상). 1 이면 감추지 않는다.
        unit_of: 계수 단위 id 를 행에서 뽑는 함수. ``None`` 이면 **행 하나가 한 단위**다. 주면 같은 id
            의 행들은 한 키에 1로만 센다(자산 하나가 여러 모달리티 버킷에 있어도 1건). 빈 id 를 돌려준
            행은 세지 않는다.
        label_of: 같은 키로 묶인 원문 목록에서 표시 라벨을 고르는 함수. 기본은 최빈 표기
            (``display_label``). 키가 곧 라벨인 닫힌 어휘 축은 ``lambda o: o[0]`` 처럼 첫 원문을 쓴다.

    Returns:
        ``{"items": [{"label": 표시라벨, "count": 건수}, ...], "has_more": bool,
        "label_by_key": {키: 표시라벨}}``. ``label_by_key`` 는 **상위 목록에 들지 못한 키까지 포함**한다
        — 결과 행의 칩을 표시할 때 행이 가진 아무 키나 라벨로 바꿔야 하기 때문이다(083 spec §⑥).

    Raises:
        ValueError: ``top_n`` 이 1 미만이거나 ``min_count`` 가 1 미만일 때. 조용히 빈 목록을 돌려주면
            "값이 없는 결과"와 설정 오류가 구분되지 않는다(fail-fast).
    """
    if top_n is not None and top_n < 1:
        raise ValueError(f"패싯 상위 개수 범위 오류: top_n={top_n!r} (>=1 또는 None)")
    if min_count < 1:
        raise ValueError(f"패싯 노출 하한 범위 오류: min_count={min_count!r} (>=1)")

    units_by_key: dict[str, set[str]] = {}
    originals_by_key: dict[str, list[str]] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        # 단위 id: 기본은 행 번호(행마다 다르다) — 같은 자산을 한 번만 세고 싶으면 unit_of 가 준다.
        unit = f"#{idx}" if unit_of is None else unit_of(row)
        if not unit:
            continue
        # 행 안에서 같은 (키, 원문) 짝이 반복돼도 라벨 투표·계수는 1회(행이 2건으로 부풀지 않게).
        seen: set[tuple[str, str]] = set()
        for key, raw in keys_of(row):
            if not key or (key, raw) in seen:
                continue
            seen.add((key, raw))
            originals_by_key.setdefault(key, []).append(raw)
            units_by_key.setdefault(key, set()).add(unit)

    label_by_key = {k: label_of(v) for k, v in originals_by_key.items()}
    eligible = [
        {"label": label_by_key[key], "count": len(units)}
        for key, units in units_by_key.items()
        if len(units) >= min_count
    ]
    # 건수 내림차순 → 라벨 코드포인트 오름차순. 동률까지 정해 두어 같은 입력이면 언제나 같은 순서.
    eligible.sort(key=lambda item: (-item["count"], item["label"]))
    return {
        "items": eligible if top_n is None else eligible[:top_n],
        "has_more": top_n is not None and len(eligible) > top_n,
        "label_by_key": label_by_key,
    }
