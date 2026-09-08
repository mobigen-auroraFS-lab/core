"""파일 검색 — **조건으로 좁히고 유사도로 줄 세우고 정확히 세는** 조회(095 시나리오 ③).

기존 `search_service.search_hybrid` 와 **다른 화면의 다른 요구**를 맡는다. 둘을 나눈 이유가 이 모듈의
존재 이유다:

| | 멀티모달 검색(`search_hybrid`) | 파일 검색(이 모듈) |
|---|---|---|
| 목적 | 뜻으로 **찾아오기** — 관련도 상위만 | 조건으로 **좁혀 훑기** — 전부 세고 페이지로 넘기기 |
| 집합 | 단어 ∪ 뜻 상위 k → **컷오프로 걸러낸 것** | 단어 ∪ **뜻이 임계 이상** + 조건 |
| 개수 | 화면에 내려간 행 수 | 검색 엔진이 센 **정확한 수**(상한까지) |
| 순위 | 파이썬에서 두 순위를 섞는다 | **검색 엔진이** 정규화·결합한다(`assets-hybrid` 파이프라인) |
| 컷오프 | 쓴다 | **쓰지 않는다**(집합 조건이 그 역할을 대신한다) |

**단어 절은 두 화면이 같은 것을 쓴다**(`query_builder.build_word_should` · 096). 각자 만들면 같은
질의가 다른 파일을 찾는다 — 실측으로 파일명 가중치만 달라도(0.5 대 2.0) 상위 10 중 3건만 겹쳤다.

## 왜 컷오프를 쓰지 않나 (실측 근거 · 골든 464질의)

컷오프는 "받아온 후보 중 1등이 배경보다 튀어나왔나"를 보는 **상대 판정**이다. 후보 풀을 고정하면 판정은
완전히 재현되지만(464/464 동일), 풀을 깊게 깔면 배경이 0 에 수렴해 게이트가 항상 열린다. 그 결과 코퍼스에
자료가 **없는** 질의에서 통과 수가 폭발했다 — `증권 약관` 0→1,494 · `베이글` 0→1,474 · `컬링` 0→1,234
(코퍼스 1,526건). 안정적이지만 사용자에게 보여줄 수 없는 숫자다.

그리고 컷오프는 결과를 받아온 **뒤** 파이썬에서 계산하므로 검색 엔진이 셀 수 없다. 세는 대상이 확정되지
않으면 "적힌 숫자 = 누르면 나오는 수"가 성립하지 않는다(2026-08-26 원칙). 그래서 이 화면은 집합을 단어·조건
으로 확정하고, 관련 없는 것을 걸러내는 일은 **조건**이, 약한 것을 아래로 내리는 일은 **순위**가 맡는다.

## 뜻에는 **경계**를 준다 — 「상위 k개」가 아니라 「이만큼 가까운 것」

뜻으로만 걸린 자료를 버리면 퇴보다 — 멀티모달 검색은 `클래식 피아노 연주회`(글자로는 0건)에 피아노·
베토벤 자료를 준다. 그런데 벡터 검색에 「가까운 순 k개」를 청하면 **관련이 없어도 k개를 채워 준다**
(실측: 코퍼스에 없는 `컬링`·`베이글` 도 k=100 이면 100건). 그러면 개수가 질의가 아니라 k 가 정한다.

그래서 **유사도 하한**(radial kNN · `min_score`)으로 청한다 — 「코사인 0.60 이상인 것 전부」. 조건이라
집합 크기가 질의에 따라 정해진다.

🔴 **그 결과를 id 목록으로 굳혀 모든 질의가 공유한다**(2026-09-08 실측 결함). 벡터 검색은 근사라
**필터가 있을 때와 없을 때 찾아내는 문서가 다르다**(작은 집합에서는 전수 비교로 바뀐다). 축별 집계는
필터를 일부러 바꾸므로, 질의마다 벡터 검색을 다시 하면 칩 건수와 클릭 결과가 어긋난다 — `등산` 에서
칩 12 대 클릭 13 이었다. 조건 없이 **한 번만** 구해 굳히면 조건이 무엇이든 같은 문서를 가리킨다.

골든 464질의 전수 측정으로 임계를 골랐다:

| 임계 | 되찾음(멀티모달 검색이 주던 것) | 잡음(그것도 안 주던 것) | 자료 없는 질의 0건 유지 |
|---|---|---|---|
| 0.55 | 213 | 1,022 | 27/34 |
| **0.60** | **101** | **107** | **32/34** |

0.55 는 하나를 되찾는 대가로 다섯이 섞인다. 0.60 은 거의 반반이고 자료 없는 질의가 0건을 지킨다.

⚠️ **정렬을 바꿔도 집합은 같아야 한다** — 그래서 이름·날짜순 정렬에도 질의 임베딩이 필요하다(집합
판정에 쓰이므로). 정렬에 따라 개수가 달라지면 화면이 거짓말을 한다.

## 두 번 묻는 이유

순위 질의와 집계 질의를 **따로** 보낸다. 순위는 하이브리드(단어+뜻)라 정규화 파이프라인을 타야 하고,
집계·개수는 "단어·조건에 맞는 전부"라는 확정된 집합에서 세야 한다. 한 질의로 합치면 세는 대상이 두 순위의
합집합이 되어 뜻이 흐려진다. 각 질의가 0.2초 미만이라 나눠도 싸다(파이썬 융합은 0.4~1.0초였다).

## 칩은 **자기 조건을 뺀 채** 센다 (096 · 087 이 겪은 함정)

주제 칩을 누른 뒤에도 **다른 주제로 갈아탈 수 있어야** 한다. 그런데 걸린 조건을 전부 적용해 세면 고른
주제 하나만 남아(다른 주제는 건수 0이라 사라진다) 갈아탈 길이 막힌다 — 실측으로 주제 칩이 5·9개에서
1·3개로 줄었다. 그래서 **축마다 자기 조건을 빼고** 센다. 옷 가게 비유: 「빨강」을 고른 상태에서 색깔
선반에는 파랑·검정이 그대로 보여야 갈아입을 수 있고, 대신 사이즈 선반은 「빨강 옷 중에서」 세는 것이 맞다.

숫자의 뜻은 이렇게 못 박는다 — **그 칩 하나만 골랐을 때 나오는 수**(다른 축 조건은 그대로 적용). 같은
축에서 여럿 고르면 「또는」이라 결과는 각 칩 수의 합집합이므로 개별 칩 수보다 크거나 같다. 이는 일반적인
좁히기 검색의 관행과 같다.

하위주제는 주제의 **자식**이라 주제 축을 셀 때 하위주제 조건까지 뺀다. 빼지 않으면 「음악 > 가수」를 고른
상태에서 가수가 음악에만 달려 있으므로 주제 축이 다시 음악 하나로 접힌다.

## 정렬

기본은 관련도(유사도)다. 필드로 정렬하면 뜻은 순서에 관여할 이유가 없으므로 **벡터 질의를 아예 보내지
않는다** — 빨라지고, 하이브리드의 페이징 깊이 제약(``rank_depth``)도 사라진다.

🔴 **정렬 기준은 화면에 보이는 값과 같아야 한다.** 이름은 화면 표시명(``file_name_sort``)으로 세운다 —
검색용 ``file_name``(잡음 정제 값)으로 세우면 화면의 84.5%가 제자리에 오지 않아(실측 1,526건) 정렬한
열이 정렬돼 보이지 않는다. 날짜는 색인이 날짜까지만 담고 표도 날짜까지만 보이므로 **보이지 않는 시각으로
순서가 갈리지 않는다**(같은 날짜는 자산 id 로 갈린다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from src.config.filename_util import display_file_name
from src.domain.numeric import safe_float
from src.domain.text_norm import normalize_text_key
from src.search.query_builder import build_word_should
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

# 단어 매칭 연산자. **모든 형태소가 맞아야** 후보다.
# 왜 ``and`` 인가: 한국어는 낱말이 형태소로 쪼개진다(``남한산성`` → 남한/산/성). ``or`` 로 두면
# 「산」이나 「성」만 든 파일까지 세어져 개수가 뜻을 잃는다(실측 354건 대 3건).
WORD_OPERATOR_DEFAULT = "and"

# 뜻으로 집합에 들어올 유사도 **하한**(코사인). 이 값 이상인 파일은 글자가 안 겹쳐도 집합에 든다.
# 왜 0.60 인가: 골든 464질의 전수 측정에서 되찾음:잡음이 101:107(0.55 는 213:1,022)이고, 자료 없는
# 질의 34개 중 32개가 0건을 유지한다. 코퍼스의 코사인이 0.27~0.36 좁은 띠에 몰려 있어 이보다 낮추면
# 무관한 파일이 급증한다. **코퍼스 성격이 크게 바뀌면 재측정한다**.
SEMANTIC_MIN_COSINE_DEFAULT = 0.60

# 뜻으로 걸린 자산 id 를 받아올 상한. 넘치면 가까운 순으로 잘린다(결정적).
# 왜 500 인가: 골든 464질의 실측에서 임계 0.60 을 넘는 자산은 질의당 평균 1건 미만이고 최대 수십
# 건이다. 500 은 넉넉한 여유이면서 ``terms`` 절이 커져 질의가 무거워지는 것을 막는 선이다.
SEMANTIC_CAP_DEFAULT = 500

# 좁히기 축 → 색인 필드. 셋 다 keyword 필드라 정확히 집계된다.
#   ⚠️ 태그 축은 **정규화 키** 필드다 — 표시 라벨은 원문이라 대표 문서에서 되찾는다(``_tag_label``).
FACET_FIELDS: dict[str, str] = {
    "topic": "topics",
    "subtopic": "subtopics",
    "tag": "keywords_norm",
}

# 축 → 그 축을 셀 때 **빼야 할** 필터 필드(위 docstring 「칩은 자기 조건을 뺀 채 센다」).
#   하위주제는 주제의 자식이라 주제 축에서 함께 뺀다 — 빼지 않으면 주제 축이 다시 하나로 접힌다.
FACET_SELF_FILTERS: dict[str, tuple[str, ...]] = {
    "topic": ("topics", "subtopics"),
    "subtopic": ("subtopics",),
    "tag": ("tags",),
}

# 정렬 이름 → 색인 정렬 절. ``None`` 은 관련도(엔진 점수) 순.
#   ⚠️ 마지막에 ``asset_id`` 를 덧붙이는 이유: 값이 같은 행들의 순서가 실행마다 흔들리면 페이지를 넘길 때
#      같은 파일이 두 번 보이거나 아예 빠진다(결정성 요구사항).
#   🔴 이름은 ``file_name_sort``(화면에 보이는 파일명)로 세운다. 검색용 ``file_name.raw`` 로 세우면
#      화면의 84.5%가 제자리에 오지 않는다(실측) — 정렬한 열이 정렬돼 보이지 않는 표가 된다.
#   ⚠️ 날짜는 색인 값이 **날짜까지**라 같은 날끼리는 자산 id 순이다. 화면 표도 날짜까지만 보이므로
#      눈에 보이는 만큼만 기준이 되는 셈이다(보이지 않는 시각으로 순서가 갈리지 않는다).
SORT_OPTIONS: dict[str, tuple[dict[str, Any], ...] | None] = {
    "relevance": None,
    "name_asc": ({"file_name_sort": "asc"}, {"asset_id": "asc"}),
    "name_desc": ({"file_name_sort": "desc"}, {"asset_id": "asc"}),
    "created_desc": ({"filter_date.created_at": "desc"}, {"asset_id": "asc"}),
    "created_asc": ({"filter_date.created_at": "asc"}, {"asset_id": "asc"}),
    "updated_desc": ({"filter_date.updated_at": "desc"}, {"asset_id": "asc"}),
    "updated_asc": ({"filter_date.updated_at": "asc"}, {"asset_id": "asc"}),
    "size_desc": ({"file_size": "desc"}, {"asset_id": "asc"}),
    "size_asc": ({"file_size": "asc"}, {"asset_id": "asc"}),
}
SORT_DEFAULT = "relevance"

# 필드 정렬로 넘길 수 있는 깊이. 하이브리드와 달리 이웃 탐색이 없어 깊이 제약이 색인 결과창뿐이다.
SORT_DEPTH_DEFAULT = 10_000

__all__ = [
    "FACET_FIELDS",
    "FACET_SELF_FILTERS",
    "FACET_SIZE_DEFAULT",
    "RANK_DEPTH_DEFAULT",
    "SEARCH_PIPELINE_DEFAULT",
    "SEMANTIC_CAP_DEFAULT",
    "SEMANTIC_MIN_COSINE_DEFAULT",
    "SORT_DEFAULT",
    "SORT_DEPTH_DEFAULT",
    "SORT_OPTIONS",
    "TOTAL_CAP_DEFAULT",
    "WORD_OPERATOR_DEFAULT",
    "build_facet_body",
    "build_facet_plan",
    "build_semantic_body",
    "build_rank_body",
    "search_files",
]


def _word_clause(query: str, *, operator: str = WORD_OPERATOR_DEFAULT) -> dict[str, Any]:
    """검색어를 단어 일치 절로 만든다 — **멀티모달 검색과 같은 절**(096).

    Args:
        query: 검색어. 앞뒤 공백은 호출자가 다듬는다.
        operator: 단어 매칭 연산자(``and`` 면 모든 형태소가 맞아야 한다).

    Returns:
        ``bool`` 절(필드별 절 5개 · ``minimum_should_match: 1``).
    """
    return {"bool": {"should": build_word_should(query, operator=operator),
                     "minimum_should_match": 1}}


def build_semantic_body(
    query_vector: Sequence[float],
    *,
    min_cosine: float = SEMANTIC_MIN_COSINE_DEFAULT,
    cap: int = SEMANTIC_CAP_DEFAULT,
) -> dict[str, Any]:
    """뜻이 **임계 이상** 가까운 파일의 id 를 구하는 질의 본문(순수 · radial kNN).

    「가까운 순 k개」가 아니라 「이만큼 가까운 것 전부」라 집합에 경계가 생긴다 — 그래서 코퍼스에 없는
    질의는 0건이 된다(k 로 청하면 관련이 없어도 k 개를 채워 준다 · 실측 `컬링` 100건).

    🔴 **조건(주제·태그·기간)을 걸지 않는다.** 벡터 검색은 근사라 **필터가 있을 때와 없을 때 찾아내는
    문서가 다르다**(작은 집합에서는 전수 비교로 바뀐다). 조건마다 다른 답이 나오면 칩 건수와 클릭
    결과가 어긋난다 — 실측으로 `등산` 에서 칩 12 대 클릭 13 이었다. 그래서 **조건 없이 한 번만** 구해
    id 목록으로 굳히고, 그 목록을 모든 질의가 공유한다(조건은 그 뒤에 걸린다).

    Args:
        query_vector: 질의 임베딩. 문서 색인과 **같은 채널**로 만든 것이어야 한다.
        min_cosine: 유사도 하한(코사인). 색인 점수 규약은 ``(코사인+1)/2`` 이므로 그렇게 환산한다.
        cap: 받아올 id 수 상한. 넘치면 **가까운 순으로** 잘린다(결정적).

    Returns:
        OpenSearch 검색 본문. 행 내용은 필요 없으므로 ``asset_id`` 만 받는다.
    """
    return {
        "size": int(cap),
        "track_total_hits": False,
        "_source": ["asset_id"],
        "query": {"knn": {"embedding": {
            "vector": list(query_vector),
            # 코사인 공간의 색인 점수는 (코사인+1)/2 다 — 코어 ``knn_score_to_cosine`` 의 역변환.
            "min_score": (float(min_cosine) + 1.0) / 2.0,
        }}},
    }


def _semantic_clause(semantic_ids: Sequence[str]) -> dict[str, Any] | None:
    """뜻으로 걸린 자산들을 **id 목록 절**로 만든다.

    id 목록이라 조건이 무엇이든 **같은 문서를 가리킨다** — 근사 벡터 검색을 질의마다 다시 하지 않기
    때문이다. 이것이 칩 건수와 클릭 결과가 일치하는 근거다.

    Args:
        semantic_ids: ``build_semantic_body`` 로 구한 자산 id 들.

    Returns:
        ``terms`` 절. 목록이 비면 ``None``(절을 아예 넣지 않는다 — 빈 ``terms`` 는 무의미한 비용이다).
    """
    ids = [str(a) for a in semantic_ids if str(a)]
    return {"terms": {"asset_id": ids}} if ids else None


def _scope_clause(
    query: str,
    semantic_ids: Sequence[str] = (),
    *,
    filters: Sequence[dict[str, Any]] = (),
    operator: str = WORD_OPERATOR_DEFAULT,
) -> dict[str, Any]:
    """**집합 정의** — 글자가 맞았거나 뜻이 임계 이상 가까운 파일, 그리고 조건에 맞는 것.

    🔴 개수·칩·순위가 **모두 이 절 하나**를 쓴다. 하나라도 다른 절을 쓰면 "적힌 숫자 = 누르면
    나오는 수"가 깨진다(2026-09-08 실측 결함이 그것이었다).

    Args:
        query: 검색어.
        semantic_ids: 뜻으로 걸린 자산 id 들(``build_semantic_body`` 결과). 비면 단어만으로 정한다.
        filters: 선필터에서 나온 조건 절.
        operator: 단어 매칭 연산자.

    Returns:
        ``bool`` 절 — ``should``(단어·뜻) + ``minimum_should_match: 1`` + ``filter``(조건).
    """
    should = [_word_clause(query, operator=operator)]
    semantic = _semantic_clause(semantic_ids)
    if semantic is not None:
        should.append(semantic)
    return {"bool": {
        "should": should,
        "minimum_should_match": 1,
        "filter": list(filters),
    }}


_ROW_SOURCE: tuple[str, ...] = (
    "asset_id", "modality", "domain_label", "file_name", "fs_uri",
    "summary", "keywords", "topics", "subtopics", "topic_pairs",
)


def build_rank_body(
    query: str,
    query_vector: Sequence[float],
    *,
    semantic_ids: Sequence[str] = (),
    filters: SearchFilters | None = None,
    from_: int = 0,
    size: int = 50,
    rank_depth: int = RANK_DEPTH_DEFAULT,
    operator: str = WORD_OPERATOR_DEFAULT,
    sort: str = SORT_DEFAULT,
) -> dict[str, Any]:
    """순위 질의 본문 — 집합은 ``_scope_clause`` 가 정하고 순서만 정렬 방식이 정한다(순수).

    관련도 정렬이면 검색 엔진의 하이브리드 질의로 두 순위(단어·뜻)를 정규화·결합한다. 필드 정렬이면
    순서를 필드가 정하므로 하이브리드가 아니라 평범한 질의를 보낸다.

    🔴 **정렬을 바꿔도 집합은 같다** — 두 갈래 모두 같은 ``_scope_clause`` 를 쓴다. 정렬에 따라
    개수가 달라지면 화면이 거짓말을 한다.

    Args:
        query: 검색어.
        query_vector: 질의 임베딩. **관련도 정렬의 순서**에 쓰인다(집합 판정은 ``semantic_ids``).
        semantic_ids: 뜻으로 걸린 자산 id 들. 집합 판정에 쓰이므로 집계 질의와 **같은 값**이어야 한다.
        filters: 주제·하위주제·태그·기간·확장자 선필터. ``None`` 이면 조건 없음.
        from_: 페이지 시작 위치.
        size: 이 페이지의 행 수.
        rank_depth: 관련도 정렬에서 순위를 매길 깊이(= 페이징 가능 깊이).
        operator: 단어 매칭 연산자.
        sort: 정렬 이름(``SORT_OPTIONS`` 의 키).

    Returns:
        OpenSearch 검색 본문. ``search_pipeline`` 은 호출부가 붙인다(정규화·결합이 그 파이프라인 몫이며
        **관련도 정렬에만** 붙인다).

    Raises:
        ValueError: 모르는 정렬 이름일 때.
    """
    if sort not in SORT_OPTIONS:
        raise ValueError(f"알 수 없는 정렬: {sort!r} (허용: {sorted(SORT_OPTIONS)})")
    clauses = filters_to_opensearch_bool(filters)
    body: dict[str, Any] = {
        "from": int(from_),
        "size": int(size),
        # 개수는 집계 질의가 정확히 센다 — 여기서 또 세면 같은 일을 두 번 한다.
        "track_total_hits": False,
        "_source": list(_ROW_SOURCE),
    }
    order = SORT_OPTIONS[sort]
    if order is not None:
        body["query"] = _scope_clause(query, semantic_ids, filters=clauses, operator=operator)
        body["sort"] = [dict(s) for s in order]
        return body
    # 하이브리드는 두 서브질의의 **합집합**이라 집합이 ``_scope_clause`` 와 같다. 두 서브질의의 절은
    # 집합 절에서 **그대로 꺼내 쓴다** — 따로 조립하면 조건 위치가 어긋나 집합이 갈라진다(실측 1건).
    # 하이브리드 두 서브질의: ① 단어(+조건) ② 벡터 이웃(+집합·조건). ②는 **순서를 매기기 위한**
    # 것이라 여기서만 벡터를 쓴다 — 집합은 ①②의 합집합이 아니라 위 ``_scope_clause`` 가 정하므로,
    # ② 에도 집합 절을 필터로 걸어 밖으로 새지 않게 한다.
    scope = _scope_clause(query, semantic_ids, filters=clauses, operator=operator)
    body["query"] = {"hybrid": {"pagination_depth": int(rank_depth), "queries": [
        {"bool": {"must": [_word_clause(query, operator=operator)], "filter": list(clauses)}},
        {"knn": {"embedding": {"vector": list(query_vector), "k": int(rank_depth),
                               "filter": scope}}},
    ]}}
    return body


def build_facet_body(
    query: str,
    semantic_ids: Sequence[str] = (),
    *,
    filters: SearchFilters | None = None,
    total_cap: int = TOTAL_CAP_DEFAULT,
    facet_size: int = FACET_SIZE_DEFAULT,
    axes: Sequence[str] = tuple(FACET_FIELDS),
    operator: str = WORD_OPERATOR_DEFAULT,
) -> dict[str, Any]:
    """개수·좁히기 칩 질의 본문 — **세는 대상 = 순위 질의의 집합**(순수).

    🔴 순위 질의와 **똑같은 ``_scope_clause``** 를 쓴다. 세는 대상과 보여주는 대상이 달라지면
    "적힌 숫자 = 누르면 나오는 수"가 깨진다(2026-09-08 실측 결함이 그것이었다).

    태그 축만 대표 문서를 하나씩 함께 받는다(``sample``) — 색인의 태그 필드는 **정규화 키**라
    ``전통 음식`` 이 ``전통음식`` 으로 저장돼 있어, 화면에 보일 원문을 그 문서에서 되찾는다.

    Args:
        query: 검색어.
        semantic_ids: 뜻으로 걸린 자산 id 들. 순위 질의와 **같은 값**이어야 한다.
        filters: 선필터.
        total_cap: 이 수까지 정확히 센다. 넘으면 응답의 ``relation`` 이 ``gte`` 가 된다.
        facet_size: 축마다 받을 항목 수.
        axes: 셀 축 이름들(``FACET_FIELDS`` 의 키).
        operator: 단어 매칭 연산자.

    Returns:
        OpenSearch 검색 본문(행은 받지 않는다 · ``size`` 0).

    Raises:
        ValueError: 모르는 축 이름이거나 ``total_cap``·``facet_size`` 가 1 미만일 때(조용히 빈 결과를
            돌려주면 "칩이 없는 결과"와 설정 오류가 구분되지 않는다).
    """
    if total_cap < 1 or facet_size < 1:
        raise ValueError(f"범위 오류: total_cap={total_cap!r} facet_size={facet_size!r} (>=1)")
    # 축이 하나도 없어도 유효하다 — 개수만 세는 질의다(096 축별 계획에서 그런 경우가 생긴다).
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
        "query": _scope_clause(query, semantic_ids,
                               filters=filters_to_opensearch_bool(filters),
                               operator=operator),
        "aggs": aggs,
    }


def _active_filter_fields(filters: SearchFilters | None) -> frozenset[str]:
    """지금 **값이 들어 있는** 좁히기 필터 필드 이름들.

    빈 축을 빼 봐야 질의가 같으므로, 값이 있는 축만 따로 세면 질의 수를 아낀다(조건이 없으면 질의 1개).

    Args:
        filters: 선필터 또는 ``None``.

    Returns:
        ``{"topics","subtopics","tags"}`` 의 부분집합.
    """
    if filters is None:
        return frozenset()
    return frozenset(
        name for name in ("topics", "subtopics", "tags") if getattr(filters, name, ())
    )


def build_facet_plan(
    query: str,
    semantic_ids: Sequence[str] = (),
    *,
    filters: SearchFilters | None = None,
    total_cap: int = TOTAL_CAP_DEFAULT,
    facet_size: int = FACET_SIZE_DEFAULT,
    axes: Sequence[str] = tuple(FACET_FIELDS),
    operator: str = WORD_OPERATOR_DEFAULT,
) -> list[dict[str, Any]]:
    """집계 **계획** — 어떤 축을 어떤 조건으로 셀지 정한다(순수 · 질의를 보내지 않는다).

    축마다 자기 조건을 빼고 세야 칩으로 갈아탈 수 있다(모듈 docstring 「칩은 자기 조건을 뺀 채 센다」).
    빼는 조합이 같은 축들은 **한 질의로 묶는다** — 예를 들어 주제만 골랐다면 하위주제·태그 축은 뺄 것이
    없어 기본 질의에 함께 실리고, 주제 축만 따로 묻는다(질의 2개).

    Args:
        query: 검색어.
        semantic_ids: 뜻으로 걸린 자산 id 들. 모든 질의가 **같은 목록**을 써야 칩과 클릭이 맞는다.
        filters: 선필터.
        total_cap: 전체 개수를 정확히 셀 상한.
        facet_size: 축마다 받을 항목 수.
        axes: 셀 축 이름들.
        operator: 단어 매칭 연산자.

    Returns:
        ``[{"axes": (축…), "body": {…}, "total": bool}]``. **첫 항목이 조건을 전부 적용한 질의**이며
        전체 개수를 센다(``total`` 참). 나머지는 칩 전용이라 개수를 쓰지 않는다.

    Raises:
        ValueError: 모르는 축 이름이거나 ``total_cap``·``facet_size`` 가 1 미만일 때.
    """
    unknown = [a for a in axes if a not in FACET_FIELDS]
    if unknown:
        raise ValueError(f"알 수 없는 좁히기 축: {unknown} (허용: {sorted(FACET_FIELDS)})")
    active = _active_filter_fields(filters)

    # 축을 "빼야 할 조건 조합" 으로 묶는다. 빼는 것이 없는 축은 기본 질의(조건 전부 적용)에 실린다.
    groups: dict[frozenset[str], list[str]] = {}
    for axis in axes:
        drop = frozenset(FACET_SELF_FILTERS.get(axis, ())) & active
        groups.setdefault(drop, []).append(axis)

    base_axes = tuple(groups.pop(frozenset(), []))
    plan: list[dict[str, Any]] = [{
        "axes": base_axes,
        "total": True,
        "body": build_facet_body(query, semantic_ids, filters=filters, total_cap=total_cap,
                                 facet_size=facet_size, axes=base_axes, operator=operator),
    }]
    # 순서를 못 박는다 — 질의 순서가 흔들리면 응답 짝짓기가 어긋난다.
    for drop in sorted(groups, key=lambda d: sorted(d)):
        scoped = replace(filters, **dict.fromkeys(drop, ())) if filters is not None else None
        plan.append({
            "axes": tuple(groups[drop]),
            "total": False,
            "body": build_facet_body(query, semantic_ids, filters=scoped, total_cap=total_cap,
                                     facet_size=facet_size, axes=tuple(groups[drop]),
                                     operator=operator),
        })
    return plan


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


def _run_facets(client: Any, index: str, plan: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """집계 계획을 실행한다 — 여러 개면 **한 번의 왕복**(msearch)으로 묶는다.

    질의가 하나면 평범한 검색으로 보낸다(묶을 것이 없는데 묶으면 응답 껍데기만 늘어난다).

    Args:
        client: OpenSearch 클라이언트.
        index: 색인 이름.
        plan: ``build_facet_plan`` 결과.

    Returns:
        계획과 **같은 순서**의 응답 목록.

    Raises:
        RuntimeError: 묶음 응답 중 하나라도 실패했을 때. 🔴 묶음 질의는 실패를 예외로 올리지 않고
            응답 안에 담아 주므로, 확인하지 않으면 **칩이 조용히 빈 채로** 화면에 나간다.
    """
    if len(plan) == 1:
        return [client.search(index=index, body=plan[0]["body"])]
    lines: list[dict[str, Any]] = []
    for entry in plan:
        lines.append({})  # 색인은 위에서 한 번 지정하므로 머리줄은 비운다
        lines.append(dict(entry["body"]))
    responses = (client.msearch(index=index, body=lines) or {}).get("responses") or []
    if len(responses) != len(plan):
        raise RuntimeError(f"묶음 집계 응답 수가 맞지 않는다: {len(responses)} != {len(plan)}")
    for entry, resp in zip(plan, responses, strict=True):
        if isinstance(resp, Mapping) and resp.get("error"):
            raise RuntimeError(f"집계 질의 실패(축 {entry['axes']}): {resp['error']}")
    return list(responses)


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
    sort: str = SORT_DEFAULT,
    sort_depth: int = SORT_DEPTH_DEFAULT,
    min_cosine: float = SEMANTIC_MIN_COSINE_DEFAULT,
    semantic_cap: int = SEMANTIC_CAP_DEFAULT,
    operator: str = WORD_OPERATOR_DEFAULT,
) -> dict[str, Any]:
    """조건으로 좁힌 파일을 **유사도 순 한 페이지 + 정확한 전체 개수 + 좁히기 칩**으로 조회한다.

    "적힌 숫자 = 누르면 나오는 수"가 성립한다 — 개수와 칩을 세는 대상이 필터가 적용되는 대상과 같기
    때문이다(2026-08-26 원칙). 페이지를 넘겨도 개수와 순서가 흔들리지 않는다(실측: 페이지 겹침 일치).

    Args:
        client: OpenSearch 클라이언트.
        index: 색인 이름.
        query: 검색어. 빈 값이면 ``ValueError``(조건만으로 훑는 화면은 이 함수의 몫이 아니다).
        query_vector: 질의 임베딩. **문서와 같은 채널**로 만들어야 한다. 정렬 방식과 무관하게
            필요하다 — 뜻으로 집합에 들어오는 파일을 판정하는 데 쓰인다.
        filters: 선필터(주제·하위주제·태그·기간·확장자). 주제·하위주제·태그는 **여럿**을 받으며
            같은 축의 여러 값은 「또는」이다.
        from_: 페이지 시작 위치. ``from_ + size`` 가 정렬별 깊이 한계를 넘으면 ``ValueError`` —
            그 밖은 순위를 **매기지 않은** 구간이라 빈 페이지로 돌려주면 "끝"과 구분되지 않는다.
            반면 ``from_`` 이 전체 개수를 넘는 것은 오류가 아니라 그냥 **끝을 지난 것**이므로
            빈 ``rows`` 와 정상 ``total`` 을 돌려준다(둘은 다른 상황이다).
        size: 이 페이지의 행 수(1 이상).
        rank_depth: 관련도 정렬의 순위·페이징 깊이. 필드 정렬은 ``sort_depth`` 를 쓴다.
        total_cap: 개수를 정확히 셀 상한.
        facet_size: 축마다 받을 칩 수.
        axes: 셀 축 이름들.
        pipeline: 정규화·결합 파이프라인 이름. **관련도 정렬에만** 붙인다(필드 정렬은 하이브리드
            질의가 아니라 정규화할 것이 없다).
        sort: 정렬 이름(``SORT_OPTIONS`` 의 키). 기본은 관련도.
        sort_depth: 필드 정렬로 넘길 수 있는 깊이(색인 결과창 한계).
        min_cosine: 뜻으로 집합에 들어올 유사도 하한(코사인).
        semantic_cap: 뜻으로 걸린 자산 id 를 받아올 상한.
        operator: 단어 매칭 연산자.

    Returns:
        ``{rows, total, total_capped, facets, from, size, sort}``. ``total_capped`` 가 참이면
        ``total`` 은 "이 수 이상"이라는 뜻이다(화면이 "1만 건 이상"으로 표기한다). ``facets`` 는
        ``{축: [{key, label, count}]}`` 이고, 각 칩 수는 **그 칩 하나만 골랐을 때 나오는 수**다
        (모듈 docstring 참조 — 같은 축의 다른 선택은 세는 데서 빼기 때문이다).

    Raises:
        ValueError: 빈 질의 · 범위 밖 페이지 · 잘못된 축·상한·정렬 · 관련도 정렬인데 임베딩 없음.
        RuntimeError: 묶음 집계 질의 중 하나가 실패했을 때.
        OpenSearch 미도달 예외는 감싸지 않고 그대로 올린다 — 결과가 백엔드 가용성에 따라 달라지면 안 된다.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("파일 검색은 검색어가 필요하다(조건만으로 훑는 경로는 따로 둔다)")
    if sort not in SORT_OPTIONS:
        raise ValueError(f"알 수 없는 정렬: {sort!r} (허용: {sorted(SORT_OPTIONS)})")
    if size < 1 or from_ < 0:
        raise ValueError(f"페이지 범위 오류: from_={from_!r} size={size!r}")
    # 깊이 한계는 정렬 방식에 따라 다르다 — 관련도는 이웃 탐색 깊이에, 필드 정렬은 색인 결과창에 걸린다.
    by_field = SORT_OPTIONS[sort] is not None
    depth = int(sort_depth if by_field else rank_depth)
    if from_ + size > depth:
        raise ValueError(
            f"넘길 수 있는 깊이를 넘는 페이지다: from_+size={from_ + size} > {depth} "
            f"(정렬 {sort!r}) — 조건을 더 걸거나 정렬을 바꿔야 한다")

    # 🔴 **개수를 먼저 센다.** 순위 질의는 결과 끝을 넘는 페이지를 요청하면 오류를 낸다
    #    ("Reached end of search result" · 실측). 끝을 넘은 페이지는 오류가 아니라 **빈 페이지**여야
    #    맞으므로, 총계를 먼저 알고 그때만 순위를 묻는다(질의 하나를 아끼는 효과도 있다).
    # ① 뜻으로 걸린 자산을 **한 번만** 구해 id 로 굳힌다. 조건마다 벡터 검색을 다시 하면 근사
    #    검색이 다른 답을 주어 칩 건수와 클릭 결과가 어긋난다(실측 `등산` 칩 12 대 클릭 13).
    semantic = client.search(
        index=index,
        body=build_semantic_body(query_vector, min_cosine=min_cosine, cap=semantic_cap),
    )
    semantic_ids = [
        str((h.get("_source") or {}).get("asset_id") or "")
        for h in ((semantic.get("hits") or {}).get("hits") or [])
    ]
    semantic_ids = [a for a in semantic_ids if a]

    plan = build_facet_plan(q, semantic_ids, filters=filters, total_cap=total_cap,
                            facet_size=facet_size, axes=axes, operator=operator)
    responses = _run_facets(client, index, plan)
    total_info = (responses[0].get("hits") or {}).get("total") or {}
    total = int(total_info.get("value") or 0)

    hits: list[dict[str, Any]] = []
    if from_ < total:
        params = {} if by_field else {"search_pipeline": pipeline}
        rank = client.search(
            index=index,
            body=build_rank_body(q, query_vector, semantic_ids=semantic_ids, filters=filters,
                                 from_=from_,
                                 # 남은 것보다 더 달라고 하면 같은 오류가 난다 — 남은 만큼만 청한다.
                                 size=min(size, total - from_), rank_depth=rank_depth, sort=sort,
                                 operator=operator),
            params=params,
        )
        hits = ((rank.get("hits") or {}).get("hits") or [])

    facets: dict[str, list[dict[str, Any]]] = {}
    for entry, resp in zip(plan, responses, strict=True):
        aggs = resp.get("aggregations") or {}
        for axis in entry["axes"]:
            facets[axis] = _facet_items(axis, aggs.get(axis))
    return {
        "rows": [_row(h) for h in hits],
        "total": total,
        # 상한에 걸렸으면 검색 엔진이 ``gte``(이 수 이상)로 알려 준다.
        "total_capped": str(total_info.get("relation") or "eq") != "eq",
        # 축 순서는 요청 순서를 따른다(화면이 칩 묶음을 그 순서로 그린다).
        "facets": {axis: facets.get(axis, []) for axis in axes},
        "from": int(from_),
        "size": int(size),
        "sort": sort,
    }
