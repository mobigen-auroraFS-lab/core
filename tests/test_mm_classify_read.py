"""095 FR-2 — ``mm_classify.read.label_names_of_assets`` 단위 테스트(mock conn · DB 불필요).

봉인: 빈 입력은 DB 미접촉 · 스킬 코드는 반드시 호출자가 준다 · 미부여 제외가 SQL 에 있다 · 정의 순서(ord)
정렬 · 자산별 묶기 · ``fetch_active_skills`` 재수출.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.mm_classify import persist, read


def _conn_returning(rows: list[dict]):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestLabelNamesOfAssets(unittest.TestCase):
    def test_empty_ids_or_skills_do_not_touch_db(self) -> None:
        conn, cur = _conn_returning([])
        self.assertEqual(read.label_names_of_assets(conn, [], skill_codes=["content_form"]), {})
        self.assertEqual(read.label_names_of_assets(conn, ["a1"], skill_codes=[]), {})
        cur.execute.assert_not_called()

    def test_sql_excludes_unassigned_and_orders_by_definition(self) -> None:
        conn, cur = _conn_returning([])
        read.label_names_of_assets(conn, ["a1", "a2"], skill_codes=["content_form", "food_content"])
        sql, params = cur.execute.call_args.args
        flat = " ".join(sql.split())
        self.assertIn("l.label_code <> 'unassigned'", flat)
        self.assertIn("WITH ORDINALITY", flat)
        self.assertIn("ORDER BY l.asset_id, n.skill_code, n.ord", flat)
        self.assertIn("n.skill_code = l.skill_code", flat)  # 이름 표는 같은 스킬 안에서만 조인
        self.assertEqual(params, {"skills": ["content_form", "food_content"], "ids": ["a1", "a2"]})

    def test_groups_names_by_asset_in_row_order(self) -> None:
        conn, _cur = _conn_returning([
            {"asset_id": "a1", "name": "인터뷰"}, {"asset_id": "a1", "name": "리뷰·해석"},
            {"asset_id": "a2", "name": "레시피"},
        ])
        out = read.label_names_of_assets(conn, ["a1", "a2", "a3"], skill_codes=["content_form"])
        self.assertEqual(out, {"a1": ["인터뷰", "리뷰·해석"], "a2": ["레시피"]})  # a3 은 키 없음

    def test_reexports_fetch_active_skills(self) -> None:
        self.assertIs(read.fetch_active_skills, persist.fetch_active_skills)


if __name__ == "__main__":
    unittest.main()
