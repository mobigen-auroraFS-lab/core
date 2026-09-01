"""091 — 검색 **결과 내 재검색**(글자 좁히기) 순수 함수. DB·OpenSearch·LLM 불필요.

무엇을 하는 모듈인가: 이미 받아 든 검색 결과를 **글자로 한 번 더 좁힌다**. 쇼핑몰에서 "노트북"을
검색해 200개를 받은 뒤 목록 위 작은 칸에 "16인치"를 치면 그 자리에서 줄어드는 것과 같다 —
새로 검색하는 게 아니라 **눈앞의 결과를 걸러낸다**.

**왜 필요한가**(083 dev 실측): 같은 단어로 검색하면 그 태그를 가진 자산은 풀에 거의 다 들어오지만
**상위 10건의 절반은 그 태그가 없다**(정밀도 중위 50%). 즉 검색은 데려오되 골라내지 못한다.
083 은 그 좁히기를 닫힌 어휘(태그 패싯 클릭)로 냈고, 이 모듈은 **열린 글자**로 좁힌다 — 패싯에
뜨지 않는 말(파일명 조각·요약문 안의 낱말)로도 걸러야 하기 때문이다.

**왜 서버에 다시 묻지 않나**(spec 091 §2-3): 083 이 같은 갈림길에서 **클라이언트 좁히기**로
결론냈다(2026-08-24 사용자 확정). ① 건수가 화면과 일치한다("47건 중 12건"이 정의상 성립)
② 재검색 방식은 93항목 중 **80%만 일치**했고 컷오프와 상호작용했다. ②는 090 게이트 도입으로
더 분명해졌다 — 질의가 바뀌면 **게이트가 다시 판정해** 없던 것이 나타난다.
대가로 상위 N 밖은 좁히기 대상이 아니며, 그래서 **화면이 스코프를 밝힌다**.

**왜 토큰으로 쪼개나**(089 실측): 089 는 통짜 부분 문자열이었고 다어절 질의 30개 중 **29개가
0건**이었다 — `김치 담그기` 라는 글자가 어느 필드에도 연속으로 있을 리 없다. 다만 089 는
찾아오기(recall)라 **OR** 였고, 여기는 골라내기라 **AND** 다. 좁히기에서 OR 를 쓰면 좁혀지지 않는다.

**왜 필드를 이어붙이지 않나**: 정규화(``normalize_text_key``)가 **공백을 지운다**. 필드를 이어
붙이면 경계를 넘어 우연히 걸린다 — 파일명이 `된장` 으로 끝나고 요약이 `국…` 으로 시작하면
`된장국` 이 걸리는데 그 자산은 된장국과 무관하다. 그래서 추출기는 **필드 목록**을 돌려주고
검사도 필드 단위로 한다.

**왜 필드 이름을 여기서 모르나**: 자산 행은 ``file_name``·``summary``·``tags``, 개체 행은
``name``·``keywords``·``description`` 이다. 이 모듈에서 분기하면 세 번째 화면이 생길 때 분기가
또 는다. **"행에서 검색 대상 글자를 뽑는 함수"를 인자로 받아** 도메인을 모르게 둔다
(헌법 4조의 축소판 · 008 이 cross_asset 슬롯을 주입 seam 으로 만든 것과 같은 모양).

여기 있는 것은 전부 **순수 함수**다 — 같은 입력이면 언제나 같은 출력이고 입력 행을 고치지 않는다
(헌법 3조 결정 재현성).

설계 배경: `specs/091-search-within-results`(spec §2 · plan §설계 결정)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.domain.text_norm import normalize_text_key


def refine_tokens(q: str | None) -> tuple[str, ...]:
    """재검색어를 공백으로 쪼개 **정규화된 토큰**으로 만든다(빈 토큰은 버린다).

    🔴 정규화는 코어 정본 ``normalize_text_key`` **하나만** 쓴다(083 태그 키·084 개체 키·
    089 질의 토큰과 같은 함수). 여기서 규칙을 새로 정의하면 표기 해석이 두 벌이 되어, 같은 글자가
    색인에서는 걸리고 필터에서는 안 걸리는 일이 생긴다. 규칙은 NFKC → 공백 제거 → casefold 다.

    🔴 **최소 길이 필터를 두지 않는다**(089 T004 ⑦ 이 실측으로 기각한 사안). 한 글자를 버리면
    `강`·`산` 같은 재검색어가 토큰 0개가 되고, 토큰 0개는 "좁히지 않음"으로 해석되므로 화면에서는
    **좁히기가 고장 난 것**처럼 보인다.

    ⚠️ 089 ``split_query`` 와 규칙은 같지만 별도 함수로 둔다 — 그쪽은 찾아오기(OR)용 mm_meta
    소관이고 이쪽은 골라내기(AND)용 검색 소관이다. 공유하는 것은 **정규화 정본 한 곳**이다.

    Args:
        q: 재검색어 원문. ``None``·빈 문자열·공백뿐이면 토큰이 없다(호출부가 "좁히지 않음"으로 읽는다).

    Returns:
        정규화된 토큰들(입력 순서 유지 · 중복도 그대로). 토큰이 없으면 빈 튜플.
    """
    if not q:
        return ()
    tokens = (normalize_text_key(part) for part in q.split())
    return tuple(t for t in tokens if t)


def refine_rows(
    rows: Sequence[Mapping[str, Any]],
    q: str | None,
    *,
    fields_of: Callable[[Mapping[str, Any]], Sequence[str]],
) -> list[dict[str, Any]]:
    """결과 행들을 재검색어로 좁힌다 — **토큰 AND · 원 순서 유지**(순수).

    남기는 조건은 "**모든** 토큰이 **어느 필드에든** 있다"이다. 토큰끼리는 AND, 한 토큰 안에서
    필드끼리는 OR 다 — `전통음식 배추` 처럼 한 낱말은 태그에, 다른 낱말은 요약에 있는 경우가
    흔하므로 한 필드에 다 있기를 요구하면 실제로 거의 걸리지 않는다.

    🔴 **원 순서를 뒤집지 않는다.** 재검색은 필터이지 재랭킹이 아니다 — 순위가 바뀌면 사용자가
    방금 본 화면과 달라진다(spec 091 D5 · 미달이면 즉시 되돌림).

    Args:
        rows: 좁힐 결과 행들(검색 응답 모양). 이 목록도 각 행도 고치지 않는다.
        q: 재검색어. ``None``·빈 문자열·공백뿐이면 **좁히지 않고 원 결과를 그대로** 돌려준다
            (되돌림의 실질 — 칸을 비우면 좁히기 전으로 돌아간다).
        fields_of: 행 하나에서 **검색 대상 필드 목록**을 뽑는 함수(``asset_refine_fields`` 등).
            이 함수가 도메인을 모르게 하는 주입 seam 이다.

    Returns:
        조건을 만족한 행들의 **얕은 사본**(원 순서). 조건에 맞는 행이 없으면 빈 목록.
    """
    tokens = refine_tokens(q)
    if not tokens:
        return [dict(row) for row in rows]

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue  # 검색 백엔드가 예상 밖 모양을 주더라도 좁히기 전체가 죽지 않게.
        try:
            fields = fields_of(row)
        except Exception:  # noqa: BLE001 — 한 행의 추출 실패로 목록 전체를 잃지 않는다.
            continue
        haystacks = [n for n in (normalize_text_key(str(f)) for f in fields) if n]
        if not haystacks:
            continue
        if all(any(token in hay for hay in haystacks) for token in tokens):
            out.append(dict(row))
    return out


def asset_refine_fields(
    row: Mapping[str, Any], *, summary: str | None = None
) -> list[str]:
    """자산 결과 행에서 **검색 대상 필드**를 뽑는다(파일명 · 요약 · 태그).

    셋을 고른 이유는 **화면 카드에 실제로 보이는 값**이기 때문이다 — "보이는 것으로 걸러진다"는
    계약이 서면 사용자가 결과에 놀라지 않는다.

    ⚠️ **내부키를 쓰지 않는다.** 같은 행에 ``_kwtext``(키워드+파일명 합본)·``_about``·``_rrtext``
    가 있지만 ``_`` 접두라 응답 전에 제거되고, ``_kwtext`` 는 파일명이 이미 섞여 있어 이중 계산이
    된다(리랭커·aboutness 필터 전용 · `fusion.py` 참조).

    Args:
        row: 검색 결과 행. ``file_name``(str)·``summary``(str)·``tags``(str 배열)를 읽으며,
            없거나 타입이 다르면 그 축은 없는 것으로 본다(083 ``aggregate_tag_facets`` 와 같은 방어 —
            백엔드가 모양을 바꿔도 좁히기가 죽지 않는다).
        summary: 요약 **클립 전 원문**. compact 응답은 요약을 자르는데, 잘린 글자로 거르면
            "화면엔 보이는데 안 걸림"이 생긴다. 사용자는 "이 자산의 요약에 그 말이 있나"를 묻는
            것이므로 원문을 본다(spec 091 §2-5). ``None`` 이면 행의 ``summary`` 를 쓴다.

    Returns:
        빈 값을 제외한 필드 문자열 목록(파일명 → 요약 → 태그 순).
    """
    out: list[str] = []
    file_name = row.get("file_name")
    if isinstance(file_name, str) and file_name:
        out.append(file_name)

    text = summary if summary is not None else row.get("summary")
    if isinstance(text, str) and text:
        out.append(text)

    tags = row.get("tags")
    # 문자열 하나가 오면 글자 단위로 순회돼 쓰레기 값이 생긴다 — 배열만 받는다.
    if isinstance(tags, (list, tuple)):
        out.extend(t for t in tags if isinstance(t, str) and t)
    return out


__all__ = ["asset_refine_fields", "refine_rows", "refine_tokens"]
