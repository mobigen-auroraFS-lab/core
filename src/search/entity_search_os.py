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

설계 배경: `specs/092-entity-search-opensearch`(spec §2 · plan §설계 결정)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.config.search_constants import (
    ENTITY_BM25_FIELDS_DEFAULT,
    ENTITY_BM25_OPERATOR_DEFAULT,
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


__all__ = ["DEFAULT_CANDIDATE_SIZE", "DEFAULT_FUSION_WEIGHTS", "search_entities_hybrid"]
