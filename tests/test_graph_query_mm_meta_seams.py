"""095 FR-1 — 개체 화면 seam 4종(``list_entities``·``count_entities_by_type``·``count_entities_by_area``·
``assets_of_entities``)과 ``mm_meta_bundle`` 의 ``description`` — mock conn 단위 테스트(DB 불필요).

무엇을 봉인하나: ① SQL 이 노출 개체의 정의(entity 노드 · 소속 kind · 상태 바인딩 · DISTINCT src 계수 ·
임계 바인딩)를 전부 갖는다 ② 상태·kind·임계·갈래는 **바인딩**이고 리터럴이 아니다 ③ 반환 모양(문자열 id ·
배열은 빈 값 제거·가나다 순 · 0건 라벨 포함)이 정해진 대로다. 실 DB 대조(데모 SQL 과 같은 결과)는
``test_mm_meta_seams_e2e``(RUN_DB_E2E) 가 한다.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq
from src.relations.schema import MM_MEMBER_KIND_CODE


def _conn_returning(rows: list[dict]):
    """``conn.cursor(row_factory=dict_row)`` 컨텍스트매니저를 흉내내는 mock conn."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql_and_params(cur) -> tuple[str, dict]:
    sql, params = cur.execute.call_args.args
    return " ".join(sql.split()), params


class TestListEntities(unittest.TestCase):
    def test_sql_has_exposure_definition_and_bound_filters(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, entity_type="장소", area_names=["여행·명소", "여행·명소"],
                         min_bundle_size=3, limit=200, form_skill_codes=["content_form"])
        sql, p = _sql_and_params(cur)
        self.assertIn("n.node_kind = 'entity'", sql)
        self.assertIn("rk.kind_code = %(kind)s", sql)
        self.assertIn("ge.status = ANY(%(statuses)s)", sql)
        self.assertIn("HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s", sql)
        self.assertIn("ORDER BY confirmed_count DESC, n.entity_uid ASC LIMIT %(limit)s", sql)
        self.assertIn("el.label_code <> 'unassigned'", sql)  # 갈래 집계에서 미부여 제외
        self.assertNotIn("'active'", sql)  # 상태는 리터럴이 아니라 바인딩
        self.assertEqual(p["kind"], MM_MEMBER_KIND_CODE)
        self.assertEqual(p["statuses"], ["active", "proposed"])
        self.assertEqual(p["etype"], "장소")
        self.assertEqual((p["minsize"], p["limit"]), (3, 200))
        self.assertEqual(p["form_skills"], ["content_form"])
        self.assertEqual(p["areas"], ["여행·명소", "여행·명소"])
        self.assertEqual(p["area_n"], 1)  # AND 판정은 **고유** 이름 수

    def test_no_filters_bind_none_and_empty_forms(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=2, limit=10)
        _sql, p = _sql_and_params(cur)
        self.assertIsNone(p["etype"])
        self.assertIsNone(p["areas"])
        self.assertEqual(p["area_n"], 0)
        self.assertEqual(p["form_skills"], [])

    def test_row_shaping_is_deterministic(self) -> None:
        conn, _cur = _conn_returning([{
            "entity_type": "장소", "entity_uid": "제주도", "node_id": 7, "name": "제주도", "source": "auto",
            "description": "", "confirmed_count": 14, "total_count": 14,
            "modalities": ["video", None, "text"], "keywords": ["제주 해녀", "제주도", ""],
            "topics": ["여행", "자연"], "forms": None, "areas": ["여행·명소", None],
        }])
        [row] = gq.list_entities(conn, min_bundle_size=3, limit=1)
        self.assertEqual(row["node_id"], "7")
        self.assertIsNone(row["description"])  # 빈 문자열은 None
        self.assertEqual(row["modalities"], ["text", "video"])
        self.assertEqual(row["keywords"], ["제주 해녀", "제주도"])  # 원문 전부 · 가나다 순(절단은 호출자)
        self.assertEqual(row["topics"], ["여행", "자연"])
        self.assertEqual(row["forms"], [])
        self.assertEqual(row["areas"], ["여행·명소"])

    def test_statuses_override_is_bound_as_given(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=5, statuses=["active"])
        _sql, p = _sql_and_params(cur)
        self.assertEqual(p["statuses"], ["active"])


class TestCounts(unittest.TestCase):
    def test_count_by_type_has_no_type_or_area_condition(self) -> None:
        # 종류 칩은 "갈아타는 축" — 아무 조건도 적용하지 않는다(spec 087 2차 정정).
        conn, cur = _conn_returning([{"entity_type": "인물", "n": 3}, {"entity_type": "장소", "n": 40}])
        out = gq.count_entities_by_type(conn, min_bundle_size=3)
        sql, p = _sql_and_params(cur)
        self.assertNotIn("%(etype)s", sql)
        self.assertNotIn("%(areas)s", sql)
        self.assertIn("HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s", sql)
        self.assertEqual(p["minsize"], 3)
        self.assertEqual(out, {"인물": 3, "장소": 40})

    def test_count_by_area_applies_type_and_picked_areas_and_keeps_zero(self) -> None:
        conn, cur = _conn_returning([
            {"name": "영화", "skill": "자료 성격", "skill_code": "content_form", "n": 5},
            {"name": "음료", "skill": "음식", "skill_code": "food_content", "n": 0},
        ])
        out = gq.count_entities_by_area(conn, entity_type="음식", area_names=["한식"], min_bundle_size=3)
        sql, p = _sql_and_params(cur)
        self.assertIn("%(etype)s", sql)
        self.assertIn("%(areas)s", sql)
        self.assertIn("s.skill_code <> ALL(%(reserved)s)", sql)  # 타입 어휘 저장용 예약 스킬 제외
        self.assertIn("lab.code <> 'unassigned'", sql)
        self.assertIn("ORDER BY n DESC, lab.skill_code, lab.ord", sql)
        self.assertEqual(p["etype"], "음식")
        self.assertEqual((p["areas"], p["area_n"]), (["한식"], 1))
        self.assertEqual(out[1], {"name": "음료", "skill": "음식", "skill_code": "food_content", "count": 0})


class TestAssetsOfEntities(unittest.TestCase):
    def test_sql_and_shaping(self) -> None:
        conn, cur = _conn_returning([
            {"asset_id": "a1", "modality": None, "fs_path": None, "file_size": None},
            {"asset_id": "a2", "modality": "video", "fs_path": "/v.mp4", "file_size": 10},
        ])
        out = gq.assets_of_entities(conn, entity_type="장소", min_bundle_size=3, exclude_video=True)
        sql, p = _sql_and_params(cur)
        self.assertIn("a.status = 'registered'", sql)
        self.assertIn("(NOT %(exclude_video)s OR a.modality <> 'video')", sql)
        self.assertIn("ORDER BY 1", sql)  # DISTINCT 와 함께라 선택 목록 첫 컬럼(asset_id)로 정렬
        self.assertIs(p["exclude_video"], True)
        self.assertEqual(out[0], {"asset_id": "a1", "modality": "", "fs_path": None, "file_size": 0})
        self.assertEqual(out[1]["file_size"], 10)


class TestBundleDescription(unittest.TestCase):
    def test_mm_meta_bundle_includes_description(self) -> None:
        node = {"node_id": 1, "name": "제주도", "canonical": {"source": "auto", "description": "섬"}}
        conn, cur = _conn_returning([])
        cur.fetchone.return_value = node
        out = gq.mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도")
        self.assertEqual(out["description"], "섬")

    def test_missing_description_is_none(self) -> None:
        node = {"node_id": 1, "name": "제주도", "canonical": {}}
        conn, cur = _conn_returning([])
        cur.fetchone.return_value = node
        out = gq.mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도")
        self.assertIsNone(out["description"])


if __name__ == "__main__":
    unittest.main()
