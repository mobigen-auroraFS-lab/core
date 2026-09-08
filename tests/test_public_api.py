"""공개 API 봉인 — README 「공개 API」 표에 적힌 이름이 실제로 존재하고, 밑줄이 없고, 표와 코드가 어긋나지 않는다.

**왜 필요한가**: 코어는 라이브러리다. 파이프라인·백엔드가 import 하는 이름이 곧 계약인데, 지금까지는
"무엇이 계약이고 무엇이 내부인가"를 적어 둔 곳이 없어 백엔드가 밑줄 이름(`_REVIEW_STATUSES`)을
가져다 쓰는 일이 실제로 있었다(2026-09-02 감사 A1). 표 하나를 정본으로 두고 이 테스트가 세 가지를 잡는다.

  ① 표의 이름이 **import 된다** — 이름을 바꾸거나 지우면 소비 레포보다 여기서 먼저 깨진다.
  ② 표의 이름에 **밑줄이 없다** — 내부 이름을 계약으로 올리는 실수를 막는다.
  ③ 표(README)와 이 목록이 **같다** — 한쪽만 고치면 실패한다. 목록의 정본은 README 다.

목록을 늘리는 방법: README 표에 한 줄 추가 + 아래 ``PUBLIC_API`` 에 같은 이름 추가 + ``CHANGELOG.md``.
소비 레포 쪽 가드(백엔드 ``tests/test_core_import_guard.py``)는 밑줄 import 를 막는다 — 둘이 짝이다.
"""
from __future__ import annotations

import importlib
import re
import unittest
from pathlib import Path

# 정본은 README 표다. 여기 목록은 그 표를 코드로 옮긴 것이고, 아래 테스트 ③ 이 둘을 대조한다.
PUBLIC_API: dict[str, tuple[str, ...]] = {
    "src.config.settings": ("init_settings", "get_current_settings", "PipelineSettings", "active_embed_channel"),
    "src.config.embedding_constants": (
        "FIX_EMBEDDING_DIMENSION", "EMBEDDING_KIND_ST", "EMBEDDING_KIND_CLIP", "DEFAULT_CLIP_MODEL_NAME",
    ),
    "src.config.filename_util": ("basename_of", "strip_asset_id_prefix", "display_file_name"),
    "src.config.bootstrap": ("bootstrap_env",),
    "src.config.search_constants": ("TAG_FACET_TOP_N_DEFAULT", "TAG_FACET_MIN_COUNT_DEFAULT", "ENTITY_INDEX_DEFAULT"),
    "src.config.search_modalities": (
        "VALID_SEARCH_MODALITIES", "parse_modalities_csv", "MODALITY_TO_BUCKET", "BUCKET_TO_MODALITY",
    ),
    "src.database.postgres_util": ("PostgresUtil",),
    "src.database.ids": ("uuid7",),
    "src.database.lineage_persist": ("record_lineage",),
    "src.database.lineage_activity": ("LineageActivity",),
    "src.domain.status_vocab": (
        "AssetStatus", "AccessTier", "GraphEdgeStatus", "RelationResolutionStatus",
        "RegistryFieldStatus", "MmSkillStatus", "RelationKindStatus",
    ),
    "src.domain.text_norm": ("normalize_text_key",),
    "src.domain.numeric": ("safe_float",),
    "src.registry.access_tier": ("project_ext_meta", "principal_clearance"),
    "src.registry.ext_meta_field_registry": ("fetch_access_tiers", "validate_ext_meta"),
    "src.relations.graph_query": (
        "fetch_relations_for_asset", "fetch_active_relations_for_asset", "mm_meta_of_asset", "mm_meta_bundle", "list_entities", "count_entities_by_type", "count_entities_by_area", "assets_of_entities",
    ),
    "src.relations.approval_policy": ("TIER_ORDER", "tier_rank"),
    "src.relations.review": (
        "list_edges_for_review", "list_relation_kinds", "bulk_review", "revise_edge", "promote_relation_kind",
    ),
    "src.search.search_service": ("search_hybrid",),
    "src.search.search_filters": ("SearchFilters", "parse_search_filters"),
    "src.search.search_tuning": ("SearchTuning",),
    "src.search.refine": ("refine_rows", "refine_tokens"),
    "src.search.facets": ("aggregate_facets",),
    "src.search.file_search": (
        "search_files", "build_rank_body", "build_facet_body", "FACET_FIELDS",
        "RANK_DEPTH_DEFAULT", "TOTAL_CAP_DEFAULT", "FACET_SIZE_DEFAULT",
        "SEARCH_PIPELINE_DEFAULT", "WORD_FIELDS_DEFAULT",
    ),
    "src.search.tag_facets": ("aggregate_tag_facets", "normalize_tag_key"),
    "src.search.query_embed": ("embed_query_for_media_search",),
    "src.search.opensearch_sync": ("get_client",),
    "src.search.entity_search_os": ("search_entities_hybrid",),
    "src.mm_meta.entity_search": (
        "split_query", "match_entity_reason", "narrow_entities", "fuse_entity_results", "gate_semantic_hits",
        "entity_refine_fields", "REASON_CODE_NAME", "REASON_CODE_KEYWORD", "REASON_CODE_DESCRIPTION",
        "REASON_KEYWORD", "REASON_DESCRIPTION", "REASON_SEMANTIC", "REASON_TEXT_MATCH",
    ),
    "src.mm_meta.entity_embedding": ("find_similar_entities",),
    "src.topic.asset_topic_query": (
        "fetch_asset_topic", "find_same_topic_groups", "list_topics", "assets_in_topic", "assets_unclassified",
    ),
    "src.mm_meta.rules": ("MIN_BUNDLE_SIZE",),
    "src.mm_meta.persist": ("fetch_meta_type_vocab",),
    "src.mm_classify.persist": ("fetch_active_skills",),
    "src.mm_classify.read": ("label_names_of_assets", "fetch_active_skills"),
    "src.llm.client": ("get_llm_client", "complete_text", "complete_json", "complete_vision_json"),
}

_README = Path(__file__).resolve().parents[1] / "README.md"


def _readme_table() -> dict[str, set[str]]:
    """README 「공개 API」 절의 표를 ``{모듈: {이름…}}`` 으로 읽는다.

    Returns:
        표에 적힌 모듈별 이름 집합. 절이 없으면 빈 dict(테스트가 실패하도록).
    """
    text = _README.read_text(encoding="utf-8")
    m = re.search(r"^## 공개 API.*?(?=^## )", text, flags=re.S | re.M)
    if not m:
        return {}
    out: dict[str, set[str]] = {}
    for line in m.group(0).splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| 영역"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        module = cells[1].strip("`")
        names = {n.strip().strip("`") for n in cells[2].split("·") if n.strip()}
        out.setdefault(module, set()).update(names)
    return out


class TestPublicApi(unittest.TestCase):
    """공개 API 표 = 코드 = 이 목록."""

    def test_공개_이름이_전부_import_된다(self) -> None:
        for module, names in PUBLIC_API.items():
            mod = importlib.import_module(module)
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(mod, name), f"{module}.{name} 이 없다 — 이름이 바뀌었으면 표·CHANGELOG 도 고칠 것")

    def test_공개_이름에_밑줄이_없다(self) -> None:
        for module, names in PUBLIC_API.items():
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertFalse(name.startswith("_"), f"{module}.{name}: 밑줄 이름은 내부 구현이다")

    def test_README_표와_목록이_같다(self) -> None:
        table = _readme_table()
        self.assertTrue(table, "README 에 「## 공개 API」 절이 없다")
        listed = {m: set(n) for m, n in PUBLIC_API.items()}
        self.assertEqual(table, listed, "README 표와 tests/test_public_api.py 의 PUBLIC_API 가 다르다 — 정본은 README 표")


if __name__ == "__main__":
    unittest.main()
