"""093 1단계 — 어휘·상수 정본 5종이 **종전 사본과 값·순서·동작이 같다**. DB 없음.

무엇을 봉인하나: 세 레포에 흩어져 있던 리터럴·사본을 코어 한 곳으로 모으면서 "모으는 과정에서 값이
바뀌지 않았다"를 여기서 못 박는다. 사본이 있던 자리(파이프 계보 리터럴 · 백엔드 정렬표·버킷표·정규식·
정화 함수)는 각 레포의 parity 테스트가 자기 몫을 본다.
"""
from __future__ import annotations

import json
import math
import unittest

from src.config.search_modalities import BUCKET_TO_MODALITY, MODALITY_TO_BUCKET
from src.database.lineage_activity import LineageActivity
from src.domain.numeric import safe_float
from src.domain.status_vocab import RelationKindStatus
from src.relations import approval_policy, graph_query
from src.relations.approval_policy import TIER_ORDER, tier_rank
from src.search import fusion, search_service


class TestLineageActivity(unittest.TestCase):
    """계보 활동명 — 3레포 리터럴이 정확히 이 값이었다."""

    _LEGACY = {
        "INGEST_RECEIVED": "ingest.received.v1", "INGEST_ROUTING": "ingest.routing.v1",
        "INGEST_CLASSIFYING": "ingest.classifying.v1", "INGEST_CLASSIFIED": "ingest.classified.v1",
        "INGEST_DEFERRED": "ingest.deferred.v1", "INGEST_EXTRACTING": "ingest.extracting.v1",
        "INGEST_REGISTERED": "ingest.registered.v1", "INGEST_FAILED": "ingest.failed.v1",
        "INGEST_RESET": "ingest.reset.v1", "RELATIONS_PROPOSED": "relations.proposed.v1",
        "ENTITY_JUDGED": "entity.judged.v1",
    }

    def test_값이_리터럴과_같다(self) -> None:
        self.assertEqual({m.name: m.value for m in LineageActivity}, self._LEGACY)

    def test_문자열처럼_동작한다(self) -> None:
        m = LineageActivity.RELATIONS_PROPOSED
        self.assertIsInstance(m, str)
        self.assertEqual(f"{m}", "relations.proposed.v1")
        self.assertEqual(json.dumps(m), '"relations.proposed.v1"')

    def test_규약_대상_사건_판(self) -> None:
        for m in LineageActivity:
            with self.subTest(m=m.name):
                parts = m.value.split(".")
                self.assertEqual(len(parts), 3)
                self.assertRegex(parts[2], r"^v\d+$")

    def test_mm_meta_persist_가_같은_정본을_쓴다(self) -> None:
        from src.mm_meta.persist import LINEAGE_ACTIVITY
        self.assertIs(LINEAGE_ACTIVITY, LineageActivity.ENTITY_JUDGED)
        self.assertEqual(LINEAGE_ACTIVITY, "entity.judged.v1")


class TestRelationKindStatus(unittest.TestCase):
    def test_값과_순서(self) -> None:
        self.assertEqual([s.value for s in RelationKindStatus], ["active", "inactive"])

    def test_기본_등록_상태는_inactive(self) -> None:
        import inspect

        from src.relations.relation_type_catalog import ensure_relation_kind_for_llm_proposal
        default = inspect.signature(ensure_relation_kind_for_llm_proposal).parameters["status"].default
        self.assertEqual(default, "inactive")
        self.assertIs(default, RelationKindStatus.INACTIVE)


class TestModalityBuckets(unittest.TestCase):
    _LEGACY = {"text": "text_documents", "audio": "audio", "image": "image", "video": "video"}

    def test_표와_순서가_전과_같다(self) -> None:
        self.assertEqual(list(MODALITY_TO_BUCKET.items()), list(self._LEGACY.items()))

    def test_역표가_정확히_뒤집힌다(self) -> None:
        self.assertEqual(BUCKET_TO_MODALITY, {v: k for k, v in self._LEGACY.items()})
        self.assertEqual({BUCKET_TO_MODALITY[b] for b in MODALITY_TO_BUCKET.values()}, set(MODALITY_TO_BUCKET))

    def test_검색_서비스가_같은_객체를_쓴다(self) -> None:
        self.assertIs(search_service._MODALITY_BUCKETS, MODALITY_TO_BUCKET)


class TestTierRank(unittest.TestCase):
    """종전 사본 ``{"strong": 0, "weak": 1}.get(t, 2)`` 와 같은 값."""

    def test_순서(self) -> None:
        self.assertEqual(TIER_ORDER, ("strong", "weak"))
        for t, want in (("strong", 0), ("weak", 1), ("", 2), (None, 2), ("None", 2), ("x", 2)):
            with self.subTest(t=t):
                self.assertEqual(tier_rank(t), want)

    def test_graph_query_가_사본_없이_정본을_쓴다(self) -> None:
        self.assertFalse(hasattr(graph_query, "_TIER_RANK"))
        self.assertIs(graph_query.tier_rank, approval_policy.tier_rank)


class TestSafeFloat(unittest.TestCase):
    def test_fusion_이_같은_함수를_쓴다(self) -> None:
        self.assertIs(fusion._safe_float, safe_float)

    def test_종전_로직과_같다(self) -> None:
        def legacy(value, default=0.0):
            try:
                x = float(value)
            except (TypeError, ValueError):
                return default
            return x if math.isfinite(x) else default

        for v in (0.5, "0.25", None, "x", float("nan"), float("inf"), -float("inf"), -1, 1e308, [], True, "1e3"):
            for d in (0.0, -1.0):
                with self.subTest(v=v, d=d):
                    self.assertEqual(safe_float(v, d), legacy(v, d))


if __name__ == "__main__":
    unittest.main()
