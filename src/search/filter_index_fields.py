"""045 Phase B — OS 색인·필터용 파생 필드(순수·결정적)."""

from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime
from typing import Any

from src.config.filename_util import display_file_name


def derive_file_ext(fs_path: str) -> str | None:
    """경로에서 확장자를 뽑아 정규화한다(소문자·유니코드 NFKC).

    Args:
        fs_path: 자산 경로. 빈 값도 받는다.

    Returns:
        확장자(``jpg``). 점이 없거나 점으로 끝나면 ``None``.
    """
    base = os.path.basename(fs_path or "")
    if "." not in base or base.endswith("."):
        return None
    ext = base.rsplit(".", 1)[-1].strip()
    if not ext:
        return None
    return unicodedata.normalize("NFKC", ext).casefold()


def derive_source_dataset(fs_path: str) -> str:
    """경로 규칙으로 source_dataset 키를 반환한다(ADR 2026-06-24-search-filter-v1).

    규칙 순서: ``sample_data/data1|2|3`` 경로면 그 키 → 아니면 경로에 ``wikipedia``/``youtube`` 가 있으면
    그 출처 → 그 외 ``unknown``. 현행 코퍼스는 새 경로 ``data_all`` 이라 data1/2/3 규칙은 불발하고,
    파일명·경로의 ``youtube``/``wikipedia`` substring 폴백으로 분류된다(그 외 파일은 unknown 패싯).

    ℹ️ 이 값은 **색인에만 남는다** — 검색 필터로는 노출하지 않는다(경로 휴리스틱이라 약 70%가
    ``unknown`` 이라 패싯으로 쓸모가 없었다). 출처 추적용 메타로만 존치한다.

    Args:
        fs_path: 자산 경로.

    Returns:
        출처 키(``data1``|``data2``|``data3``|``wikipedia``|``youtube``|``unknown``).
    """
    p = unicodedata.normalize("NFKC", fs_path or "").casefold().replace("\\", "/")
    for n in ("data1", "data2", "data3"):
        if f"/sample_data/{n}/" in p or f"sample_data/{n}/" in p:
            return n
    if "wikipedia" in p:
        return "wikipedia"
    if "youtube" in p:
        return "youtube"
    return "unknown"


def _coerce_filter_date(value: Any) -> str | None:
    """생성 시각을 OpenSearch date 필드용 문자열로 바꾼다(일 단위).

    Args:
        value: ``datetime``·``date``·PostgreSQL timestamptz 문자열·``None`` 무엇이든 받는다.
            문자열은 앞 10자만 취한다(시각·시간대는 버린다).

    Returns:
        ``YYYY-MM-DD``. 값이 없거나 알 수 없는 타입이면 ``None``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        # PG timestamptz 문자열 — 앞 10자 date
        return value.strip()[:10]
    return None


def build_filter_index_fields(
    *,
    fs_path: str,
    created_at: Any = None,
    updated_at: Any = None,
    file_size: Any = None,
) -> dict[str, Any]:
    """색인 문서에 병합할 필터·정렬 전용 필드를 만든다(순수).

    **096 정렬**: 파일 검색 화면이 표에 찍는 값(표시용 파일명·크기·수정일)으로 줄을 세울 수 있어야
    한다. 색인에 없으면 정렬 자체가 불가능하다 — 검색 엔진은 색인된 필드로만 줄을 세운다.

    🔴 **표시명은 화면에 보이는 그 문자열**이다(``display_file_name``) — 이미 있는 ``file_name`` 은
    잡음을 정제한 **검색용** 값이라 화면 값과 다르다(실측: 1,526건 전부 다르고, 그 필드로 줄을 세우면
    화면의 84.5%가 제자리에 오지 않는다). 두 값을 섞으면 "이름순으로 눌렀는데 이름순이 아닌 표"가 된다.

    ⚠️ **수정일은 날짜까지만** 담는다(생성일과 같은 규칙). 화면 표도 날짜까지만 보이므로 **눈에 보이는
    만큼만** 정렬 기준이 되고, 같은 날짜끼리의 순서는 호출부의 동률 기준(자산 id)이 정한다.

    Args:
        fs_path: 자산 경로(확장자·출처·표시명을 여기서 파생한다).
        created_at: 생성 시각. ``None`` 이면 ``filter_date.created_at`` 을 넣지 않는다.
        updated_at: 수정 시각. ``None`` 이면 ``filter_date.updated_at`` 을 넣지 않는다.
        file_size: 파일 크기(바이트). ``None`` 이면 ``file_size`` 를 넣지 않는다.

    Returns:
        ``{"filter_kw": {...}, "filter_date": {...}, "file_name_sort": …, "file_size": …}`` 부분
        dict. **값이 없는 키는 넣지 않는다** — 빈 문자열·0 으로 채우면 "모른다"와 "0바이트"가 같아진다.
    """
    out: dict[str, Any] = {}
    filter_kw: dict[str, str] = {}
    ext = derive_file_ext(fs_path)
    if ext:
        filter_kw["file_ext"] = ext
    filter_kw["source_dataset"] = derive_source_dataset(fs_path)
    if filter_kw:
        out["filter_kw"] = filter_kw
    dates: dict[str, str] = {}
    created = _coerce_filter_date(created_at)
    if created:
        dates["created_at"] = created
    updated = _coerce_filter_date(updated_at)
    if updated:
        dates["updated_at"] = updated
    if dates:
        out["filter_date"] = dates
    shown = display_file_name(fs_path)
    if shown:
        out["file_name_sort"] = shown
    if isinstance(file_size, (int, float)) and not isinstance(file_size, bool):
        out["file_size"] = int(file_size)
    return out


__all__ = [
    "build_filter_index_fields",
    "derive_file_ext",
    "derive_source_dataset",
]
