"""092 — 개체(멀티모달 메타) **OpenSearch 융합 검색**. BM25(형태소) + kNN(임베딩).

무엇을 하는 모듈인가: 개체를 검색 엔진에서 찾는다. 자산 검색이 쓰는 그 방식(BM25 + kNN 을
정규화해 섞고 게이트로 무관 결과를 막는다)을 개체에 맞게 조정해 옮긴 것이다.

**왜 옮겼나**(spec 092): 개체 검색만 PG ``entity_embedding`` 을 직접 조회해 037 "OpenSearch 단일
백엔드" 원칙에서 예외로 남아 있었고, 짧은 한국어 단어에서 임베딩이 **뜻이 아니라 글자**를 보는
결함이 드러났다(`한글` → 한라산·한강). nori 형태소가 이를 구조적으로 배제한다 —
`한글창제`는 ``['한글','창제']`` 로 쪼개져 걸리지만 `한라산`은 ``['한라','산']`` 이라 겹치지 않는다.

**두 신호를 왜 섞나**: 착수 전 실측에서 BM25 단독은 정밀하지만 재현율이 낮았고(글자가 없으면
못 찾는다 — `발효`→김치), kNN 단독은 표기 유사성에 속았다. 섞으면 서로의 실패를 덮는다.

🔴 **게이트는 kNN 에만 건다.** BM25 결과까지 막으면 단어 하나 재현율이 **90% → 50%** 로 무너진다
(실측) — BM25 로는 명확히 걸리는 질의인데 벡터 신호가 약한 경우가 많기 때문이다. 자산 검색의
lexical rescue("게이트 실패해도 어휘 증거가 있으면 회수")와 같은 취지다.

🔴 **operator 는 ``or``**(자산은 ``and``). 개체는 텍스트가 짧아 "모든 낱말이 다 있어야 한다"가
가혹하다 — 실측 다어절 73.3%(and) vs **86.7%(or)**. 대가로 무관 질의 결과가 0.2 → 1.4건이 되는데,
BM25 노이즈는 "그 글자가 실제로 있어서" 걸린 것이라 화면에 근거를 보일 수 있다.

**이 모듈에는 계약이 둘 있다**(099 G4 · 2026-09-17). 섞지 말 것 —

| 함수 | 무엇을 돌려주나 | 쓰는 장치 | 누가 순서를 정하나 |
|---|---|---|---|
| ``search_entities_hybrid`` | **가장 맞는 5개**(순위) | BM25 + kNN + 게이트 + min-max 융합 | 융합 점수 |
| ``match_entity_keys`` | **맞는 것 전부**(집합) + **어느 갈래로 걸렸는지** | BM25 낱말 매칭 **∪** 게이트 통과한 kNN 창 | DB 목록(구성 자산 수 내림차순) |

099 는 "결과 내 재검색"을 **결과 집합 전체**에 적용하기로 했다(spec §3-1). 그러려면 기반이 상위 5가
아니라 집합이어야 한다. 그리고 개체도 낱말 단위로 맞추기로 하면서 ``q`` 와 재검색이 **같은 일**
(낱말을 던져 매칭 개체 집합을 얻기)이 되어 함수 하나로 접혔다(spec §3-2a) — 한쪽만 고쳐지는 사고가
원리상 없어진다. 순위 경로는 **되돌림 경로로 그대로 남긴다**(plan §1-④ · 097 이 ``search_files`` 를
두고 ``browse_files`` 를 새로 만든 것과 같은 판단).

🔴 **집합도 「뜻」으로 찾는다 — 두 갈래의 합집합**(2026-09-17 사용자 결정. T018 의 "낱말만"을 뒤집었다)::

    집합 = ① BM25 낱말 매칭 전부  ∪  ② (kNN 게이트 통과 시) kNN 창(20) 안 전부

낱말만 쓰면 **글자가 없는 매칭**을 통째로 잃는다 — `발효`→김치 0건, `도자기`→고려청자 0건.
089 이후 남은 검색 실패 30건이 바로 그 어휘 불일치였고, `090-entity-semantic-search` 스펙 하나가
통째로 그것을 풀려고 있었다. 도서관에 비유하면 ① 은 "제목에 그 글자가 있는 책"을 뽑는 색인 카드고,
② 는 "뜻이 가까운 책"을 집어 오는 사서다. 사서가 아무 근거 없이 아무 책이나 들고 오지 않도록
**게이트**("1등이 나머지 무리보다 튀어나왔나")를 통과할 때만 ② 를 더한다.

⚠️ **절대 코사인 하한으로는 못 가른다**(2026-09-17 실 색인 실측). 무의미 질의 `존재하지않는낱말xyz`
1등이 **0.442** 인데 유관 질의 `불교 건축` 1등이 **0.456** 이라 값이 붙어 있다. 자산 검색의
``SEMANTIC_MIN_COSINE_DEFAULT``(0.60)를 개체에 쓰면 개체 전 구간(≤0.64)이 잘린다. 그래서 개체는
**상대 게이트**(``gate_signal`` + ``passes_cutoff(eps=0.15, floor=0)``)를 그대로 쓴다 —
🔴 **새 임계를 만들지 않는다**(재보정할 개체 골든이 없어서, 새 숫자는 근거 없는 숫자가 된다).

🔴 **집합만 주지 않고 「어느 갈래로 걸렸는지」를 함께 준다**(2026-09-17 사용자 결정) —
``EntityMatchSet``. 뜻(kNN)으로 걸린 결과는 **화면 어디에도 검색어가 보이지 않기** 때문이다:
`왕실 무덤` 으로 찾으면 `영릉` 이 나오는데 그 카드에는 "왕실 무덤" 이라는 글자가 한 자도 없다 →
근거가 없으면 사용자는 "검색이 고장났나"로 읽는다. 판정은 **이미 두 갈래로 따로 계산**되고
마지막에 합쳐질 뿐이라, 버리지 않고 함께 돌려주기만 하면 된다 — **추가 질의는 0회**다.

설계 배경: `specs/092-entity-search-opensearch`(spec §2 · plan §설계 결정) ·
`specs/099-refine-requery-entity-cursor`(spec §3-2·§3-2a · tasks T018·T019)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, NamedTuple

from src.config.search_constants import (
    ENTITY_BM25_FIELDS_DEFAULT,
    ENTITY_BM25_OPERATOR_DEFAULT,
    ENTITY_MATCH_FIELDS_DEFAULT,
    ENTITY_MATCH_MAX_HITS_DEFAULT,
    ENTITY_SEARCH_TOP_N_DEFAULT,
    ENTITY_SEMANTIC_GATE_EPS_DEFAULT,
    ENTITY_SET_GATE_EPS_DEFAULT,
    ENTITY_SET_GATE_FLOOR_DEFAULT,
)
from src.search.fusion import (
    gate_signal,
    knn_score_to_cosine,
    minmax_normalize,
    passes_cutoff,
)

# 융합에 쓸 후보 깊이. 상위 3만 받으면 정규화 기준이 좁아 점수가 왜곡되고, 게이트의 배경 수준
# (하위 절반 평균)도 상위권 평균이 되어 신호가 죽는다(090 후속에서 겪은 그 함정).
DEFAULT_CANDIDATE_SIZE = 20

# BM25 와 kNN 의 융합 가중(자산 검색 기본과 같은 0.5/0.5). 한쪽을 키우면 그 신호의 실패 모드가
# 그대로 드러난다 — BM25 쪽은 글자가 없으면 못 찾고, kNN 쪽은 표기 유사성에 속는다.
DEFAULT_FUSION_WEIGHTS = (0.5, 0.5)

# 게이트가 절대 하한을 쓰지 않는다는 사실을 못 박는다(090 후속 판단 — 분포가 겹쳐 절대값으로는
# 정답과 무관을 가를 수 없다).
_GATE_NO_FLOOR = 0.0

_LOG = logging.getLogger(__name__)


def _split_doc_id(doc_id: str) -> tuple[str, str]:
    """문서 id(``타입/표기``)를 되돌린다.

    Args:
        doc_id: ``entity_doc_id`` 가 만든 문자열.

    Returns:
        ``(entity_type, entity_uid)``. 구분자가 없으면 타입은 빈 문자열.
    """
    head, sep, tail = doc_id.partition("/")
    return (head, tail) if sep else ("", head)


def _rows(hits: Sequence[Any]) -> list[tuple[str, str, str, float]]:
    """OS hit 들을 ``(doc_id, entity_type, entity_uid, score)`` 로 편다.

    Args:
        hits: ``response['hits']['hits']``. ``_source`` 가 없어도 id 로 개체를 되살린다
            (색인 문서 모양이 바뀌어도 검색이 죽지 않게).

    Returns:
        평평한 행 목록(입력 순서 유지).
    """
    out: list[tuple[str, str, str, float]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        doc_id = str(hit.get("_id", ""))
        src = hit.get("_source") or {}
        etype = str(src.get("entity_type") or "") or _split_doc_id(doc_id)[0]
        uid = str(src.get("entity_uid") or "") or _split_doc_id(doc_id)[1]
        out.append((doc_id, etype, uid, float(hit.get("_score") or 0.0)))
    return out


def search_entities_hybrid(
    client: Any,
    index: str,
    *,
    query: str,
    query_vector: Sequence[float],
    top_n: int = ENTITY_SEARCH_TOP_N_DEFAULT,
    fields: Sequence[str] = ENTITY_BM25_FIELDS_DEFAULT,
    operator: str = ENTITY_BM25_OPERATOR_DEFAULT,
    gate_eps: float = ENTITY_SEMANTIC_GATE_EPS_DEFAULT,
    candidate_size: int = DEFAULT_CANDIDATE_SIZE,
) -> list[dict[str, Any]]:
    """개체를 BM25 + kNN 융합으로 찾는다.

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름(자산 인덱스와 달라야 한다).
        query: 검색어 원문. 형태소 분석은 엔진이 한다(``nori_user``).
        query_vector: 질의 임베딩(저장 차원). 🔴 개체 벡터와 **같은 모델·같은 채널**이어야 한다 —
            다른 모델로 만들면 유사도가 뜻을 잃는다(090 G4 에서 실제로 겪었다).
        top_n: 돌려줄 개수. 기본 5 는 실측 확정치다 — 3으로 자르면 4~5위에 있는 정답을
            통째로 잃는다(상수 주석에 스윕 표).
        fields: BM25 필드와 가중(기본 = 상수 · 이름을 가장 무겁게).
        operator: BM25 결합자. 기본 ``or`` — 자산의 ``and`` 를 그대로 쓰지 않는다(모듈 docstring).
        gate_eps: kNN 게이트 임계((top − baseline) 하한). 기본은 090 후속 실측 확정치.
        candidate_size: 각 신호에서 받을 후보 깊이(정규화·게이트 계산의 표본).

    Returns:
        ``[{entity_type, entity_uid, score, bm25, cosine, by_text, by_semantic}]`` — 융합 점수
        내림차순 → 문서 id 오름차순(동점 tiebreak 고정 · 결정성). 조회 실패·빈 응답이면 빈 목록.

    ⚠️ **게이트가 막아도 BM25 결과는 나간다**(lexical rescue). 벡터 신호가 약한 것과 글자가 맞은
    것은 다른 이야기다.
    """
    bm25_body = {
        "size": candidate_size,
        "query": {"multi_match": {"query": query, "fields": list(fields),
                                  "type": "best_fields", "operator": operator}},
    }
    knn_body = {
        "size": candidate_size,
        "query": {"knn": {"vec": {"vector": list(query_vector), "k": candidate_size}}},
    }
    bm25_rows = _rows(client.search(index=index, body=bm25_body).get("hits", {}).get("hits", []))
    knn_rows = _rows(client.search(index=index, body=knn_body).get("hits", {}).get("hits", []))

    # kNN 게이트 — 1등이 나머지 무리보다 튀어나왔는가(자산 검색과 같은 공식).
    cosines = [knn_score_to_cosine(score) for _, _, _, score in knn_rows]
    top, baseline = gate_signal(cosines)
    knn_passed = bool(knn_rows) and passes_cutoff(top, baseline, eps=gate_eps, floor=_GATE_NO_FLOOR)

    w_bm25, w_knn = DEFAULT_FUSION_WEIGHTS
    bm25_norm = dict(zip([r[0] for r in bm25_rows],
                         minmax_normalize([r[3] for r in bm25_rows]), strict=True))
    knn_norm = (dict(zip([r[0] for r in knn_rows], minmax_normalize(cosines), strict=True))
                if knn_passed else {})
    cosine_by_id = dict(zip([r[0] for r in knn_rows], cosines, strict=True))
    meta = {r[0]: (r[1], r[2]) for r in (*knn_rows, *bm25_rows)}
    raw_bm25 = {r[0]: r[3] for r in bm25_rows}

    scored = [
        (
            -(w_bm25 * bm25_norm.get(doc_id, 0.0) + w_knn * knn_norm.get(doc_id, 0.0)),
            doc_id,  # 동점이면 문서 id 오름차순 — 같은 입력에 같은 순서가 나와야 한다.
        )
        for doc_id in set(bm25_norm) | set(knn_norm)
    ]
    scored.sort()
    out: list[dict[str, Any]] = []
    for neg_score, doc_id in scored[:top_n]:
        etype, uid = meta.get(doc_id, _split_doc_id(doc_id))
        out.append({
            "entity_type": etype,
            "entity_uid": uid,
            "score": round(-neg_score, 6),
            "bm25": round(raw_bm25.get(doc_id, 0.0), 4),
            "cosine": round(cosine_by_id.get(doc_id, 0.0), 4) if knn_passed else 0.0,
            "by_text": doc_id in bm25_norm,
            "by_semantic": doc_id in knn_norm,
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 099 G4 — **집합 판정**(순위 아님). 위 융합 경로와 같은 인덱스를 보지만 계약이 다르다.
# ══════════════════════════════════════════════════════════════════════════════


def entity_match_clause(query: str | None) -> dict[str, Any] | None:
    """낱말을 **개체 집합을 가르는 AND 절**로 만든다(순수 · 099 G4).

    쇼핑몰 검색칸에 `노트북 16인치` 를 치면 둘 다 들어간 상품만 남는 그 규칙이다. 조합은 091 그대로
    — **낱말끼리 AND, 한 낱말 안에서 필드끼리 OR**. `전통음식 배추` 처럼 한 낱말은 키워드에, 다른
    낱말은 구성 자산 요약에 있는 경우가 흔해서 **한 필드에 전부** 있기를 요구하면 거의 걸리지 않는다
    (091 §2-4). 반대로 낱말끼리 OR 로 하면 좁혀지지 않는다 — 그건 찾아오기 규칙이다.

    ⚠️ **재검토 지점**: 낱말 결합자는 ``and`` 다. 092 순위 경로가 고른 ``or`` 였다면 집합이 더
    넓어졌을 것이다(한 낱말만 맞은 개체까지 들어와 재현율↑·무관 결과↑ — 092 스윕 0.4→1.4건/질의).
    지금은 어느 쪽이 나은지 **판정할 개체 골든이 없어** 현행을 유지한다. 골든이 생기면 여기를 다시 본다.

    ⚠️ 그래서 ``multi_match`` + ``operator=and`` 를 쓰지 않는다. ``multi_match`` 의 ``and`` 는
    "한 필드 안에 모든 낱말"이라 위의 흔한 경우가 통째로 탈락한다. 파일 검색의
    ``file_search.refine_clause`` 와 **같은 모양**인 이유이기도 하다 — 화면마다 다른 규칙을 사용자가
    기억할 이유가 없다(spec 099 §3-4 사용자 결정).

    Args:
        query: 낱말들(공백 구분). ``None``·빈 문자열·공백뿐이면 **절을 만들지 않는다**
            (호출부가 "조건 없음"으로 읽는다 — 되돌림의 실질).

    Returns:
        ``bool.filter`` 안에 낱말별 절을 담은 ``bool`` 절. 만들 것이 없으면 ``None``.
    """
    # 정규화(``normalize_text_key``)를 걸지 않는다 — 색인과 질의가 **같은 분석기**(``nori_user``)를
    #   지나야 대칭이 성립한다. 미리 소문자로 바꾸면 분석기 쪽 처리와 어긋난다(G3 T014 와 같은 규율).
    tokens = [t for t in (query or "").split() if t]
    if not tokens:
        return None
    return {"bool": {"filter": [
        {"bool": {
            # 🔴 ``operator=and`` 는 **한 낱말이 형태소로 쪼개졌을 때 그 조각들**에 건다.
            #   없으면 ``match`` 기본이 OR 라 조각 하나만 걸려도 통과한다 — 2026-09-17 실 색인 실측:
            #   ``존재하지않는낱말xyz`` → nori ``['존재','하','지','않','는','낱','말','xyz']`` →
            #   ``하``·``지``·``말`` 같은 흔한 조각 때문에 **822개 중 818개(99.5%)** 가 매칭됐다
            #   (``숭례문`` 92→4 · ``석굴암`` 70→15 도 같은 노이즈였다).
            # ⚠️ 이것은 **필드 간** and 가 아니다(그건 위 docstring 이 금지한 것 — `전통음식`+`배추` 가
            #   서로 다른 필드에 있으면 탈락한다). 낱말끼리 AND · 필드끼리 OR 라는 계약은 그대로이고,
            #   여기서 조이는 것은 **낱말 하나 안의 형태소 조각**이다.
            "should": [{"match": {field: {"query": token, "operator": "and"}}}
                       for field in ENTITY_MATCH_FIELDS_DEFAULT],
            "minimum_should_match": 1,
        }}
        for token in tokens
    ]}}


class EntitySemanticMatch(NamedTuple):
    """의미(kNN) 갈래의 결과 — 키 집합과 **게이트 판정 사실**을 함께 돌려준다.

    왜 사실까지 돌려주나: 게이트가 막으면 의미 갈래가 통째로 사라지는데, 그 일이 조용히 일어나면
    호출부는 "왜 못 찾지"를 추적할 수 없다. 로그는 사후 추적용이고, 값은 화면이 근거를 보여 줄 때
    쓴다(044 의 "질의 근거를 응답에 싣는다"와 같은 취지).

    Attributes:
        keys: 통과했을 때의 개체 키 ``(entity_type, entity_uid)`` 집합. 막혔으면 **빈 집합**이다.
        gate_passed: 게이트를 통과했는지. 후보가 0건이어도 ``False`` 이므로 "막혔다"와 "후보가
            없다"를 가르려면 ``sample_size`` 를 함께 본다.
        top: 표본의 최고 코사인(진단용 · 표본이 없으면 0.0).
        baseline: 배경 수준 = 표본 하위 절반 평균(진단용 · 표본이 2개 미만이면 0.0).
        sample_size: 실제로 받은 kNN 후보 수(≤ 창 크기). 0이면 색인에 후보 자체가 없었다는 뜻.
    """

    keys: frozenset[tuple[str, str]]
    gate_passed: bool
    top: float
    baseline: float
    sample_size: int


class EntityMatchSet(NamedTuple):
    """집합 판정 결과 — 결과 집합과 **어느 갈래로 걸렸는지**를 함께 돌려준다.

    ``EntitySemanticMatch`` 와 **같은 결**이다(NamedTuple · 키는 ``frozenset`` · 판정 사실을 값으로).
    호출부가 결과만 쓰려면 ``keys`` 하나만 보면 되고, 화면에 "왜 이게 나왔나"를 보이려면 갈래 둘을
    본다 — 도서관 비유로 ``text_keys`` 는 "제목에 그 글자가 있어서" 뽑힌 책이고 ``semantic_keys``
    는 "뜻이 가까워서" 사서가 집어 온 책이다. 뒤엣것은 카드에 검색어가 한 자도 없으므로 근거를
    보여 주지 않으면 사용자가 검색을 의심하게 된다.

    🔴 **``keys`` 는 파생값**(= ``text_keys | semantic_keys``)이다. 갈래를 따로 돌려줘도 결과
    집합 자체는 종전과 **완전히 같다** — 이 동봉은 판정 규칙을 하나도 바꾸지 않는다.

    Attributes:
        keys: 결과 집합 = 두 갈래의 합집합. 매칭이 없으면 빈 집합(= 0건이며 "필터 없음"이 아니다).
        text_keys: ① 낱말(BM25) 갈래로 걸린 키들 — 그 글자가 **실제로 적혀 있는** 개체다.
        semantic_keys: ② 의미(kNN) 갈래로 걸린 키들. 게이트에 막혔거나 벡터를 주지 않았으면
            **빈 집합**이다(일부만 버리지 않는다 · 090 후속 게이트 계약).
        semantic_gate_passed: ② 가 게이트를 통과했는지. ``False`` 이면 ``semantic_keys`` 가 빈
            집합이며, "막혔다"·"후보가 없다"·"껐다"의 구분은 경고 로그가 말한다.
    """

    keys: frozenset[tuple[str, str]]
    text_keys: frozenset[tuple[str, str]]
    semantic_keys: frozenset[tuple[str, str]]
    semantic_gate_passed: bool


def semantic_entity_keys(
    client: Any,
    index: str,
    *,
    query_vector: Sequence[float],
    candidate_size: int = DEFAULT_CANDIDATE_SIZE,
    gate_eps: float = ENTITY_SET_GATE_EPS_DEFAULT,
    gate_floor: float = ENTITY_SET_GATE_FLOOR_DEFAULT,
) -> EntitySemanticMatch:
    """**뜻이 가까운** 개체를 kNN 으로 고르고, 믿을 만할 때만 돌려준다(099 G4 · 2026-09-17).

    집합 판정의 ② 갈래다. ① 낱말 갈래가 "그 글자가 적혀 있나"를 보는 색인 카드라면 이쪽은 "뜻이
    가까운가"를 보는 사서다 — `발효` 라고 물으면 그 글자가 없는 **김치**를 데려온다. 089 이후 남은
    검색 실패 30건이 이 어휘 불일치였다.

    🔴 **게이트는 순위 경로가 쓰는 그 장치 그대로다**(``gate_signal`` + ``passes_cutoff``).
    새 임계를 만들지 않는다 — 재보정할 개체 골든이 지금 없어서(092 질의셋은 코퍼스 전면 교체로
    정답 0건) 새로 고른 숫자는 근거 없는 숫자가 된다. 절대 코사인 하한도 두지 않는다: 실측에서
    무의미 질의 1등(0.442)과 유관 질의 1등(0.456)이 붙어 있어 절대값으로는 가를 수 없다.

    🔴 **창(후보 깊이)은 ``candidate_size``(20)로 고정한다 — 이 고정이 설계의 핵심이다.**
    창을 키우면 배경 수준(``baseline`` = 표본 하위 절반 평균)이 내려가고, 게이트가 보는 격차
    ``top − baseline`` 이 커져 **반드시 더 관대해진다**(099 T020 단조성 증명: 풀 k→K(K≥2k) 이면
    차단집합(K) ⊆ 차단집합(k)). 즉 창을 넓히면 "무관한데 통과"가 늘어난다. eps 0.15 는 2026-09-01
    에 보정된 값이므로, 그 보정이 성립하려면 **창이 그때와 같은 모양**이어야 한다. 그래서 집합
    상한(``max_hits`` = 10,000)을 이 창에 쓰지 않는다 — 상한은 ① 낱말 갈래의 폭주 방지선일 뿐이다.

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름(자산 인덱스와 다르다).
        query_vector: 질의 임베딩(저장 차원). 🔴 개체 벡터와 **같은 모델·같은 채널**이어야 한다 —
            다른 모델로 만들면 유사도가 뜻을 잃는다(090 G4 에서 실제로 겪었다).
        candidate_size: kNN 창 크기(=받을 후보 수 = ``k``). 🔴 **키우지 말 것**(위 단조성).
        gate_eps: 게이트의 **상대 격차**((top − baseline) 하한). 기본 0 — 꺼져 있다.
        gate_floor: 게이트의 **절대 하한**(주판정 · 기본 0.44). ``top`` 이 이 값 미만이면
            의미 갈래를 통째로 버린다. 실측에서 고른 값이라 실사용 로그가 쌓이면 다시 본다.

    Returns:
        ``EntitySemanticMatch``. 게이트를 못 넘거나 후보가 없으면 ``keys`` 가 빈 집합이다
        (일부만 버리지 않는다 — 통째로 버리는 것이 090 후속 게이트의 계약이다).
    """
    body = {
        # 🔴 size 와 k 를 **같은 값**으로 묶는다. 갈리면 게이트·정규화가 보는 표본과 실제로 받은
        #    결과 수가 어긋나 판정 근거가 흔들린다.
        "size": int(candidate_size),
        "_source": ["entity_type", "entity_uid"],
        "query": {"knn": {"vec": {"vector": list(query_vector), "k": int(candidate_size)}}},
    }
    hits = (client.search(index=index, body=body) or {}).get("hits", {}) or {}
    rows = _rows(hits.get("hits", []) or [])
    cosines = [knn_score_to_cosine(score) for _doc_id, _etype, _uid, score in rows]
    top, baseline = gate_signal(cosines)
    # 🔴 주판정이 **절대 하한**으로 바뀌었다(2026-09-21). 상대 격차(eps)는 기본 0 이라 사실상
    #    꺼져 있다 — 색인이 커지면 격차가 줄어 같은 질의가 조용히 막히기 때문이다.
    passed = bool(rows) and passes_cutoff(top, baseline, eps=gate_eps, floor=gate_floor)
    keys = (frozenset((etype, uid) for _doc_id, etype, uid, _score in rows if uid)
            if passed else frozenset())
    return EntitySemanticMatch(keys=keys, gate_passed=passed, top=top, baseline=baseline,
                               sample_size=len(rows))


def match_entity_keys(
    client: Any,
    index: str,
    *,
    query: str | None,
    query_vector: Sequence[float] | None,
    max_hits: int = ENTITY_MATCH_MAX_HITS_DEFAULT,
    candidate_size: int = DEFAULT_CANDIDATE_SIZE,
    gate_eps: float = ENTITY_SET_GATE_EPS_DEFAULT,
    gate_floor: float = ENTITY_SET_GATE_FLOOR_DEFAULT,
) -> EntityMatchSet:
    """질의에 **맞는 개체 전부**의 키 집합을 구한다(순위 없음 · 099 G4 · FR-003).

    ``search_entities_hybrid`` 가 "가장 맞는 5개"라면 이 함수는 "맞는 것 전부"다. 화면은 이 집합을
    ``graph_query.list_entities(uid_allow=…)`` 에 얹어 **구성 자산 수 내림차순**으로 정렬하고 커서로
    이어 읽는다 — 순서를 DB 가 정하므로 여기서 점수를 매길 이유가 없다(spec §3-2a).

    **집합 = ① 낱말 매칭 전부 ∪ ② (게이트 통과 시) kNN 창 안 전부**(2026-09-17 사용자 결정).
    둘은 **순수 합집합**이다 — 순위를 매기지 않으므로 어느 갈래로 들어왔는지에 가중을 주지 않는다.
    ① 은 "글자가 맞았나"만 보고(임계·정규화·절단 없음), ② 는 "뜻이 가까운가"를 본다. ② 를 빼면
    `발효`→김치처럼 글자가 없는 매칭을 통째로 잃는다(090 스펙이 통째로 그것을 위한 것이다).

    🔴 **찾아오기(``q``)와 좁히기(재검색)가 이 함수 하나를 쓴다.** 둘 다 「질의를 던져 매칭 개체
    집합을 얻기」라서다. 결과는 호출부가 교집합으로 합친다(``A ∩ B``) — refine 이 ``q`` 질의를
    바꾸지 않으므로 좁힌 결과는 언제나 좁히기 전 결과의 **부분집합**이다(spec §3-1 · FR-002).

    🔴 **게이트는 ② 에만 건다.** ① 까지 막으면 단어 하나 재현율이 90% → 50% 로 무너진다(092 실측) —
    "벡터 신호가 약하다"와 "글자가 맞았다"는 다른 이야기이기 때문이다(자산 검색의 lexical rescue
    와 같은 취지). ② 가 막히면 **경고 로그**를 남긴다(아래 Returns 아래 문단).

    ⚠️ **낱말 결합자는 ``and`` 다**(``entity_match_clause``). 092 순위 경로가 고른 ``or`` 였다면
    집합이 **더 넓어졌을 것**이다 — 한 낱말만 맞은 개체까지 들어오므로 재현율은 오르고 무관 결과도
    함께 는다(092 스윕: 무관 0.4 → 1.4건/질의). 지금은 어느 쪽이 나은지 **판정할 개체 골든이 없어**
    현행(``and``)을 유지한다. 골든이 생기면 이 한 줄이 재검토 지점이다.

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름(자산 인덱스와 다르다).
        query: 낱말들(공백 구분). 형태소 분석은 엔진이 한다.
        query_vector: 질의 임베딩(개체 색인과 같은 모델·채널). 🔴 **기본값을 두지 않는다** —
            깜빡 빠뜨리면 의미 재현이 조용히 사라지므로 호출부가 매번 명시하게 한다.
            ``None`` 은 "의미 갈래를 일부러 끈다"는 **명시적 선택**이며, 그때도 경고 로그를 남긴다.
        max_hits: ① 낱말 갈래에서 한 번에 받아올 매칭 개체 수 상한(**순위 절단이 아니라 폭주
            방지선**). 상한에 닿으면 집합이 불완전해지므로 경고 로그를 남긴다.
            🔴 이 값은 ② 의 창이 **아니다**(창을 넓히면 게이트가 관대해진다 · 위 함수 주석).
        candidate_size: ② kNN 창 크기(기본 20 · 고정이 원칙).
        gate_eps: ② 게이트의 **상대 격차** 기준. 기본 0 이라 사실상 꺼져 있다 — 색인이 커지면
            격차가 줄어 같은 질의가 조용히 막히기 때문이다(2026-09-21).
        gate_floor: ② 게이트의 **절대 하한**(주판정). 1등 코사인이 이 값 미만이면 의미 갈래를
            버린다. 코사인 값 자체는 색인 크기와 무관해 자료가 늘어도 흔들리지 않는다.

    Raises:
        ValueError: ``query`` 가 비었을 때. 🔴 빈 집합(=0건)과 "묻지 않았다"(=필터 없음)는 **다른
            값**이라 여기서 섞으면 안 된다 — 빈 집합을 돌려주면 교집합에서 결과가 통째로 사라지고,
            반대로 전체를 돌려주면 "검색했는데 전부 나오는" 조용한 오류가 된다.

    Returns:
        ``EntityMatchSet`` — 결과 집합(``keys``)과 **어느 갈래로 걸렸는지**(``text_keys`` ·
        ``semantic_keys`` · ``semantic_gate_passed``). 매칭이 없으면 ``keys`` 가 **빈 집합**
        (= 0건). 같은 입력이면 같은 값이다(헌법 3조 — 집합이라 순서 자체가 없다).

        🔴 갈래를 함께 주는 이유: 뜻으로 걸린 결과는 **화면에 검색어가 보이지 않는다**(`왕실 무덤`
        → `영릉`). 근거를 못 보이면 사용자는 검색을 의심한다. 이미 따로 계산된 값이라 **질의는
        늘지 않는다**(낱말 1회 + 의미 1회 · 종전과 같다).

    로그로 드러나는 것 셋(조용한 실패 방지): ① 낱말 갈래가 상한에서 잘림 · ② 의미 갈래가 게이트에
    막힘(격차·임계 동봉) · ③ 의미 갈래가 아예 꺼짐(벡터 미제공 또는 후보 0건). ②③ 은 반환값
    (``semantic_gate_passed``·빈 ``semantic_keys``)으로도 읽을 수 있고, 진단 수치(top·baseline)가
    필요하면 ``semantic_entity_keys`` 를 직접 부른다.
    """
    clause = entity_match_clause(query)
    if clause is None:
        raise ValueError("집합 판정에는 낱말이 있어야 한다 — 빈 질의를 0건으로 바꾸면 '안 물어봤다'와 구분이 사라진다")
    body = {
        # 순위가 아니라 집합이므로 상한까지 받는다. 화면에 보일 수(``limit``)는 목록 질의가 정한다.
        "size": int(max_hits),
        # 잘렸는지 알아야 경고할 수 있다 — 조용히 잘린 집합은 "있어야 할 게 없는" 오류로만 보인다.
        "track_total_hits": True,
        # 키 말고는 읽지 않는다(카드 재료는 DB 목록이 만든다).
        "_source": ["entity_type", "entity_uid"],
        "query": clause,
    }
    hits = (client.search(index=index, body=body) or {}).get("hits", {}) or {}
    rows = _rows(hits.get("hits", []) or [])
    total = _total_hits(hits.get("total"), len(rows))
    if total > len(rows):
        _LOG.warning("개체 집합 판정이 상한에서 잘렸다 — 매칭 %d건 중 %d건만 받았다(max_hits=%d)",
                     total, len(rows), int(max_hits))
    text_keys = frozenset((etype, uid) for _doc_id, etype, uid, _score in rows if uid)

    if query_vector is None:
        # 의미 갈래를 끈 채로 도는 상태는 **이례적**이다 — `발효`→김치 류를 못 찾게 되므로 남긴다.
        _LOG.warning("개체 집합 판정에서 의미(kNN) 갈래가 꺼졌다 — 질의 벡터가 없다(낱말 매칭 %d건만)",
                     len(text_keys))
        return EntityMatchSet(keys=text_keys, text_keys=text_keys,
                              semantic_keys=frozenset(), semantic_gate_passed=False)

    semantic = semantic_entity_keys(client, index, query_vector=query_vector,
                                    candidate_size=candidate_size, gate_eps=gate_eps,
                                    gate_floor=gate_floor)
    if semantic.sample_size == 0:
        _LOG.warning("개체 의미(kNN) 갈래의 후보가 0건이다 — 색인이 비었거나 벡터 필드가 없다"
                     "(낱말 매칭 %d건만)", len(text_keys))
    elif not semantic.gate_passed:
        # 게이트 차단은 "정답이 없다"는 판정이라 **정상 동작**이지만, 조용하면 "왜 못 찾지"를
        # 추적할 수 없다. 격차와 임계를 함께 남겨 사후에 판정을 재현할 수 있게 한다.
        # 주판정이 절대 하한이므로 **하한과 top 을** 먼저 남긴다 — 격차는 참고값으로 뒤에 붙인다.
        _LOG.warning("개체 의미(kNN) 갈래가 게이트에 막혔다 — top=%.4f < 하한=%s "
                     "(배경=%.4f 격차=%.4f · 후보 %d건) · 낱말 매칭 %d건만 남긴다",
                     semantic.top, gate_floor, semantic.baseline,
                     semantic.top - semantic.baseline, semantic.sample_size, len(text_keys))
    # 🔴 ``keys`` 는 **파생값**이다 — 갈래를 따로 싣는다고 결과 집합이 달라지지 않는다.
    return EntityMatchSet(keys=text_keys | semantic.keys, text_keys=text_keys,
                          semantic_keys=semantic.keys,
                          semantic_gate_passed=semantic.gate_passed)


def _total_hits(total: Any, fallback: int) -> int:
    """``hits.total`` 을 정수로 읽는다(응답 모양 두 가지를 모두 받는다).

    Args:
        total: OpenSearch 응답의 ``hits.total`` — dict(``{"value": n}``) 또는 숫자.
        fallback: 값을 읽을 수 없을 때 쓸 수(받은 행 수 — 경고가 오탐하지 않도록).

    Returns:
        매칭 총 건수.
    """
    if isinstance(total, dict):
        total = total.get("value")
    try:
        return int(total)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(fallback)


__all__ = ["DEFAULT_CANDIDATE_SIZE", "DEFAULT_FUSION_WEIGHTS", "EntityMatchSet",
           "EntitySemanticMatch", "entity_match_clause", "match_entity_keys",
           "search_entities_hybrid", "semantic_entity_keys"]
