"""092 T004 — 개체 OpenSearch 색인 문서 조립(``src/search/entity_index.py``) 단위 테스트.

무엇을 검증하나: 개체 하나를 **검색 문서**로 만드는 규칙이다. OS·DB 가 필요 없는 순수 함수라
mock 없이 시험한다.

왜 필드를 나누나(spec 092 §2-1): PG 벡터는 이름·설명·키워드가 **한 벡터에 섞여** 있어 비중을 줄
자리가 없었다(가중 실험이 역효과였던 이유). 필드를 나누면 BM25 boost 로 "이름을 더 무겁게"가
곧장 된다.

🔴 **``member`` 필드가 재현율의 핵심이다**(착수 전 실측): 구성 자산 요약·키워드를 빼면
단어 하나 재현율이 **90% → 50%** 로 무너진다. 개체 설명문 한 문장(68자)에는 없는 낱말이 구성
자산 요약에는 있기 때문이다 — `한글` 이 훈민정음 설명문에는 없지만 구성 자산 오디오 요약에는
"**한글 창제** 외에도…" 로 있다.

⚠️ 개체 생성물을 늘리는 것이 아니다. 적재 때 이미 만들어 저장한 값을 색인 문서에 싣는 것뿐이다.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.config import search_constants
from src.search.entity_index import ENTITY_INDEX_MAPPING, entity_to_doc


def _entity(**over: Any) -> dict[str, Any]:
    base = {"entity_type": "작품", "entity_uid": "훈민정음", "name": "훈민정음",
            "description": "훈민정음의 창제 원리와 세종대왕의 업적.",
            "keywords": ["한글창제", "훈민정음"]}
    base.update(over)
    return base


class TestEntityIndexConstants(unittest.TestCase):
    """상수 — 자산 인덱스를 건드리지 않는다는 것이 가장 중요하다."""

    def test_개체_인덱스는_자산과_다른_이름이다(self) -> None:
        # 🔴 같은 인덱스에 섞으면 매핑이 충돌하고 037 이후 안정된 자산 검색에 위험이 간다.
        self.assertNotEqual(search_constants.ENTITY_INDEX_DEFAULT, "assets")
        self.assertTrue(search_constants.ENTITY_INDEX_DEFAULT)

    def test_operator_기본은_or_다(self) -> None:
        """🔴 자산은 and 다. 개체는 텍스트가 짧아 and 가 가혹하다(실측 C2 73.3% vs or 86.7%)."""
        self.assertEqual(search_constants.ENTITY_BM25_OPERATOR_DEFAULT, "or")
        self.assertEqual(search_constants.OS_BM25_OPERATOR_DEFAULT, "and")   # 자산은 그대로

    def test_필드_가중은_이름을_가장_무겁게(self) -> None:
        fields = search_constants.ENTITY_BM25_FIELDS_DEFAULT
        self.assertEqual(fields[0], "name^3")
        self.assertIn("keywords^2", fields)
        self.assertIn("member", fields)      # 재현율의 핵심 — 빠지면 C1 이 반토막 난다


class TestEntityIndexMapping(unittest.TestCase):
    """매핑 — 분석기는 자산과 같은 것을 쓰고, 벡터는 정본 차원을 지킨다."""

    def test_텍스트_필드는_nori_분석기를_쓴다(self) -> None:
        # 분석 규칙이 갈리면 같은 글자가 한쪽에서만 걸린다.
        props = ENTITY_INDEX_MAPPING["properties"]
        for field in ("name", "keywords", "description", "member"):
            with self.subTest(field=field):
                self.assertEqual(props[field]["analyzer"], "nori_user")

    def test_벡터는_1536D_코사인이다(self) -> None:
        vec = ENTITY_INDEX_MAPPING["properties"]["vec"]
        self.assertEqual(vec["type"], "knn_vector")
        self.assertEqual(vec["dimension"], 1536)          # 헌법: 1536D 통일
        self.assertEqual(vec["method"]["space_type"], "cosinesimil")


class TestEntityToDoc(unittest.TestCase):
    """색인 문서 조립 — 순수 함수."""

    _VEC = [0.1] * 1536

    def test_네_필드와_벡터를_싣는다(self) -> None:
        doc = entity_to_doc(_entity(), vector=self._VEC,
                            member_summaries=["요약1", "요약2"], member_keywords=["창제", "문자"])
        self.assertEqual(doc["name"], "훈민정음")
        self.assertEqual(doc["keywords"], "한글창제 훈민정음")
        self.assertIn("창제 원리", doc["description"])
        self.assertIn("요약1", doc["member"])
        self.assertIn("창제", doc["member"])
        self.assertEqual(doc["vec"], self._VEC)

    def test_개체_식별자도_싣는다(self) -> None:
        # 검색 결과에서 개체를 되살리려면 타입·표기가 문서에 있어야 한다(목록이 정본이지만
        # 문서만 보고도 어느 개체인지 알 수 있어야 디버깅이 된다).
        doc = entity_to_doc(_entity(), vector=self._VEC)
        self.assertEqual(doc["entity_type"], "작품")
        self.assertEqual(doc["entity_uid"], "훈민정음")

    def test_구성_자산이_없어도_문서가_만들어진다(self) -> None:
        doc = entity_to_doc(_entity(), vector=self._VEC)
        self.assertEqual(doc["member"], "")

    def test_결측_타입이상에도_문자열_필드를_준다(self) -> None:
        for over in ({"description": None}, {"keywords": None}, {"keywords": "문자열이_왔다"},
                     {"description": 123}):
            with self.subTest(over=over):
                doc = entity_to_doc(_entity(**over), vector=self._VEC)
                for f in ("name", "keywords", "description", "member"):
                    self.assertIsInstance(doc[f], str)

    def test_같은_입력이면_같은_문서(self) -> None:
        """결정성(E5) — 색인이 흔들리면 재측정이 성립하지 않는다."""
        args = {"vector": self._VEC, "member_summaries": ["a", "b"],
                "member_keywords": ["x", "y"]}
        first = entity_to_doc(_entity(), **args)
        for _ in range(4):
            self.assertEqual(entity_to_doc(_entity(), **args), first)

    def test_입력을_고치지_않는다(self) -> None:
        ent = _entity()
        before = dict(ent)
        entity_to_doc(ent, vector=self._VEC, member_summaries=["a"])
        self.assertEqual(ent, before)

    def test_member_는_요약_다음_키워드_순이다(self) -> None:
        # 순서에 뜻이 있다 — 요약이 문장이라 형태소가 풍부하고, 키워드는 보조다.
        doc = entity_to_doc(_entity(), vector=self._VEC,
                            member_summaries=["가나다"], member_keywords=["라마바"])
        self.assertLess(doc["member"].index("가나다"), doc["member"].index("라마바"))


class _FakeIndices:
    def __init__(self, exists: bool) -> None:
        self._exists, self.created, self.deleted = exists, [], []

    def exists(self, index: str) -> bool:  # noqa: D102
        return self._exists

    def create(self, index: str, body: Any) -> None:  # noqa: D102
        self.created.append((index, body))

    def delete(self, index: str) -> None:  # noqa: D102
        self.deleted.append(index)


class _FakeClient:
    def __init__(self, exists: bool = False, doc_exists: bool = True) -> None:
        self.indices = _FakeIndices(exists)
        self.bulk_calls: list[Any] = []
        self.deleted_docs: list[Any] = []
        self._doc_exists = doc_exists

    def bulk(self, body: Any, refresh: bool = False) -> None:  # noqa: D102, FBT001, FBT002
        self.bulk_calls.append(body)

    def exists(self, index: str, id: str) -> bool:  # noqa: A002, D102
        return self._doc_exists

    def delete(self, index: str, id: str) -> None:  # noqa: A002, D102
        self.deleted_docs.append(id)


class TestEntityIndexBody(unittest.TestCase):
    """🔴 분석기 정의를 두 벌로 만들지 않는다 — 자산 것을 그대로 빌린다."""

    def test_settings_는_자산_인덱스와_같다(self) -> None:
        from src.search.entity_index import build_entity_index_body
        from src.search.opensearch_sync import build_index_body
        # 값이 갈리면 같은 글자가 자산에서는 걸리고 개체에서는 안 걸린다.
        self.assertEqual(build_entity_index_body()["settings"], build_index_body()["settings"])

    def test_mappings_는_개체_것이다(self) -> None:
        from src.search.entity_index import build_entity_index_body
        self.assertEqual(build_entity_index_body()["mappings"], ENTITY_INDEX_MAPPING)


class TestEnsureEntityIndex(unittest.TestCase):
    def test_없으면_만든다(self) -> None:
        from src.search.entity_index import ensure_entity_index
        c = _FakeClient(exists=False)
        self.assertEqual(ensure_entity_index(c, "mm_entities"), "created")
        self.assertEqual(c.indices.created[0][0], "mm_entities")

    def test_있으면_그대로_둔다(self) -> None:
        """🔴 기본이 비파괴여야 한다 — 배치가 돌 때마다 색인이 날아가면 안 된다."""
        from src.search.entity_index import ensure_entity_index
        c = _FakeClient(exists=True)
        self.assertEqual(ensure_entity_index(c, "mm_entities"), "exists")
        self.assertEqual(c.indices.deleted, [])

    def test_recreate_는_명시할_때만_지운다(self) -> None:
        from src.search.entity_index import ensure_entity_index
        c = _FakeClient(exists=True)
        self.assertEqual(ensure_entity_index(c, "mm_entities", recreate=True), "recreated")
        self.assertEqual(c.indices.deleted, ["mm_entities"])


class TestBulkIndexEntities(unittest.TestCase):
    def test_문서_id_는_타입과_표기를_합친다(self) -> None:
        """같은 표기라도 타입이 다르면 다른 개체다(`김밥` 이 음식과 작품으로 갈린 실례)."""
        from src.search.entity_index import entity_doc_id
        self.assertEqual(entity_doc_id("작품", "훈민정음"), "작품/훈민정음")
        self.assertNotEqual(entity_doc_id("음식", "김밥"), entity_doc_id("작품", "김밥"))

    def test_같은_개체를_다시_색인하면_덮어쓴다(self) -> None:
        # bulk 액션이 index(덮어쓰기)여야 재실행이 멱등하다(create 면 충돌한다).
        from src.search.entity_index import bulk_index_entities
        c = _FakeClient()
        doc = entity_to_doc(_entity(), vector=[0.1] * 1536)
        bulk_index_entities(c, "mm_entities", [doc])
        action = c.bulk_calls[0][0]
        self.assertIn("index", action)
        self.assertEqual(action["index"]["_id"], "작품/훈민정음")

    def test_빈_목록이면_부르지_않는다(self) -> None:
        from src.search.entity_index import bulk_index_entities
        c = _FakeClient()
        self.assertEqual(bulk_index_entities(c, "mm_entities", []), 0)
        self.assertEqual(c.bulk_calls, [])


class TestDeleteEntityDoc(unittest.TestCase):
    """개체가 사라지면 색인에서도 지운다 — 없는 개체가 검색되면 안 된다(087 고아 113건 전례)."""

    def test_있으면_지운다(self) -> None:
        from src.search.entity_index import delete_entity_doc
        c = _FakeClient(doc_exists=True)
        self.assertTrue(delete_entity_doc(c, "mm_entities", "작품", "훈민정음"))
        self.assertEqual(c.deleted_docs, ["작품/훈민정음"])

    def test_없으면_조용히_넘어간다(self) -> None:
        from src.search.entity_index import delete_entity_doc
        c = _FakeClient(doc_exists=False)
        self.assertFalse(delete_entity_doc(c, "mm_entities", "작품", "없는것"))
        self.assertEqual(c.deleted_docs, [])
