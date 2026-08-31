"""개체 의미 검색용 임베딩 — 재료 조립·해시·저장·조회 (spec 090 G1).

무엇을 하는 모듈인가: 개체(멀티모달 메타) 하나마다 벡터를 만들어 두어 **글자가 겹치지 않아도**
찾게 한다. 089(질의 토큰 분리)가 다어절 질의를 0% → 43.3% 로 올렸지만 거기서 멈췄다 —
남은 실패 30건은 매칭이 아니라 **어휘 불일치**다(`발효`→김치 0건 · 개체 텍스트에 그 낱말이 없다).

🔴 **판정 재료와 검색 재료를 나눈다.** 087 ``build_entity_material`` 은 **라벨 판정**에 쓰는
재료이고, 고치면 판정 결과가 바뀌어 재판정이 필요해진다. 검색 재료는 여기서 따로 만든다 —
G0 측정에서 **근거 키워드를 넣으면 단어 하나 질의가 35% → 50%** 로 올랐으므로 넣어야 하는데,
그 변경을 판정 쪽으로 번지게 할 이유가 없다.

🔴 **``material_hash`` 가 이 모듈의 핵심이다.** 개체는 배치가 다시 만들 때마다 바뀐다
(087 재판정에서 925→1,065). 해시가 없으면 매 배치마다 1,000+ 회 임베딩 호출이 돈다 —
합격선 C6(재실행 시 재사용 ≥95%)이 그것을 잰다.

⚠️ **저장 차원은 1536 이고 모델 출력은 1024 다.** bge-m3 raw 가 1024D 이므로
``pad_embedding_to_storage_dim`` 을 거쳐 저장한다(헌법: 1536D 통일 · 자산 임베딩과 같은 함수).
0 패딩은 코사인 유사도를 바꾸지 않는다.

⚠️ **FK 가 없다** — ``node(entity_type, entity_uid)`` 의 유니크가 부분 인덱스라 PostgreSQL 이
FK 대상으로 받지 않는다(v304 와 같은 사유). **개체 존재 검증은 이 모듈이 한다.**
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from src.config.embedding_constants import FIX_EMBEDDING_DIMENSION
from src.embedders.text_embedder import pad_embedding_to_storage_dim

# 재료에 실을 근거 키워드 상한. 개체당 중위 1.8개라 넉넉하지만, 키워드가 수십 개인 개체가
# 나오면 재료가 그것으로 뒤덮여 이름·설명문이 묻힌다(순서에 뜻이 있다 — 앞이 정체).
MAX_MATERIAL_KEYWORDS = 12

# 재료가 이보다 짧으면 임베딩해도 뜻을 싣지 못한다고 보고 건너뛴다. G0 실측 중위 68자 ·
# 최소 50자였으므로 정상 개체는 걸리지 않는다 — 이름·타입만 있는 빈 개체를 걸러내는 선이다.
MIN_MATERIAL_CHARS = 12


class EntityEmbeddingError(RuntimeError):
    """개체 임베딩 처리 실패(재료 부족·개체 부재 등)."""


def build_search_material(
    *,
    name: str,
    entity_type: str,
    description: str | None = None,
    keywords: Sequence[str] = (),
    max_keywords: int = MAX_MATERIAL_KEYWORDS,
) -> str:
    """개체 하나의 **검색 재료**를 조립한다(순수 · DB·임베딩 호출 없음).

    Args:
        name: 개체 대표 표기(예: ``아이유``). 비어 있으면 예외.
        entity_type: 개체 타입(예: ``인물``). 비어 있으면 예외 — 같은 이름이라도 타입이 다르면
            다른 대상이다(``김밥`` 이 음식과 작품으로 갈린 실례).
        description: 생성된 개체 설명문. 구성 자산 전체를 종합한 문장이라 **가장 좋은 재료**다.
        keywords: 근거 키워드. 🔴 **087 판정 재료에는 없는 축**이며, G0 측정에서 이것을 넣자
            단어 하나 질의가 35% → 50% · 이름 질의가 96.7% → 100% 로 올랐다.
        max_keywords: 실을 키워드 개수 상한.

    Returns:
        검색 재료 문자열. 순서에 뜻이 있다 — **이름·타입 → 설명문 → 근거 키워드**.
        앞쪽이 대상의 정체이고 뒤쪽은 보조라서, 재료가 잘려도 정체가 먼저 남는다
        (087 판정 재료와 같은 순서 원칙).

    Raises:
        EntityEmbeddingError: 이름이나 타입이 비었을 때.
    """
    label = (name or "").strip()
    kind = (entity_type or "").strip()
    if not label or not kind:
        raise EntityEmbeddingError(f"이름과 타입이 모두 필요하다(name={name!r} type={entity_type!r})")

    parts = [f"{label} / {kind}"]
    desc = (description or "").strip()
    if desc:
        parts.append(desc)
    # 🔴 **정렬한다.** 키워드는 집합이지 순서에 뜻이 없는데, 순서가 흔들리면 재료 해시가 바뀌어
    #   재임베딩이 돈다(헌법 결정성 · 합격선 C6). 실제로 겪었다 — 같은 개체를 API 목록
    #   (`sorted`)과 배치 SQL(`ARRAY_AGG`)로 각각 조립했더니 82건 중 3건의 해시가 갈렸다
    #   (2026-08-31). 상한을 적용하기 **전에** 정렬해야 어느 경로에서 오든 같은 셋이 뽑힌다.
    picked = sorted({str(k).strip() for k in keywords if str(k).strip()})[:max_keywords]
    if picked:
        parts.append("근거 키워드: " + ", ".join(picked))
    return "\n".join(parts)


def material_hash(material: str) -> str:
    """재료 텍스트의 지문을 만든다(재사용 판단용).

    Args:
        material: ``build_search_material`` 이 만든 문자열.

    Returns:
        SHA-256 16진 문자열 64자. DB ``material_hash CHAR(64)`` 와 폭이 같다.

    해시를 쓰는 이유: 재료 원문을 저장해 비교하면 컬럼이 커지고(설명문 수백 자) 비교도 느리다.
    지문이 같으면 재료가 같다고 보고 임베딩을 건너뛴다.
    """
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _entity_exists(conn: Any, *, entity_type: str, entity_uid: str) -> bool:
    """개체가 ``node`` 에 실제로 있는지 본다(FK 대신 앱이 하는 검증).

    Args:
        conn: DB 연결.
        entity_type: 개체 타입.
        entity_uid: 개체 표기 키.

    Returns:
        있으면 True.
    """
    row = conn.execute(
        "SELECT 1 FROM node WHERE node_kind='entity' AND entity_type=%s AND entity_uid=%s LIMIT 1",
        (entity_type, entity_uid),
    ).fetchone()
    return row is not None


def upsert_entity_embedding(
    conn: Any,
    *,
    entity_type: str,
    entity_uid: str,
    material: str,
    embed_fn: Callable[[str], list[float]],
    model_name: str,
    model_version: str | None = None,
) -> str:
    """개체 임베딩을 저장한다 — **재료가 그대로면 임베딩을 부르지 않는다**.

    Args:
        conn: DB 연결(트랜잭션은 호출한 쪽이 관리한다).
        entity_type: 개체 타입.
        entity_uid: 개체 표기 키.
        material: ``build_search_material`` 결과.
        embed_fn: 재료 문자열 → 벡터. **주입받는다** — 테스트가 실제 임베딩 서버 없이 돌고,
            모델 교체가 이 모듈을 고치지 않게 하기 위해서다.
        model_name: 쓴 모델 이름(재생성 판단에 쓴다).
        model_version: 모델 버전(선택).

    Returns:
        ``"reused"``(재료·모델이 같아 건너뜀) · ``"created"`` · ``"updated"``.

    Raises:
        EntityEmbeddingError: 개체가 ``node`` 에 없거나 재료가 너무 짧을 때.

    🔴 **모델이 바뀌면 해시가 같아도 다시 만든다.** 다른 모델의 벡터가 같은 공간에 섞이면
    유사도가 뜻을 잃는다.
    """
    if len(material.strip()) < MIN_MATERIAL_CHARS:
        raise EntityEmbeddingError(
            f"재료가 너무 짧다({len(material.strip())}자 < {MIN_MATERIAL_CHARS}): "
            f"{entity_type}/{entity_uid}"
        )
    if not _entity_exists(conn, entity_type=entity_type, entity_uid=entity_uid):
        # FK 가 없으므로 여기서 막지 않으면 고아 행이 쌓인다.
        raise EntityEmbeddingError(f"개체가 없다: {entity_type}/{entity_uid}")

    digest = material_hash(material)
    row = conn.execute(
        "SELECT material_hash, model_name, COALESCE(model_version,'') FROM entity_embedding"
        " WHERE entity_type=%s AND entity_uid=%s",
        (entity_type, entity_uid),
    ).fetchone()
    if row is not None and row[0] == digest and row[1] == model_name \
            and row[2] == (model_version or ""):
        return "reused"

    vector = pad_embedding_to_storage_dim(embed_fn(material))
    if len(vector) != FIX_EMBEDDING_DIMENSION:
        raise EntityEmbeddingError(
            f"패딩 후 차원이 어긋난다({len(vector)} != {FIX_EMBEDDING_DIMENSION})"
        )
    conn.execute(
        f"""
        INSERT INTO entity_embedding
            (entity_type, entity_uid, embedding, model_name, model_version,
             material_hash, material_chars)
        VALUES (%s, %s, %s::vector({FIX_EMBEDDING_DIMENSION}), %s, %s, %s, %s)
        ON CONFLICT (entity_type, entity_uid) DO UPDATE SET
            embedding      = EXCLUDED.embedding,
            model_name     = EXCLUDED.model_name,
            model_version  = EXCLUDED.model_version,
            material_hash  = EXCLUDED.material_hash,
            material_chars = EXCLUDED.material_chars,
            updated_at     = now()
        """,
        (entity_type, entity_uid, vector, model_name, model_version,
         digest, len(material)),
    )
    return "created" if row is None else "updated"


def find_similar_entities(
    conn: Any,
    *,
    query_vector: list[float],
    top_n: int,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """질의 벡터에 가까운 개체를 상위 N 개 돌려준다.

    Args:
        conn: DB 연결.
        query_vector: 질의 임베딩(패딩 전이어도 된다 — 이 함수가 맞춘다).
        top_n: 몇 개까지. 🔴 **유사도 컷오프를 두지 않는다**(spec 090 §2) — G0 에서 컷오프
            0.45 가 정답 22건 중 16건을 버렸다. 재료가 짧아 절대 유사도가 전반적으로 낮고,
            1위로 정확히 맞춘 것도 0.33~0.44 였다(`보양식`→삼계탕 1위 0.359).
            **순위는 믿을 수 있고 절대값은 못 믿는다.**
        model_name: 이 모델로 만든 벡터만 본다(선택). 모델이 섞이면 유사도가 뜻을 잃는다.

    Returns:
        ``{entity_type, entity_uid, similarity}`` 목록. 유사도 내림차순 → 표기 키 오름차순
        (같은 유사도에서 순서가 흔들리지 않게 · 헌법 결정성).
    """
    if top_n <= 0:
        return []
    vector = pad_embedding_to_storage_dim(query_vector)
    # 벡터 노름 0(패딩만 된 빈 벡터)은 코사인이 정의되지 않아 제외한다 — topic_registry 조회와 같은 관례.
    where = "WHERE vector_norm(embedding) > 0"
    if model_name:
        where += " AND model_name = %s"
    sql = f"""
        SELECT entity_type, entity_uid,
               1 - (embedding <=> %s::vector({FIX_EMBEDDING_DIMENSION})) AS similarity
          FROM entity_embedding
          {where}
         ORDER BY embedding <=> %s::vector({FIX_EMBEDDING_DIMENSION}), entity_uid ASC
         LIMIT %s
    """
    # 바인딩 순서 = SQL 텍스트상 %s 등장 순서 그대로:
    #   ①SELECT 의 벡터 → ②[WHERE model_name] → ③ORDER BY 의 벡터 → ④LIMIT
    params: tuple = ((vector, model_name, vector, top_n) if model_name
                     else (vector, vector, top_n))
    rows = conn.execute(sql, params).fetchall()
    return [{"entity_type": r[0], "entity_uid": str(r[1]), "similarity": float(r[2])}
            for r in rows]


def purge_orphan_embeddings(conn: Any) -> int:
    """``node`` 에 없는 개체의 임베딩을 지운다.

    Args:
        conn: DB 연결.

    Returns:
        지운 행 수.

    🔴 **왜 필요한가.** FK 가 없어 개체가 사라져도 임베딩이 남는다. 개체는 배치가 다시 만들
    때마다 바뀌며(087 재판정에서 925→1,065) 실제로 **고아 113건**이 났던 전례가 있다.
    개체 정리 경로에서 이 함수를 함께 불러야 한다.
    """
    cur = conn.execute(
        """
        DELETE FROM entity_embedding ee
         WHERE NOT EXISTS (
               SELECT 1 FROM node n
                WHERE n.node_kind='entity'
                  AND n.entity_type = ee.entity_type
                  AND n.entity_uid  = ee.entity_uid)
        """
    )
    return int(getattr(cur, "rowcount", 0) or 0)


def count_entity_embeddings(conn: Any, *, model_name: str | None = None) -> int:
    """저장된 개체 임베딩 수를 센다(배치 계측·운영 점검용).

    Args:
        conn: DB 연결.
        model_name: 이 모델로 만든 것만 셀 때 지정.

    Returns:
        행 수.
    """
    if model_name:
        row = conn.execute(
            "SELECT count(*) FROM entity_embedding WHERE model_name=%s", (model_name,)
        ).fetchone()
    else:
        row = conn.execute("SELECT count(*) FROM entity_embedding").fetchone()
    return int(row[0]) if row else 0

# 대상 조회 SQL — 087 ``_TARGET_SQL`` 과 같은 뼈대에 **근거 키워드**를 더한 것이다.
# 🔴 087 함수를 확장하지 않고 따로 두는 이유: 그쪽은 **판정** 대상 조회이고 반환 계약이 판정
#   경로에 묶여 있다. 검색은 키워드가 필요하고(G0 에서 단어 하나 질의 35→50%) 판정은 쓰지
#   않으므로, 같은 함수에 넣으면 쓰지 않는 값을 판정 쪽이 늘 실어 나른다.
# 키워드는 소속 엣지의 ``reason`` 에 ``kw=…`` 형태로 들어 있다(화면 목록과 같은 추출 규칙).
_EMBED_TARGET_SQL = """
SELECT n.entity_type, n.entity_uid,
       COALESCE(n.canonical->>'name', n.entity_uid) AS name,
       n.canonical->>'description'                  AS description,
       COUNT(DISTINCT ge.src_node)                   AS members,
       ARRAY_AGG(DISTINCT substring(ge.reason from 'kw=([^|]*)'))
           FILTER (WHERE ge.reason IS NOT NULL)      AS keywords
  FROM node n
  JOIN graph_edge ge    ON ge.dst_node = n.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
 WHERE n.node_kind = 'entity'
   AND rk.kind_code = 'mm_member'
   AND ge.status = ANY(%(statuses)s)
 GROUP BY n.entity_type, n.entity_uid, n.canonical
HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
 ORDER BY COUNT(DISTINCT ge.src_node) DESC, n.entity_uid
"""


def fetch_embedding_targets(
    conn: Any,
    *,
    min_members: int,
    statuses: Sequence[str],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """임베딩할 개체 목록을 읽는다(조회 전용 · 결정적 정렬).

    Args:
        conn: DB 커넥션.
        min_members: 최소 구성 자산 수(화면 노출 임계와 같은 값을 넘긴다).
        statuses: 셈에 넣을 엣지 상태 목록(화면과 같은 기준이어야 건수가 맞는다).
        limit: 한 번에 가져올 상한. ``None`` 이면 전량.

    Returns:
        ``[{entity_type, entity_uid, name, description, members, keywords}]``
        — 구성 자산 수 내림차순 → 표기 키(결정적 정렬).

    🔴 **노출 임계를 통과한 개체만** 대상이다. 화면에 뜨지 않는 1건짜리 개체를 임베딩해도
    검색 결과로 나가지 않는다(087 판정 대상 선별과 같은 판단 · 개체 1,065개 중 82개).
    """
    sql = _EMBED_TARGET_SQL
    params: dict[str, Any] = {"minsize": min_members, "statuses": list(statuses)}
    if limit is not None:
        sql = sql + "LIMIT %(limit)s\n"
        params["limit"] = limit
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [
            {
                "entity_type": str(r[0]),
                "entity_uid": str(r[1]),
                "name": str(r[2]),
                "description": (r[3] or None),
                "members": int(r[4]),
                "keywords": [k for k in (r[5] or []) if k],
            }
            for r in cur.fetchall()
        ]
