"""045 Phase B — 검색 선필터(SearchFilters) · OS bool.filter 변환."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.search.tag_facets import normalize_tag_key


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """portal/CLI 명시 필터 — 자동 질의 승격 없음(044)."""

    file_exts: tuple[str, ...] = ()
    created_from: date | datetime | None = None
    created_to: date | datetime | None = None
    # 056 FR-503 · 096 복수화 — 주제/하위주제 keyword terms 필터. 색인된 ``topics``/``subtopics``
    # (keyword) 원문과 정확 일치해야 하므로 casefold/정규화하지 않는다(strip 만·parse 단계).
    # terms 절로 변환해 bool.filter 에 넣는다 → **결정적·랭킹 무영향**(topics_text boost 철회).
    #
    # 🔴 **여럿을 받는다**(096) — 같은 칸에서 여럿 고르면 「또는」이다(태그와 같은 규칙). 종전에는
    # 단일 값이었고 절은 이미 ``terms``(1원소 배열)로 만들고 있었다 — 그때 남긴 주석대로 "향후 다중
    # 선택 대비" 가 실현된 것이라 절 모양은 바뀌지 않는다. 순서는 첫 등장 순서를 보존한다.
    topics: tuple[str, ...] = ()
    subtopics: tuple[str, ...] = ()
    # 083 FR-201 — 태그(키워드) 필터. **원문 표기를 그대로 보관**하고(서비스가 "선택한 태그"를
    # 사용자에게 되돌려 보여줄 수 있어야 한다) 정규화는 절을 만들 때 한 번만 한다
    # (``filters_to_opensearch_bool``). 기본값 ``()`` 라 기존 호출부는 손대지 않아도 되고, 비어
    # 있으면 절 자체가 생기지 않아 **기존 질의 바디가 그대로**다(하위호환·동작 불변).
    tags: tuple[str, ...] = ()

    @property
    def topic(self) -> str | None:
        """⚠️ **낡은 이름**(096 이전) — 첫 주제 하나만 돌려준다.

        096 에서 주제 필터가 복수가 됐다. 종전 이름을 읽는 소비 코드가 조용히 깨지지 않게 남겨 두지만,
        **여럿 고른 상태를 표현하지 못한다** — 새 코드는 ``topics`` 를 읽어야 한다.

        Returns:
            첫 주제, 또는 조건이 없으면 ``None``.
        """
        return self.topics[0] if self.topics else None

    @property
    def subtopic(self) -> str | None:
        """⚠️ **낡은 이름**(096 이전) — 첫 하위주제 하나만 돌려준다(``topic`` 과 같은 이유).

        Returns:
            첫 하위주제, 또는 조건이 없으면 ``None``.
        """
        return self.subtopics[0] if self.subtopics else None


def _norm_names(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """주제·하위주제 이름을 정규화한다 — 문자열 하나든 여럿이든 받는다(096).

    색인된 keyword 원문과 **정확 일치**해야 하므로 앞뒤 공백만 다듬고 소문자화·유니코드 정규화는 하지
    않는다(그렇게 하면 색인 값과 어긋나 아무것도 걸리지 않는다).

    Args:
        value: 이름 하나(문자열) 또는 여럿(시퀀스) 또는 ``None``. 빈 값·공백뿐인 값은 버린다.

    Returns:
        중복을 없앤 이름 튜플(**첫 등장 순서 보존** — set 은 순서가 흔들린다).
    """
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else list(value)
    return tuple(dict.fromkeys(x.strip() for x in items if isinstance(x, str) and x.strip()))


def _norm_ext(value: str) -> str:
    """확장자를 비교 가능한 형태로 정규화한다 — 유니코드 정규화·소문자화·앞 ``.`` 제거.

    Args:
        value: 사용자가 준 확장자(``.JPG``·``jpg`` 등 표기가 제각각).

    Returns:
        정규화된 확장자(``jpg``).
    """
    return unicodedata.normalize("NFKC", value.strip()).casefold().lstrip(".")


def _parse_date_param(raw: str) -> date | datetime:
    """ISO 문자열을 날짜 또는 일시로 파싱한다.

    Args:
        raw: ``YYYY-MM-DD``(10자) 또는 ISO 8601 일시. 끝의 ``Z`` 는 ``+00:00`` 으로 바꿔 받는다.

    Returns:
        10자면 ``date``, 그 외는 ``datetime``.

    Raises:
        ValueError: ISO 형식이 아닐 때(``fromisoformat`` 이 던진다). 호출자(API)가 400 으로 변환한다.
    """
    text = raw.strip()
    if len(text) == 10:
        return date.fromisoformat(text)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def parse_search_filters(
    *,
    file_ext: list[str] | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    topic: str | Sequence[str] | None = None,
    subtopic: str | Sequence[str] | None = None,
    tag: list[str] | None = None,
) -> SearchFilters | None:
    """API 질의 파라미터를 ``SearchFilters`` 로 파싱한다(순수).

    Args:
        file_ext: 확장자 목록(반복 파라미터). 정규화·중복 제거·정렬해 담는다.
        created_from: 생성일 시작(ISO 문자열). 빈 문자열은 미지정으로 본다.
        created_to: 생성일 끝(ISO 문자열).
        topic: 주제 정확 일치 필터. 색인된 keyword 원문과 맞춰야 하므로 **소문자화하지 않고**
            앞뒤 공백만 자른다.
        subtopic: 세부주제 정확 일치 필터(같은 규칙).
        tag: 태그 목록(반복 파라미터 ``tag`` · 083). 앞뒤 공백만 자르고 **원문 표기·입력 순서를
            보존**한 채 중복만 없앤다 — 정규화는 절을 만들 때 하고(``filters_to_opensearch_bool``),
            여기 값은 화면에 "선택한 태그"로 되돌려 보여줄 수 있어야 한다.

    Returns:
        ``SearchFilters``. **하나도 지정되지 않았으면 ``None``** — 호출부가 "필터 없음"을
        빈 객체와 구분해 처리한다.

    Raises:
        ValueError: 날짜 문자열이 ISO 형식이 아닐 때.
    """
    exts = tuple(
        sorted({_norm_ext(x) for x in (file_ext or []) if x and x.strip()})
    )
    cf = _parse_date_param(created_from) if created_from and created_from.strip() else None
    ct = _parse_date_param(created_to) if created_to and created_to.strip() else None
    # 056: 주제/하위주제는 색인 keyword 원문과 정확 일치용이라 strip 만(casefold·소문자화 금지).
    # 096: 문자열 하나도 여럿도 받는다 — 종전 호출부(문자열)를 고치지 않아도 되게.
    topics_v = _norm_names(topic)
    subtopics_v = _norm_names(subtopic)
    # 083: dict.fromkeys 는 **첫 등장 순서를 보존한 채** 중복을 없앤다(set 은 순서가 흔들린다).
    tags_v = tuple(dict.fromkeys(x.strip() for x in (tag or []) if x and x.strip()))
    # 🔴 "아무 필터도 없음"(None) 판정에 tags 를 **반드시** 포함한다 — 빼먹으면 태그만 지정한
    # 요청이 여기서 None 이 되어 필터가 통째로 사라진다(태그 기능이 조용히 무력화).
    if (
        not exts
        and cf is None
        and ct is None
        and not topics_v
        and not subtopics_v
        and not tags_v
    ):
        return None
    return SearchFilters(
        file_exts=exts,
        created_from=cf,
        created_to=ct,
        topics=topics_v,
        subtopics=subtopics_v,
        tags=tags_v,
    )


def _to_utc_date(value: date | datetime) -> str:
    """OpenSearch date 필드에 넣을 ISO 날짜 문자열로 바꾼다(일 단위).

    Args:
        value: 날짜 또는 일시. 일시면 **시각을 버리고 날짜만** 쓴다.

    Returns:
        ``YYYY-MM-DD``.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def filters_to_opensearch_bool(filters: SearchFilters | None) -> list[dict[str, Any]]:
    """``SearchFilters`` 를 OpenSearch ``bool.filter`` 절 목록으로 바꾼다(BM25·kNN 공통).

    filter 절은 **점수에 기여하지 않는다** — 걸러내기만 하므로 랭킹이 흔들리지 않는다.

    Args:
        filters: 파싱된 필터. ``None`` 이면 빈 목록을 돌려준다(필터 없음).

    Returns:
        절 dict 목록. 지정된 항목만 들어가며, 아무것도 없으면 빈 목록.
    """
    if filters is None:
        return []
    clauses: list[dict[str, Any]] = []
    if filters.file_exts:
        clauses.append({"terms": {"filter_kw.file_ext": sorted(filters.file_exts)}})
    if filters.created_from is not None:
        gte = _to_utc_date(filters.created_from)
        clauses.append({"range": {"filter_date.created_at": {"gte": gte}}})
    if filters.created_to is not None:
        lte = _to_utc_date(filters.created_to)
        clauses.append({"range": {"filter_date.created_at": {"lte": lte}}})
    # 056 FR-503 — 주제/하위주제 terms 필터. 색인된 keyword 필드(top-level ``topics``/``subtopics``·
    # opensearch_sync.build_index_body)에 정확 일치. 단일 값도 terms(1원소 배열)로 두어 향후 다중
    # 확장(반복 파라미터)과 형상 일관. filter 절이라 점수 기여 0 → 랭킹 무영향(결정적).
    # 096: 여럿이면 그대로 배열로 넣는다 — ``terms`` 는 값들의 **또는** 이므로 하나라도 맞으면 남는다.
    if filters.topics:
        clauses.append({"terms": {"topics": list(filters.topics)}})
    if filters.subtopics:
        clauses.append({"terms": {"subtopics": list(filters.subtopics)}})
    # 083 FR-201 — 태그 필터. 색인 시점(``asset_to_doc``)이 ``keywords_norm`` 에 넣은 키와 **같은
    # 함수**(normalize_tag_key)로 맞춘다 — 두 쪽이 다른 규칙을 쓰면 필터가 아무것도 못 찾는다.
    # terms 는 **값들의 OR** 이므로 태그 여럿을 고르면 "이 중 하나라도 가진 자산"이 남는다
    # (083 spec §④ — AND 아님). 값을 정렬해 두어 고른 순서가 달라도 질의 바디가 같아진다
    # (결정적·OS 요청 캐시 친화). 정규화 결과가 빈 키는 버리고, 남는 키가 없으면 **절 자체를
    # 만들지 않는다** — 빈 terms 는 모든 문서를 배제해 결과가 통째로 사라진다.
    if filters.tags:
        tag_keys = sorted({k for k in (normalize_tag_key(t) for t in filters.tags) if k})
        if tag_keys:
            clauses.append({"terms": {"keywords_norm": tag_keys}})
    return clauses


__all__ = [
    "SearchFilters",
    "filters_to_opensearch_bool",
    "parse_search_filters",
]
