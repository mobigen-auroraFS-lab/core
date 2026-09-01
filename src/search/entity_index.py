"""092 — 개체(멀티모달 메타) **OpenSearch 색인** 문서 조립·매핑. 순수 부분은 DB·OS 불필요.

무엇을 하는 모듈인가: 개체 하나를 검색 엔진이 읽을 **문서**로 만든다. 자산이 ``opensearch_sync``
로 색인되는 것과 같은 자리이며, **인덱스는 자산과 분리**한다(문서 모양·필드·가중이 다르고, 자산
매핑을 건드리면 037 이후 안정된 경로에 위험이 간다).

**왜 옮기나**(spec 092): 개체 검색만 PG ``entity_embedding`` 을 직접 조회해 037 "OpenSearch 단일
백엔드" 원칙에서 예외로 남아 있었다. 계기는 검색 결함이다 — 짧은 한국어 단어에서 임베딩이
**뜻이 아니라 글자**를 본다(`한글` → 한라산·한강 · 첫 글자 공유 노이즈가 무작위 대비 7배).
후처리 규칙 6종이 전부 재현율을 합격선까지 깎아 실패했고, **nori 형태소 분석이 이를 구조적으로
배제한다** — `한글창제`는 ``['한글','창제']`` 로 쪼개져 걸리지만 `한라산`은 ``['한라','산']`` 이라
겹치지 않는다.

**왜 필드를 나누나**: PG 벡터는 이름·설명·키워드가 한 벡터에 섞여 있어 비중을 줄 자리가 없었다
(이름 가중을 높이려던 실험이 오히려 역효과였던 이유). 필드를 나누면 BM25 boost 로 곧장 조절된다.

🔴 **``member`` 필드가 재현율의 핵심이다.** 구성 자산의 요약·키워드를 싣지 않으면 단어 하나
재현율이 **90% → 50%** 로 무너진다(착수 전 실측). 개체 설명문 한 문장(중위 68자)에 없는 낱말이
구성 자산 요약에는 있기 때문이다 — `한글` 이 훈민정음 설명문에는 없지만 구성 자산 오디오 요약에는
"한글 창제 외에도…" 로 있다. ⚠️ **개체 생성물을 늘리는 것이 아니라**, 적재 때 이미 만들어
``asset_metadata`` 에 저장한 값을 읽어 싣는 것뿐이다(개체 재판정 0).

설계 배경: `specs/092-entity-search-opensearch`(spec §2 · plan §설계 결정)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.config.embedding_constants import FIX_EMBEDDING_DIMENSION
from src.search.opensearch_sync import build_index_body

# 색인 문서의 텍스트 필드. 순서에 뜻이 있다 — 앞이 개체의 정체이고 뒤가 보조다.
ENTITY_TEXT_FIELDS: tuple[str, ...] = ("name", "keywords", "description", "member")

# 🔴 분석기는 자산과 **같은 것**(``nori_user``)을 쓴다. 규칙이 갈리면 같은 글자가 한쪽에서만
#    걸린다(083 이 정규화 정본을 하나로 묶은 것과 같은 규율). 분석기 정의 자체는 인덱스를 만들 때
#    자산 쪽 설정을 그대로 빌려 온다(``build_entity_index_body``).
ENTITY_INDEX_MAPPING: dict[str, Any] = {
    "properties": {
        # 개체 식별자 — 검색 결과에서 개체를 되살리고, 문서만 보고도 어느 개체인지 알 수 있게.
        "entity_type": {"type": "keyword"},
        "entity_uid": {"type": "keyword"},
        "name": {"type": "text", "analyzer": "nori_user"},
        "keywords": {"type": "text", "analyzer": "nori_user"},
        "description": {"type": "text", "analyzer": "nori_user"},
        # 구성 자산 요약 + 키워드 집계. 재현율의 핵심(모듈 docstring 참조).
        "member": {"type": "text", "analyzer": "nori_user"},
        # 자산 인덱스와 같은 방식(hnsw · cosinesimil · lucene)이라 점수 환산식도 같다
        # (``fusion.knn_score_to_cosine`` 이 그 전제로 쓰인다).
        "vec": {
            "type": "knn_vector",
            "dimension": FIX_EMBEDDING_DIMENSION,
            "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
        },
    }
}


def _text(value: Any) -> str:
    """색인 필드로 쓸 문자열 하나를 만든다(문자열이 아니면 빈 값).

    Args:
        value: 원본 값. ``None``·숫자·리스트 등 무엇이 와도 죽지 않는다.

    Returns:
        문자열이면 그대로, 아니면 빈 문자열. 숫자를 ``str()`` 로 눌러 담지 않는 이유는 색인에
        ``123`` 같은 값이 형태소로 들어가면 검색에서 의미 없는 매칭을 만들기 때문이다.
    """
    return value if isinstance(value, str) else ""


def _joined(values: Any, sep: str = " ") -> str:
    """문자열 배열을 하나로 잇는다(배열이 아니면 빈 값).

    Args:
        values: 문자열 배열로 기대하는 값. 문자열 하나가 오면 **글자 단위로 순회되어** 쓰레기
            토큰이 생기므로 배열만 받는다(083 ``aggregate_tag_facets`` 와 같은 방어).
        sep: 잇는 구분자.

    Returns:
        빈 값을 제외하고 이은 문자열. 배열이 아니면 빈 문자열.
    """
    if not isinstance(values, (list, tuple)):
        return ""
    return sep.join(v for v in values if isinstance(v, str) and v)


def entity_to_doc(
    entity: Mapping[str, Any],
    *,
    vector: Sequence[float],
    member_summaries: Sequence[str] = (),
    member_keywords: Sequence[str] = (),
) -> dict[str, Any]:
    """개체 하나를 **색인 문서**로 만든다(순수 · DB·OS 호출 없음).

    Args:
        entity: 개체 행. ``entity_type``·``entity_uid``·``name``·``description``·``keywords`` 를
            읽으며, 없거나 타입이 다르면 그 필드는 빈 값으로 둔다(백엔드가 모양을 바꿔도 색인이
            죽지 않게).
        vector: 개체 임베딩(저장 차원 1536D). 호출부가 ``pad_embedding_to_storage_dim`` 을 거친
            값을 넘긴다 — 원시 bge-m3 출력은 1024D 라 그대로 넣으면 색인이 거부된다.
        member_summaries: 구성 자산 요약들(호출부가 상한을 걸어 넘긴다 · 규모 방어는 조회 쪽 몫).
        member_keywords: 구성 자산 키워드(빈도순 상위 N).

    Returns:
        색인 문서 dict. 텍스트 필드는 **항상 문자열**이라 화면·검색이 유무를 분기하지 않는다.

    ⚠️ ``member`` 는 **요약 다음 키워드** 순으로 잇는다 — 요약은 문장이라 형태소가 풍부하고
    키워드는 보조다. 순서가 BM25 점수를 바꾸지는 않지만, 사람이 문서를 열어 볼 때 읽는 순서다.
    """
    member = " ".join(x for x in (_joined(list(member_summaries)),
                                  _joined(list(member_keywords), sep=" ")) if x)
    return {
        "entity_type": _text(entity.get("entity_type")),
        "entity_uid": _text(entity.get("entity_uid")),
        "name": _text(entity.get("name")),
        "keywords": _joined(entity.get("keywords")),
        "description": _text(entity.get("description")),
        "member": member,
        "vec": list(vector),
    }


def build_entity_index_body(
    *, dim: int = FIX_EMBEDDING_DIMENSION, nori_user_words: Any = None
) -> dict[str, Any]:
    """개체 인덱스의 ``{settings, mappings}`` 를 만든다(순수 데이터 · 생성은 호출부가 한다).

    🔴 **settings 는 자산 인덱스의 것을 그대로 빌린다**(``build_index_body``). 분석기 정의를 여기서
    다시 쓰면 사전·토크나이저가 두 벌이 되어, 같은 글자가 자산 검색에서는 걸리고 개체 검색에서는
    안 걸리는 일이 생긴다. 자산 쪽 분석기가 개선되면 개체도 자동으로 따라간다.

    Args:
        dim: 벡터 차원. 기본값이 정본이며 바꾸면 기존 색인과 호환되지 않는다(재색인 필요).
        nori_user_words: 분해를 막을 외래어·고유명사 목록. ``None`` 이면 자산과 같은 기본 목록.

    Returns:
        ``{"settings": 자산과 동일, "mappings": 개체 전용}``.
    """
    return {
        "settings": build_index_body(dim=dim, nori_user_words=nori_user_words)["settings"],
        "mappings": ENTITY_INDEX_MAPPING,
    }


def ensure_entity_index(client: Any, index: str, *, recreate: bool = False) -> str:
    """개체 인덱스가 없으면 만든다. ``recreate=True`` 면 **삭제 후 재생성**(파괴적·옵트인).

    Args:
        client: OpenSearch 클라이언트.
        index: 인덱스 이름. 🔴 **자산 인덱스 이름을 넘기면 안 된다** — 매핑이 덮여 자산 검색이
            깨진다. 호출부는 ``ENTITY_INDEX_DEFAULT`` 또는 설정값을 쓴다.
        recreate: 참이면 기존 인덱스를 지우고 다시 만든다(색인된 문서가 전부 사라진다).

    Returns:
        ``'created'`` · ``'recreated'`` · ``'exists'``.
    """
    body = build_entity_index_body()
    exists = client.indices.exists(index=index)
    if exists and recreate:
        client.indices.delete(index=index)
        client.indices.create(index=index, body=body)
        return "recreated"
    if not exists:
        client.indices.create(index=index, body=body)
        return "created"
    return "exists"


def entity_doc_id(entity_type: str, entity_uid: str) -> str:
    """색인 문서 id — ``타입/표기``.

    개체의 자연키가 (타입, 표기) 둘이라 한 값으로 합친다(``김밥`` 이 음식과 작품으로 갈린 실례).
    같은 개체를 다시 색인하면 **덮어쓰기**가 되어 재실행이 멱등하다(자산이 asset_id 를 쓰는 것과
    같은 관례).

    Args:
        entity_type: 개체 타입.
        entity_uid: 표기 키(정규화된 값).

    Returns:
        ``"작품/훈민정음"`` 형태의 문서 id.
    """
    return f"{entity_type}/{entity_uid}"


def bulk_index_entities(client: Any, index: str, docs: Sequence[Mapping[str, Any]]) -> int:
    """개체 문서들을 한 번에 색인한다(멱등 — 같은 id 는 덮어쓴다).

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름.
        docs: ``entity_to_doc`` 결과들. ``entity_type``·``entity_uid`` 로 문서 id 를 만든다.

    Returns:
        색인을 시도한 문서 수(0이면 호출 자체를 하지 않는다).
    """
    if not docs:
        return 0
    body: list[Mapping[str, Any]] = []
    for doc in docs:
        body.append({"index": {"_index": index,
                               "_id": entity_doc_id(str(doc.get("entity_type", "")),
                                                    str(doc.get("entity_uid", "")))}})
        body.append(doc)
    client.bulk(body=body, refresh=True)
    return len(docs)


def delete_entity_doc(client: Any, index: str, entity_type: str, entity_uid: str) -> bool:
    """개체 문서 하나를 지운다(purge 경로용 · 없으면 조용히 넘어간다).

    개체가 재판정으로 사라질 때 색인에 남으면 **없는 개체가 검색된다**. 087 재판정에서 개체가
    925→1,065 로 바뀌며 고아 113건이 났던 전례가 있어, PG 임베딩과 같은 자리에서 함께 지운다.

    Args:
        client: OpenSearch 클라이언트.
        index: 개체 인덱스 이름.
        entity_type: 개체 타입.
        entity_uid: 표기 키.

    Returns:
        실제로 지웠으면 참, 문서가 없었으면 거짓.
    """
    doc_id = entity_doc_id(entity_type, entity_uid)
    if not client.exists(index=index, id=doc_id):
        return False
    client.delete(index=index, id=doc_id)
    return True


__all__ = [
    "ENTITY_INDEX_MAPPING",
    "ENTITY_TEXT_FIELDS",
    "build_entity_index_body",
    "bulk_index_entities",
    "delete_entity_doc",
    "ensure_entity_index",
    "entity_doc_id",
    "entity_to_doc",
]
