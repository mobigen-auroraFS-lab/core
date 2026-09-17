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
| ``match_entity_keys`` | **맞는 것 전부**(집합) | BM25 낱말 매칭 하나 | DB 목록(구성 자산 수 내림차순) |

099 는 "결과 내 재검색"을 **결과 집합 전체**에 적용하기로 했다(spec §3-1). 그러려면 기반이 상위 5가
아니라 집합이어야 한다. 그리고 개체도 낱말 단위로 맞추기로 하면서 ``q`` 와 재검색이 **같은 일**
(낱말을 던져 매칭 개체 집합을 얻기)이 되어 함수 하나로 접혔다(spec §3-2a) — 한쪽만 고쳐지는 사고가
원리상 없어진다. 순위 경로는 **되돌림 경로로 그대로 남긴다**(plan §1-④ · 097 이 ``search_files`` 를
두고 ``browse_files`` 를 새로 만든 것과 같은 판단).

설계 배경: `specs/092-entity-search-opensearch`(spec §2 · plan §설계 결정) ·
`specs/099-refine-requery-entity-cursor`(spec §3-2·§3-2a · tasks T018·T019)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.config.search_constants import (
    ENTITY_BM25_FIELDS_DEFAULT,
    ENTITY_BM25_OPERATOR_DEFAULT,
    ENTITY_MATCH_FIELDS_DEFAULT,
    ENTITY_MATCH_MAX_HITS_DEFAULT,
    ENTITY_SEARCH_TOP_N_DEFAULT,
    ENTITY_SEMANTIC_GATE_EPS_DEFAULT,
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
            "should": [{"match": {field: {"query": token}}}
                       for field in ENTITY_MATCH_FIELDS_DEFAULT],
            "minimum_should_match": 1,
        }}
        for token in tokens
    ]}}


def match_entity_keys(
    client: Any,
    index: str,
    *,
    query: str | None,
    max_hits: int = ENTITY_MATCH_MAX_HITS_DEFAULT,
) -> set[tuple[str, str]]:
    """낱말에 **맞는 개체 전부**의 키 집합을 구한다(순위 없음 · 099 G4 · FR-003).

    ``search_entities_hybrid`` 가 "가장 맞는 5개"라면 이 함수는 "맞는 것 전부"다. 화면은 이 집합을
    ``graph_query.list_entities(uid_allow=…)`` 에 얹어 **구성 자산 수 내림차순**으로 정렬하고 커서로
    이어 읽는다 — 순서를 DB 가 정하므로 여기서 점수를 매길 이유가 없다(spec §3-2a).

    🔴 **찾아오기(``q``)와 좁히기(재검색)가 이 함수 하나를 쓴다.** 둘 다 「낱말을 던져 매칭 개체
    집합을 얻기」라서다. 결과는 호출부가 교집합으로 합친다(``A ∩ B``) — refine 이 ``q`` 질의를
    바꾸지 않으므로 좁힌 결과는 언제나 좁히기 전 결과의 **부분집합**이다(spec §3-1 · FR-002).

    🔴 **임계가 없다**(T018). 점수 컷·kNN 게이트·상위 N 절단을 쓰지 않는다 — 셋 다 순위를 매기는
    장치이고, 집합 판정에는 순위가 없다. 덤으로 "후보 깊이를 늘리면 정규화 모수와 게이트 배경이
    함께 움직인다"는 결합이 이 경로에서는 원천적으로 없다(T020).

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름(자산 인덱스와 다르다).
        query: 낱말들(공백 구분). 형태소 분석은 엔진이 한다.
        max_hits: 한 번에 받아올 매칭 개체 수 상한(**순위 절단이 아니라 폭주 방지선**).
            상한에 닿으면 집합이 불완전해지므로 경고 로그를 남긴다.

    Raises:
        ValueError: ``query`` 가 비었을 때. 🔴 빈 집합(=0건)과 "묻지 않았다"(=필터 없음)는 **다른
            값**이라 여기서 섞으면 안 된다 — 빈 집합을 돌려주면 교집합에서 결과가 통째로 사라지고,
            반대로 전체를 돌려주면 "검색했는데 전부 나오는" 조용한 오류가 된다.

    Returns:
        ``{(entity_type, entity_uid), …}``. 매칭이 없으면 **빈 집합**(= 0건). 같은 입력이면 같은
        집합이다(헌법 3조 — 집합이라 순서 자체가 없다).
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
    return {(etype, uid) for _doc_id, etype, uid, _score in rows if uid}


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


__all__ = ["DEFAULT_CANDIDATE_SIZE", "DEFAULT_FUSION_WEIGHTS", "entity_match_clause",
           "match_entity_keys", "search_entities_hybrid"]
