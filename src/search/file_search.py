"""파일 검색 — **조건으로 좁히고 유사도로 줄 세우고 정확히 세는** 조회(095 시나리오 ③).

기존 `search_service.search_hybrid` 와 **다른 화면의 다른 요구**를 맡는다. 둘을 나눈 이유가 이 모듈의
존재 이유다:

| | 멀티모달 검색(`search_hybrid`) | 파일 검색(이 모듈) |
|---|---|---|
| 목적 | 뜻으로 **찾아오기** — 관련도 상위만 | 조건으로 **좁혀 훑기** — 전부 세고 페이지로 넘기기 |
| 집합 | 관련도 컷을 통과한 것 | **단어 일치 + 조건**에 맞는 것 |
| 개수 | 화면에 내려간 행 수 | 검색 엔진이 센 **정확한 수**(상한까지) |
| 순위 | 파이썬에서 두 순위를 섞는다 | **검색 엔진이** 정규화·결합한다(`assets-hybrid` 파이프라인) |
| 컷오프 | 쓴다 | **쓰지 않는다** |

## 왜 컷오프를 쓰지 않나 (실측 근거 · 골든 464질의)

컷오프는 "받아온 후보 중 1등이 배경보다 튀어나왔나"를 보는 **상대 판정**이다. 후보 풀을 고정하면 판정은
완전히 재현되지만(464/464 동일), 풀을 깊게 깔면 배경이 0 에 수렴해 게이트가 항상 열린다. 그 결과 코퍼스에
자료가 **없는** 질의에서 통과 수가 폭발했다 — `증권 약관` 0→1,494 · `베이글` 0→1,474 · `컬링` 0→1,234
(코퍼스 1,526건). 안정적이지만 사용자에게 보여줄 수 없는 숫자다.

그리고 컷오프는 결과를 받아온 **뒤** 파이썬에서 계산하므로 검색 엔진이 셀 수 없다. 세는 대상이 확정되지
않으면 "적힌 숫자 = 누르면 나오는 수"가 성립하지 않는다(2026-08-26 원칙). 그래서 이 화면은 집합을 단어·조건
으로 확정하고, 관련 없는 것을 걸러내는 일은 **조건**이, 약한 것을 아래로 내리는 일은 **순위**가 맡는다.

## 두 번 묻는 이유

순위 질의와 집계 질의를 **따로** 보낸다. 순위는 하이브리드(단어+뜻)라 정규화 파이프라인을 타야 하고,
집계·개수는 "단어·조건에 맞는 전부"라는 확정된 집합에서 세야 한다. 한 질의로 합치면 세는 대상이 두 순위의
합집합이 되어 뜻이 흐려진다. 각 질의가 0.2초 미만이라 나눠도 싸다(파이썬 융합은 0.4~1.0초였다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.config.filename_util import display_file_name
from src.domain.numeric import safe_float
from src.domain.text_norm import normalize_text_key
from src.search.search_filters import SearchFilters, filters_to_opensearch_bool

# ── 설계 상수 — **코퍼스 크기에 비례시키지 않는다** ──────────────────────────────
# 비례시키면 데이터가 늘 때마다 같은 질의가 다른 결과를 낸다(관련도 컷이 겪은 그 문제).
# 값의 근거는 사람의 이용 방식과 검색 엔진 제약이며, 규모가 크게 달라지면 재측정한다.

# 전체 개수를 **정확히** 셀 상한. 넘으면 "이 수 이상"으로 표기한다.
# 왜 1만인가: 처음 검색해 수십만 건이 나올 때 그 숫자가 정확한지는 의미가 없고, 조건을 걸어 수천 건으로
# 줄어든 뒤부터 정확해야 한다. 그 경계가 1만쯤이다(검색 엔진 계열의 기본 관행과도 같다).
TOTAL_CAP_DEFAULT = 10_000

# 하이브리드 순위를 매길 깊이 = **페이징 가능 깊이**.
# 왜 1,000인가: ①색인 결과 창 기본 상한이 1만이라 그 위는 설정 변경이 필요하다 ②벡터 이웃 탐색 비용이
# 깊이에 비례한다 ③사람이 1,000건 넘게 훑지 않는다(그보다 깊이 가려면 조건을 더 걸거나 정렬을 바꾸는 것이
# 맞고, 그때는 관련도 순서가 필요 없어 페이징 제약도 사라진다).
RANK_DEPTH_DEFAULT = 1_000

# 좁히기 칩 하나의 축에 내려줄 항목 수. 화면이 상위 몇 개만 보이므로 넉넉히 준다(083 표시 기본은 12).
FACET_SIZE_DEFAULT = 24

# 검색 엔진에 등록된 정규화·결합 파이프라인(min-max + 가중평균). 파이썬 융합과 같은 계산을 서버가 한다.
SEARCH_PIPELINE_DEFAULT = "assets-hybrid"

# 단어를 찾을 필드. 파일명에 가중치를 두는 이유: "파일 이름과 내용을 함께 검색" 이 이 화면의 계약이고,
# 이름이 맞은 것은 사용자가 의도한 파일일 확률이 높다.
WORD_FIELDS_DEFAULT: tuple[str, ...] = ("file_name^2", "summary", "keywords")

# 좁히기 축 → 색인 필드. 셋 다 keyword 필드라 정확히 집계된다.
#   ⚠️ 태그 축은 **정규화 키** 필드다 — 표시 라벨은 원문이라 대표 문서에서 되찾는다(``_tag_label``).
FACET_FIELDS: dict[str, str] = {
    "topic": "topics",
    "subtopic": "subtopics",
    "tag": "keywords_norm",
}

__all__ = [
    "FACET_FIELDS",
    "FACET_SIZE_DEFAULT",
    "RANK_DEPTH_DEFAULT",
    "SEARCH_PIPELINE_DEFAULT",
    "TOTAL_CAP_DEFAULT",
    "WORD_FIELDS_DEFAULT",
    "build_facet_body",
    "build_rank_body",
    "search_files",
]


def _word_clause(query: str, fields: Sequence[str]) -> dict[str, Any]:
    """검색어를 단어 일치 절로 만든다.

    Args:
        query: 검색어. 앞뒤 공백은 호출자가 다듬는다.
        fields: 찾을 필드(가중치 표기 포함).

    Returns:
        ``multi_match`` 절.
    """
    return {"multi_match": {"query": query, "fields": list(fields)}}


def build_rank_body(
    query: str,
    query_vector: Sequence[float],
    *,
    filters: SearchFilters | None = None,
    from_: int = 0,
    size: int = 50,
    rank_depth: int = RANK_DEPTH_DEFAULT,
    fields: Sequence[str] = WORD_FIELDS_DEFAULT,
) -> dict[str, Any]:
    """순위 질의 본문 — 집합은 단어·조건으로 한정하고 순서만 뜻으로 돕는다(순수).

    🔴 **벡터 쪽에도 같은 단어 조건을 필터로 건다.** 걸지 않으면 글자가 하나도 겹치지 않는 문서까지
    집합에 들어와 개수가 부풀고(실측: `흉부` 1건 → 500건), 그러면 세는 숫자가 뜻을 잃는다. 벡터는
    **순서를 돕는 역할**만 한다.

    Args:
        query: 검색어.
        query_vector: 질의 임베딩. 문서 색인과 **같은 채널**로 만든 것이어야 같은 공간에서 비교된다.
        filters: 주제·하위주제·태그·기간·확장자 선필터. ``None`` 이면 조건 없음.
        from_: 페이지 시작 위치.
        size: 이 페이지의 행 수.
        rank_depth: 순위를 매길 깊이(= 페이징 가능 깊이). 벡터 이웃 수에도 같은 값을 쓴다.
        fields: 단어를 찾을 필드.

    Returns:
        OpenSearch 검색 본문. ``search_pipeline`` 은 호출부가 붙인다(정규화·결합이 그 파이프라인 몫).
    """
    word = _word_clause(query, fields)
    clauses = filters_to_opensearch_bool(filters)
    scope = [word, *clauses]
    return {
        "from": int(from_),
        "size": int(size),
        # 개수는 집계 질의가 정확히 센다 — 여기서 또 세면 같은 일을 두 번 한다.
        "track_total_hits": False,
        "query": {"hybrid": {"pagination_depth": int(rank_depth), "queries": [
            {"bool": {"must": [word], "filter": clauses}},
            {"knn": {"embedding": {"vector": list(query_vector), "k": int(rank_depth),
                                   "filter": {"bool": {"filter": scope}}}}},
        ]}},
        "_source": ["asset_id", "modality", "domain_label", "file_name", "fs_uri",
                    "summary", "keywords", "topics", "subtopics", "topic_pairs"],
    }


def build_facet_body(
    query: str,
    *,
    filters: SearchFilters | None = None,
    total_cap: int = TOTAL_CAP_DEFAULT,
    facet_size: int = FACET_SIZE_DEFAULT,
    axes: Sequence[str] = tuple(FACET_FIELDS),
    fields: Sequence[str] = WORD_FIELDS_DEFAULT,
) -> dict[str, Any]:
    """개수·좁히기 칩 질의 본문 — **세는 대상은 단어·조건에 맞는 전부**(순수).

    이 질의에는 벡터가 없다. 세는 대상이 "조건에 맞는 파일"이어야 클릭 결과와 숫자가 같아지기 때문이다
    (뜻으로만 가까운 문서까지 세면 눌러도 그만큼 나오지 않는다 — 실제로 겪은 결함).

    태그 축만 대표 문서를 하나씩 함께 받는다(``sample``) — 색인의 태그 필드는 **정규화 키**라
    ``전통 음식`` 이 ``전통음식`` 으로 저장돼 있어, 화면에 보일 원문을 그 문서에서 되찾는다.

    Args:
        query: 검색어.
        filters: 선필터.
        total_cap: 이 수까지 정확히 센다. 넘으면 응답의 ``relation`` 이 ``gte`` 가 된다.
        facet_size: 축마다 받을 항목 수.
        axes: 셀 축 이름들(``FACET_FIELDS`` 의 키).
        fields: 단어를 찾을 필드.

    Returns:
        OpenSearch 검색 본문(행은 받지 않는다 · ``size`` 0).

    Raises:
        ValueError: 모르는 축 이름이거나 ``total_cap``·``facet_size`` 가 1 미만일 때(조용히 빈 결과를
            돌려주면 "칩이 없는 결과"와 설정 오류가 구분되지 않는다).
    """
    if total_cap < 1 or facet_size < 1:
        raise ValueError(f"범위 오류: total_cap={total_cap!r} facet_size={facet_size!r} (>=1)")
    unknown = [a for a in axes if a not in FACET_FIELDS]
    if unknown:
        raise ValueError(f"알 수 없는 좁히기 축: {unknown} (허용: {sorted(FACET_FIELDS)})")

    aggs: dict[str, Any] = {}
    for axis in axes:
        agg: dict[str, Any] = {
            # 정렬은 건수 내림차순 → 키 오름차순으로 못 박는다(동률 순서가 실행마다 흔들리지 않게).
            "terms": {"field": FACET_FIELDS[axis], "size": int(facet_size),
                      "order": [{"_count": "desc"}, {"_key": "asc"}]},
        }
        if axis == "tag":
            agg["aggs"] = {"sample": {"top_hits": {"size": 1, "_source": ["keywords"]}}}
        aggs[axis] = agg

    return {
        "size": 0,
        "track_total_hits": int(total_cap),
        "query": {"bool": {"must": [_word_clause(query, fields)],
                           "filter": filters_to_opensearch_bool(filters)}},
        "aggs": aggs,
    }


def _tag_label(bucket: Mapping[str, Any]) -> str:
    """태그 버킷의 **표시 라벨**을 대표 문서의 원문에서 되찾는다.

    색인에는 정규화 키만 있다(``전통 음식`` → ``전통음식``). 대표 문서의 원문 키워드를 같은 규칙으로
    눌러 보아 이 버킷의 키와 맞는 것을 고른다 — 정규화 규칙은 코어 한 곳(``normalize_text_key``)뿐이라
    색인·필터·표시가 갈라지지 않는다.

    Args:
        bucket: ``terms`` 버킷. ``sample`` 하위 집계가 있으면 그 문서의 ``keywords`` 를 본다.

    Returns:
        원문 라벨. 되찾지 못하면 키 자체(누르면 서버가 다시 정규화하므로 동작은 한다).
    """
    key = str(bucket.get("key") or "")
    hits = (((bucket.get("sample") or {}).get("hits") or {}).get("hits")) or []
    if hits:
        for raw in (hits[0].get("_source") or {}).get("keywords") or []:
            if isinstance(raw, str) and normalize_text_key(raw) == key:
                return raw
    return key


def _facet_items(axis: str, agg: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """한 축의 집계 버킷을 화면 항목으로 바꾼다.

    Args:
        axis: 축 이름. 태그 축만 라벨을 되찾는다.
        agg: 그 축의 집계 결과. 없으면 빈 목록.

    Returns:
        ``[{key, label, count}]`` — 건수 내림차순(집계 정렬을 그대로 따른다). ``key`` 는 서버에 되보낼
        값이고 ``label`` 은 화면에 보일 값이다(태그 축에서만 둘이 다를 수 있다).
    """
    out: list[dict[str, Any]] = []
    for bucket in (agg or {}).get("buckets") or []:
        key = str(bucket.get("key") or "")
        if not key:
            continue
        label = _tag_label(bucket) if axis == "tag" else key
        out.append({"key": key, "label": label, "count": int(bucket.get("doc_count") or 0)})
    return out


def _row(hit: Mapping[str, Any]) -> dict[str, Any]:
    """검색 히트 하나를 결과 행으로.

    Args:
        hit: OpenSearch 히트. ``_source`` 가 없으면 빈 값으로 본다.

    Returns:
        행 dict. ``score`` 는 정규화·결합된 점수(0~1)이며 **이 응답 안에서만** 비교 가능하다.
        ``file_name`` 은 표시용(자산 id 접두를 벗긴 값)이다.
    """
    src = hit.get("_source") or {}
    return {
        "asset_id": str(src.get("asset_id") or ""),
        "modality": str(src.get("modality") or ""),
        "domain_label": str(src.get("domain_label") or "general"),
        "file_name": display_file_name(str(src.get("fs_uri") or "")) or str(src.get("file_name") or ""),
        "summary": str(src.get("summary") or ""),
        "score": safe_float(hit.get("_score")),
        "tags": [str(k) for k in (src.get("keywords") or []) if k],
        "topics": [str(t) for t in (src.get("topics") or []) if t],
        "subtopics": [str(t) for t in (src.get("subtopics") or []) if t],
        "topic_pairs": [str(t) for t in (src.get("topic_pairs") or []) if t],
    }


def search_files(
    client: Any,
    index: str,
    *,
    query: str,
    query_vector: Sequence[float],
    filters: SearchFilters | None = None,
    from_: int = 0,
    size: int = 50,
    rank_depth: int = RANK_DEPTH_DEFAULT,
    total_cap: int = TOTAL_CAP_DEFAULT,
    facet_size: int = FACET_SIZE_DEFAULT,
    axes: Sequence[str] = tuple(FACET_FIELDS),
    pipeline: str = SEARCH_PIPELINE_DEFAULT,
) -> dict[str, Any]:
    """조건으로 좁힌 파일을 **유사도 순 한 페이지 + 정확한 전체 개수 + 좁히기 칩**으로 조회한다.

    "적힌 숫자 = 누르면 나오는 수"가 성립한다 — 개수와 칩을 세는 대상이 필터가 적용되는 대상과 같기
    때문이다(2026-08-26 원칙). 페이지를 넘겨도 개수와 순서가 흔들리지 않는다(실측: 페이지 겹침 일치).

    Args:
        client: OpenSearch 클라이언트.
        index: 색인 이름.
        query: 검색어. 빈 값이면 ``ValueError``(조건만으로 훑는 화면은 이 함수의 몫이 아니다).
        query_vector: 질의 임베딩. **문서와 같은 채널**로 만들어야 한다.
        filters: 선필터(주제·하위주제·태그·기간·확장자).
        from_: 페이지 시작 위치. ``from_ + size`` 가 ``rank_depth`` 를 넘으면 ``ValueError`` —
            그 밖은 순위를 **매기지 않은** 구간이라 빈 페이지로 돌려주면 "끝"과 구분되지 않는다.
            반면 ``from_`` 이 전체 개수를 넘는 것은 오류가 아니라 그냥 **끝을 지난 것**이므로
            빈 ``rows`` 와 정상 ``total`` 을 돌려준다(둘은 다른 상황이다).
        size: 이 페이지의 행 수(1 이상).
        rank_depth: 순위·페이징 깊이.
        total_cap: 개수를 정확히 셀 상한.
        facet_size: 축마다 받을 칩 수.
        axes: 셀 축 이름들.
        pipeline: 정규화·결합 파이프라인 이름.

    Returns:
        ``{rows, total, total_capped, facets, from, size}``. ``total_capped`` 가 참이면 ``total`` 은
        "이 수 이상"이라는 뜻이다(화면이 "1만 건 이상"으로 표기한다). ``facets`` 는
        ``{축: [{key, label, count}]}``.

    Raises:
        ValueError: 빈 질의 · 범위 밖 페이지 · 잘못된 축·상한.
        OpenSearch 미도달 예외는 감싸지 않고 그대로 올린다 — 결과가 백엔드 가용성에 따라 달라지면 안 된다.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("파일 검색은 검색어가 필요하다(조건만으로 훑는 경로는 따로 둔다)")
    if size < 1 or from_ < 0:
        raise ValueError(f"페이지 범위 오류: from_={from_!r} size={size!r}")
    if from_ + size > rank_depth:
        raise ValueError(
            f"순위 깊이를 넘는 페이지다: from_+size={from_ + size} > rank_depth={rank_depth} — "
            "조건을 더 걸거나 정렬을 바꿔야 한다")

    # 🔴 **개수를 먼저 센다.** 순위 질의는 결과 끝을 넘는 페이지를 요청하면 오류를 낸다
    #    ("Reached end of search result" · 실측). 끝을 넘은 페이지는 오류가 아니라 **빈 페이지**여야
    #    맞으므로, 총계를 먼저 알고 그때만 순위를 묻는다(질의 하나를 아끼는 효과도 있다).
    facet = client.search(
        index=index,
        body=build_facet_body(q, filters=filters, total_cap=total_cap, facet_size=facet_size,
                              axes=axes),
    )
    total_info = (facet.get("hits") or {}).get("total") or {}
    total = int(total_info.get("value") or 0)
    hits: list[dict[str, Any]] = []
    if from_ < total:
        rank = client.search(
            index=index,
            body=build_rank_body(q, query_vector, filters=filters, from_=from_,
                                 # 남은 것보다 더 달라고 하면 같은 오류가 난다 — 남은 만큼만 청한다.
                                 size=min(size, total - from_), rank_depth=rank_depth),
            params={"search_pipeline": pipeline},
        )
        hits = ((rank.get("hits") or {}).get("hits") or [])
    aggs = facet.get("aggregations") or {}
    return {
        "rows": [_row(h) for h in hits],
        "total": total,
        # 상한에 걸렸으면 검색 엔진이 ``gte``(이 수 이상)로 알려 준다.
        "total_capped": str(total_info.get("relation") or "eq") != "eq",
        "facets": {axis: _facet_items(axis, aggs.get(axis)) for axis in axes},
        "from": int(from_),
        "size": int(size),
    }
