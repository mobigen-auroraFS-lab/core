"""개체 임베딩 — 재료 조립·해시·저장·조회 단위 테스트 (spec 090 G1 · T010).

무엇을 봉인하나: 틀리면 **조용히 비용을 태우거나 검색을 망가뜨리는** 다섯 가지를 본다.

    ① 재료 조립 — 근거 키워드가 실려야 한다(G0 에서 단어 하나 질의 35% → 50%). 순서도 뜻이
       있다(이름·타입 → 설명문 → 키워드) — 잘려도 정체가 먼저 남게.
    ② 해시 재사용 — 재료가 같으면 **임베딩을 부르지 않는다**. 이것이 없으면 배치마다
       1,000+ 회 호출이 돈다(합격선 C6: 재사용 ≥95%).
    ③ 모델 교체 — 해시가 같아도 모델이 다르면 다시 만든다. 다른 모델 벡터가 섞이면 유사도가
       뜻을 잃는다.
    ④ 패딩 — bge-m3 출력은 1024D 이고 DB 는 1536D 다. 거치지 않으면 저장이 실패한다.
    ⑤ 개체 검증 — FK 가 없으므로 앱이 막지 않으면 고아 행이 쌓인다.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.config.embedding_constants import FIX_EMBEDDING_DIMENSION
from src.mm_meta.entity_embedding import (
    MAX_MATERIAL_KEYWORDS,
    MIN_MATERIAL_CHARS,
    EntityEmbeddingError,
    build_search_material,
    count_entity_embeddings,
    find_similar_entities,
    material_hash,
    purge_orphan_embeddings,
    upsert_entity_embedding,
)


class FakeConn:
    """저장·조회를 흉내내는 최소 연결 — 실 DB 없이 계약을 시험한다.

    SQL 을 해석하지 않고 **첫 낱말과 대상 테이블**만 보고 갈래를 정한다. 실제 SQL 정확성은
    실 DB e2e 가 볼 몫이고, 여기서는 **모듈이 언제 임베딩을 부르는가**를 본다.
    """

    def __init__(self, *, entities: set[tuple[str, str]] | None = None,
                 stored: dict[tuple[str, str], tuple[str, str, str]] | None = None) -> None:
        self.entities = entities if entities is not None else {("인물", "아이유")}
        self.stored = dict(stored or {})
        self.executed: list[tuple[str, Any]] = []
        self.similar: list[tuple[str, str, float]] = []

    def execute(self, sql: str, params: Any = None) -> FakeConn:
        self.executed.append((" ".join(sql.split())[:60], params))
        self._sql, self._params = sql, params
        return self

    def fetchone(self) -> Any:
        sql = " ".join(self._sql.split())
        if "FROM node" in sql:
            return (1,) if (self._params[0], self._params[1]) in self.entities else None
        if "SELECT material_hash" in sql:
            return self.stored.get((self._params[0], self._params[1]))
        if "count(*)" in sql:
            return (len(self.stored),)
        return None

    def fetchall(self) -> Any:
        return [(t, u, s) for t, u, s in self.similar]

    @property
    def rowcount(self) -> int:
        return 3


def _fake_embed(calls: list[str], dim: int = 1024):
    """호출을 세는 임베딩 함수를 만든다.

    Args:
        calls: 호출된 재료가 쌓일 목록.
        dim: 돌려줄 벡터 차원(기본 1024 = bge-m3 raw).

    Returns:
        재료 → 벡터 함수.
    """
    def fn(material: str) -> list[float]:
        calls.append(material)
        return [0.1] * dim
    return fn


class TestBuildMaterial(unittest.TestCase):
    """① 재료 조립 — 키워드가 실리고 순서가 지켜진다."""

    def test_이름과_타입이_맨_앞(self) -> None:
        got = build_search_material(name="아이유", entity_type="인물")
        self.assertTrue(got.startswith("아이유 / 인물"))

    def test_설명문이_그다음(self) -> None:
        got = build_search_material(name="김치", entity_type="음식", description="발효 식품이다")
        self.assertEqual(got.splitlines()[1], "발효 식품이다")

    def test_근거_키워드가_실린다(self) -> None:
        # 🔴 087 판정 재료에는 없는 축 — G0 에서 단어 하나 질의를 35% → 50% 로 올렸다.
        got = build_search_material(name="김치", entity_type="음식", keywords=["한식", "발효"])
        self.assertIn("근거 키워드: 발효, 한식", got)   # 정렬됨

    def test_순서는_이름_설명_키워드(self) -> None:
        got = build_search_material(name="김치", entity_type="음식",
                                    description="설명", keywords=["한식"])
        self.assertEqual([ln.split(":")[0] for ln in got.splitlines()],
                         ["김치 / 음식", "설명", "근거 키워드"])

    def test_빈_설명과_빈_키워드는_줄을_만들지_않는다(self) -> None:
        got = build_search_material(name="아이유", entity_type="인물",
                                    description="  ", keywords=["", "  "])
        self.assertEqual(got, "아이유 / 인물")

    def test_키워드_순서가_달라도_같은_재료(self) -> None:
        # 🔴 실제로 겪은 결함(2026-08-31) — API 목록(sorted)과 배치 SQL(ARRAY_AGG)로 각각
        #    조립했더니 82건 중 3건의 해시가 갈려 재임베딩이 돌았다. 순서는 뜻이 없다.
        a = build_search_material(name="김치", entity_type="음식", keywords=["발효", "한식"])
        b = build_search_material(name="김치", entity_type="음식", keywords=["한식", "발효"])
        self.assertEqual(a, b)

    def test_키워드_중복은_한_번만(self) -> None:
        got = build_search_material(name="김치", entity_type="음식",
                                    keywords=["한식", "한식", "발효"])
        self.assertEqual(got.count("한식"), 1)

    def test_상한은_정렬_뒤에_적용된다(self) -> None:
        # 순서에 따라 살아남는 키워드가 달라지면 경로별로 재료가 갈린다.
        a = build_search_material(name="가", entity_type="장소",
                                  keywords=["c", "a", "b"], max_keywords=2)
        b = build_search_material(name="가", entity_type="장소",
                                  keywords=["b", "c", "a"], max_keywords=2)
        self.assertEqual(a, b)
        self.assertIn("a, b", a)

    def test_키워드_상한을_넘지_않는다(self) -> None:
        got = build_search_material(name="가", entity_type="장소",
                                    keywords=[f"k{i}" for i in range(50)])
        self.assertEqual(got.count("k"), MAX_MATERIAL_KEYWORDS)

    def test_이름이나_타입이_비면_예외(self) -> None:
        with self.assertRaises(EntityEmbeddingError):
            build_search_material(name="", entity_type="인물")
        with self.assertRaises(EntityEmbeddingError):
            build_search_material(name="아이유", entity_type=" ")

    def test_같은_입력이면_같은_재료(self) -> None:
        # 헌법 결정성 — 재료가 흔들리면 해시가 바뀌어 재임베딩이 돈다.
        kw = ["한식", "발효"]
        a = build_search_material(name="김치", entity_type="음식", description="설명", keywords=kw)
        b = build_search_material(name="김치", entity_type="음식", description="설명", keywords=kw)
        self.assertEqual(a, b)


class TestMaterialHash(unittest.TestCase):
    """해시 — 재사용 판단의 근거."""

    def test_길이가_64자(self) -> None:
        self.assertEqual(len(material_hash("가나다")), 64)   # DB CHAR(64) 와 같은 폭

    def test_같은_재료_같은_해시(self) -> None:
        self.assertEqual(material_hash("가나다"), material_hash("가나다"))

    def test_다른_재료_다른_해시(self) -> None:
        self.assertNotEqual(material_hash("가나다"), material_hash("가나다 "))


class TestUpsert(unittest.TestCase):
    """②③④⑤ 저장 — 언제 임베딩을 부르고 언제 부르지 않는가."""

    def test_없으면_만든다(self) -> None:
        calls: list[str] = []
        conn = FakeConn()
        got = upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                      material="아이유 / 인물\n가수다", embed_fn=_fake_embed(calls),
                                      model_name="bge-m3")
        self.assertEqual(got, "created")
        self.assertEqual(len(calls), 1)

    def test_재료가_같으면_임베딩을_부르지_않는다(self) -> None:
        # 🔴 C6 의 실질 — 이것이 없으면 배치마다 1,000+ 회 호출이 돈다.
        material = "아이유 / 인물\n가수다"
        conn = FakeConn(stored={("인물", "아이유"): (material_hash(material), "bge-m3", "")})
        calls: list[str] = []
        got = upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                      material=material, embed_fn=_fake_embed(calls),
                                      model_name="bge-m3")
        self.assertEqual(got, "reused")
        self.assertEqual(calls, [])                      # 한 번도 부르지 않았다

    def test_재료가_바뀌면_다시_만든다(self) -> None:
        conn = FakeConn(stored={("인물", "아이유"): (material_hash("옛 재료"), "bge-m3", "")})
        calls: list[str] = []
        got = upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                      material="아이유 / 인물\n새 설명이 들어왔다", embed_fn=_fake_embed(calls),
                                      model_name="bge-m3")
        self.assertEqual(got, "updated")
        self.assertEqual(len(calls), 1)

    def test_모델이_다르면_해시가_같아도_다시_만든다(self) -> None:
        # 🔴 다른 모델 벡터가 같은 공간에 섞이면 유사도가 뜻을 잃는다.
        material = "아이유 / 인물\n가수다"
        conn = FakeConn(stored={("인물", "아이유"): (material_hash(material), "old-model", "")})
        calls: list[str] = []
        got = upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                      material=material, embed_fn=_fake_embed(calls),
                                      model_name="bge-m3")
        self.assertEqual(got, "updated")
        self.assertEqual(len(calls), 1)

    def test_모델_버전만_달라도_다시_만든다(self) -> None:
        material = "아이유 / 인물\n가수다"
        conn = FakeConn(stored={("인물", "아이유"): (material_hash(material), "bge-m3", "v1")})
        calls: list[str] = []
        got = upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                      material=material, embed_fn=_fake_embed(calls),
                                      model_name="bge-m3", model_version="v2")
        self.assertEqual(got, "updated")

    def test_1024D_를_1536D_로_패딩해_넘긴다(self) -> None:
        # 🔴 bge-m3 raw 는 1024D 이고 DB 는 vector(1536) — 거치지 않으면 저장이 실패한다.
        conn = FakeConn()
        upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                material="아이유 / 인물\n가수다",
                                embed_fn=_fake_embed([], dim=1024), model_name="bge-m3")
        insert = next(p for sql, p in conn.executed if "INSERT INTO entity_embedding" in sql)
        vector = insert[2]
        self.assertEqual(len(vector), FIX_EMBEDDING_DIMENSION)
        self.assertEqual(len(vector), 1536)

    def test_없는_개체는_거부한다(self) -> None:
        # 🔴 FK 가 없으므로 여기서 막지 않으면 고아 행이 쌓인다.
        conn = FakeConn(entities=set())
        with self.assertRaises(EntityEmbeddingError) as ctx:
            upsert_entity_embedding(conn, entity_type="인물", entity_uid="없는사람",
                                    material="없는사람 / 인물\n설명", embed_fn=_fake_embed([]),
                                    model_name="bge-m3")
        self.assertIn("개체가 없다", str(ctx.exception))

    def test_재료가_너무_짧으면_거부한다(self) -> None:
        conn = FakeConn()
        with self.assertRaises(EntityEmbeddingError):
            upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                    material="가" * (MIN_MATERIAL_CHARS - 1),
                                    embed_fn=_fake_embed([]), model_name="bge-m3")

    def test_거부될_때는_임베딩을_부르지_않는다(self) -> None:
        # 검증보다 임베딩이 먼저 돌면 헛돈이 나간다.
        calls: list[str] = []
        conn = FakeConn(entities=set())
        with self.assertRaises(EntityEmbeddingError):
            upsert_entity_embedding(conn, entity_type="인물", entity_uid="없는사람",
                                    material="없는사람 / 인물\n설명",
                                    embed_fn=_fake_embed(calls), model_name="bge-m3")
        self.assertEqual(calls, [])

    def test_재료_길이를_함께_저장한다(self) -> None:
        conn = FakeConn()
        material = "아이유 / 인물\n가수다"
        upsert_entity_embedding(conn, entity_type="인물", entity_uid="아이유",
                                material=material, embed_fn=_fake_embed([]), model_name="bge-m3")
        insert = next(p for sql, p in conn.executed if "INSERT INTO entity_embedding" in sql)
        self.assertEqual(insert[-1], len(material))


class TestFindSimilar(unittest.TestCase):
    """조회 — 컷오프가 없고 상위 N 만 본다."""

    def test_top_n_0_이면_질의하지_않는다(self) -> None:
        conn = FakeConn()
        self.assertEqual(find_similar_entities(conn, query_vector=[0.1] * 1024, top_n=0), [])
        self.assertEqual(conn.executed, [])

    def test_결과를_사전으로_돌려준다(self) -> None:
        conn = FakeConn()
        conn.similar = [("인물", "아이유", 0.52), ("음식", "김치", 0.41)]
        got = find_similar_entities(conn, query_vector=[0.1] * 1024, top_n=3)
        self.assertEqual([g["entity_uid"] for g in got], ["아이유", "김치"])
        self.assertAlmostEqual(got[0]["similarity"], 0.52)

    def test_유사도_컷오프_인자가_없다(self) -> None:
        # 🔴 G0 결론 — 컷오프 0.45 는 정답 22건 중 16건을 버렸다. 인자로도 두지 않는다.
        import inspect
        params = set(inspect.signature(find_similar_entities).parameters)
        self.assertNotIn("cutoff", params)
        self.assertNotIn("min_similarity", params)
        self.assertIn("top_n", params)

    def test_질의_벡터도_패딩한다(self) -> None:
        conn = FakeConn()
        find_similar_entities(conn, query_vector=[0.1] * 1024, top_n=3)
        _, params = conn.executed[0]
        self.assertEqual(len(params[0]), FIX_EMBEDDING_DIMENSION)

    def test_모델을_지정하면_바인딩_순서가_맞는다(self) -> None:
        # SQL 텍스트상 %s 순서: SELECT 벡터 → WHERE model → ORDER BY 벡터 → LIMIT
        conn = FakeConn()
        find_similar_entities(conn, query_vector=[0.1] * 1024, top_n=5, model_name="bge-m3")
        _, params = conn.executed[0]
        self.assertEqual(len(params), 4)
        self.assertEqual(params[1], "bge-m3")
        self.assertEqual(params[3], 5)

    def test_모델을_안_주면_인자가_셋(self) -> None:
        conn = FakeConn()
        find_similar_entities(conn, query_vector=[0.1] * 1024, top_n=5)
        _, params = conn.executed[0]
        self.assertEqual(len(params), 3)
        self.assertEqual(params[2], 5)


class TestPurgeAndCount(unittest.TestCase):
    """고아 정리 — FK 가 없어 이것이 유일한 방어선이다."""

    def test_고아를_지운_수를_돌려준다(self) -> None:
        conn = FakeConn()
        self.assertEqual(purge_orphan_embeddings(conn), 3)

    def test_node_에_없는_것만_지운다(self) -> None:
        conn = FakeConn()
        purge_orphan_embeddings(conn)
        sql = conn.executed[0][0]
        self.assertIn("DELETE FROM entity_embedding", sql)
        self.assertIn("NOT EXISTS", sql)

    def test_건수를_센다(self) -> None:
        conn = FakeConn(stored={("인물", "아이유"): ("h", "m", "")})
        self.assertEqual(count_entity_embeddings(conn), 1)


if __name__ == "__main__":
    unittest.main()


class TestSearchMaterialMemberKeywords(unittest.TestCase):
    """092 — 검색 재료에 **구성 자산 키워드**를 싣는다(개체 생성물은 무변경).

    왜: 개체 설명문 한 문장(중위 68자)에 없는 낱말이 구성 자산에는 있다. 실측에서 이 축을 더하니
    다어절 재현율이 73.3% → 86.7% 로 올랐다.
    """

    def test_구성_키워드가_재료에_실린다(self) -> None:
        got = build_search_material(name="훈민정음", entity_type="작품",
                                    description="창제 원리.", keywords=["훈민정음"],
                                    member_keywords=["한글", "창제"])
        self.assertIn("구성 키워드: 창제, 한글", got)      # 정렬됨

    def test_안_주면_현행_재료_그대로다(self) -> None:
        """되돌림의 실질 — 인자를 안 넘기면 090 재료가 그대로 나온다."""
        base = build_search_material(name="훈민정음", entity_type="작품",
                                     description="창제 원리.", keywords=["훈민정음"])
        self.assertNotIn("구성 키워드", base)

    def test_순서가_달라도_같은_재료다(self) -> None:
        """🔴 해시가 흔들리면 재임베딩이 매번 돈다(090 G2 에서 실제로 겪었다)."""
        a = build_search_material(name="김치", entity_type="음식",
                                  member_keywords=["발효", "배추", "김장"])
        b = build_search_material(name="김치", entity_type="음식",
                                  member_keywords=["김장", "발효", "배추"])
        self.assertEqual(a, b)

    def test_빈_값은_버린다(self) -> None:
        got = build_search_material(name="김치", entity_type="음식",
                                    member_keywords=["", "  ", "발효"])
        self.assertIn("구성 키워드: 발효", got)
