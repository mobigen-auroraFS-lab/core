"""검색 결과-스코프 **태그 패싯** 순수 함수 — 정규화 키·표시 라벨·집계(태그 축 특화 래퍼).

무엇을 하는 모듈인가: 적재 때 요약기가 만들어 둔 키워드(``ext_meta['keywords']``)를 주제와 별개의
**"태그" 축**으로 보여주기 위한 계산을 모아 둔다. 온라인 쇼핑의 왼쪽 필터 목록과 같은 역할이다 —
지금 보이는 결과 안에서 "많이 등장한 태그 12개 + 각 건수"를 뽑아 주고, 사용자가 하나를 누르면
그 태그를 가진 자산만 남는다(필터 절 조립은 이 모듈이 아니라 ``search_filters`` 몫).

왜 태그 축이 필요한가(083 spec §왜 — dev 실측): 같은 단어를 검색했을 때 그 태그를 가진 자산은
풀에는 거의 다 들어오지만(회수는 충분하다) **상위 10건의 절반은 그 태그가 없는 자산**이다.
즉 검색은 데려오되 골라내지 못한다(측정 수치는 spec 083 — 코드에 두면 재측정 때마다 낡는다). 태그 필터는 정의상 정확도 100%이고,
``검은색``·``자연``·``스포츠`` 처럼 검색으로는 도달조차 못 하는 태그의 **유일한 경로**다.

세는 규칙(행당 1회 계수·하한·결정적 정렬·최빈 표기)은 축과 무관하므로 ``src.search.facets`` 의
``aggregate_facets`` 하나에 있고(093 2단계), 이 모듈은 **태그 축의 "묶는 법"** — 원문 태그 배열을
정규화 키로 묶는 것 — 만 보탠다. 서비스 레포는 이 함수를 호출하는 배선만 하고 판단 로직을 자체
구현하지 않는다(083 spec §레포 배치).

설계 배경: ``specs/083-search-tag-facet`` §②·§③·§④
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from src.domain.text_norm import normalize_text_key
from src.search.facets import aggregate_facets, display_label

__all__ = ["aggregate_tag_facets", "display_label", "normalize_tag_key"]

# 태그 정규화 키는 **도메인 정본의 재수출**이다(별 구현 금지 — 색인 시점 ``asset_to_doc`` 과 검색
# 시점 필터가 같은 함수를 써야 키가 갈라지지 않는다. 083 plan §Global Constraints).
# 084 멀티모달 메타의 개체 키 정규화도 같은 함수를 쓴다.
normalize_tag_key = normalize_text_key


def _tag_keys_of(row: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """행의 태그 배열을 ``(정규화 키, 원문)`` 짝으로 낸다 — 태그 축의 "묶는 법".

    Args:
        row: 결과 행. ``tags`` 가 없거나 배열이 아니면(문자열 하나 등) 태그가 없는 것으로 본다 —
            문자열 하나가 오면 글자 단위로 순회돼 쓰레기 키가 생기므로 배열만 받는다.

    Yields:
        ``(normalize_tag_key(원문), 원문)``. 문자열이 아닌 항목은 건너뛴다. 빈 키 제거·행 안 중복
        1회 처리는 ``aggregate_facets`` 가 한다.
    """
    tags = row.get("tags")
    if not isinstance(tags, (list, tuple)):
        return
    for raw in tags:
        if isinstance(raw, str):
            yield normalize_tag_key(raw), raw


def aggregate_tag_facets(
    rows: list[dict[str, Any]], *, top_n: int, min_count: int
) -> dict[str, Any]:
    """지금 보이는 결과 행들에서 태그 패싯을 집계한다(결과-스코프 · 코퍼스 전체가 아니다).

    비유하면 장바구니에 담긴 물건들의 라벨을 세는 일이다 — 창고 전체 재고표가 아니라 **눈앞의
    결과**만 센다. 그래서 화면의 건수가 "누르면 몇 건이 될지"와 같아진다(083 spec §③·SC-02).

    계수 규칙(``aggregate_facets`` 정본 — 여기서는 태그 축의 묶는 법만 보탠다):
      - **행 하나는 어떤 태그에도 1로만 센다.** 같은 행에 ``전통음식``·``전통 음식`` 이 둘 다 있어도
        정규화 키가 같으므로 1건이다(자산 1건이 2건으로 부풀지 않게).
      - 정규화 후 빈 키(공백뿐인 태그)는 버린다.
      - ``min_count`` 미만은 목록에서 감춘다. 실측상 태그의 대다수가 1건짜리라, 감추지 않으면
        목록이 파편으로 찬다(083 spec §③).
      - 정렬은 **건수 내림차순 → 라벨 코드포인트 오름차순**. 동률 tie-break 까지 정해 두어 같은
        입력이면 언제나 같은 순서가 나온다(헌법 3조 · SC-06).

    Args:
        rows: 검색 결과 행들. 각 행은 ``{"tags": ["전통음식", ...]}`` 처럼 원문 태그 **배열**을
            가진 dict 로 본다. ``tags`` 가 없거나 배열이 아니면(문자열 하나 등) 그 행은 태그가
            없는 것으로 취급한다 — 검색 백엔드가 예상과 다른 모양을 주더라도 집계가 죽지 않게.
        top_n: 목록에 노출할 상위 개수(1 이상). 잘린 나머지가 있으면 ``has_more`` 가 참이 된다.
        min_count: 노출 하한 건수(1 이상). 기본 운영값 2 = "1건짜리 태그는 감춘다".

    Returns:
        ``{"items": [{"label": 표시라벨, "count": 건수}, ...], "has_more": bool,
        "label_by_key": {정규화키: 표시라벨}}``. ``label_by_key`` 는 **상위 목록에 들지 못한 키까지
        포함**한다 — 결과 행의 태그 칩을 표시할 때 행이 가진 아무 키나 라벨로 바꿔야 하기 때문이다
        (083 spec §⑥ — 행은 원문을 나르고, 라벨 결정은 결과 전역을 보는 이 함수가 한다).

    Raises:
        ValueError: ``top_n`` 또는 ``min_count`` 가 1 미만일 때. 조용히 빈 목록을 돌려주면
            "태그가 없는 결과"와 설정 오류가 구분되지 않는다(fail-fast).
    """
    if top_n < 1:
        raise ValueError(f"태그 패싯 상위 개수 범위 오류: top_n={top_n!r} (>=1)")
    if min_count < 1:
        raise ValueError(f"태그 패싯 노출 하한 범위 오류: min_count={min_count!r} (>=1)")
    return aggregate_facets(rows, keys_of=_tag_keys_of, top_n=top_n, min_count=min_count)
