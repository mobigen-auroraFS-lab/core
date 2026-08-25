import unittest
from unittest.mock import MagicMock


class TestFetchActiveKinds(unittest.TestCase):
    def _conn_returning(self, rows):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__.return_value = cur
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        return conn, cur

    def test_fetch_active_relation_kinds_filters_legacy_and_inactive(self):
        from src.relations.relation_type_catalog import fetch_active_relation_kinds
        rows = [{"type_code": "duplicate_near", "type_name": "유사 근접", "description": "..."}]
        conn, cur = self._conn_returning(rows)
        out = fetch_active_relation_kinds(conn)
        self.assertEqual(out[0]["type_code"], "duplicate_near")
        sql = cur.execute.call_args[0][0]
        self.assertIn("status = 'active'", sql)
        self.assertIn("relation_kind", sql)
        self.assertNotIn("relation_type", sql)  # 조합 테이블 미참조

    def test_prompt_catalog_excludes_mm_member(self):
        """084 — ``mm_member``(자산→개체 소속)는 **프롬프트 카탈로그에 실리지 않는다**.

        왜 필요한가: 소속 kind 는 엣지가 되려면 ``active`` 여야 하는데(``graph_persist`` 는 active
        kind 만 받는다), active 라는 이유로 이 목록에 실리면 LLM 이 **자산 쌍**에 "mm_member" 를
        제안하고 그 오염이 그래프에 영속된다. 그래서 "active 등록 + 프롬프트 제외"로 갈랐다
        (spec 084 착수 전 결정 ② 확정).
        """
        from src.relations.relation_type_catalog import fetch_active_relation_kinds
        from src.relations.schema import LEGACY_DOMAIN_TYPE_CODES

        conn, cur = self._conn_returning([])
        fetch_active_relation_kinds(conn)

        excluded = cur.execute.call_args[0][1][0]
        self.assertIn("mm_member", excluded)
        # 레거시 도메인 코드 제외는 그대로 살아 있다(제외 기준이 넓어진 것이지 바뀐 것이 아니다).
        self.assertTrue(set(LEGACY_DOMAIN_TYPE_CODES) <= set(excluded))
        # 바인딩이 결정적이어야 한다 — frozenset 을 그대로 list() 하면 실행마다 순서가 달라져
        # 같은 질의의 파라미터가 흔들린다(비교·재현이 안 된다).
        self.assertEqual(excluded, sorted(excluded))

    def test_existing_catalog_rows_are_unaffected(self):
        # 🔴 동작 불변 근거: 현 DB 에 ``mm_member`` 행이 **없다**(084 배치 미실행). 제외 목록이
        # 넓어져도 지금 카탈로그에 있는 종류는 하나도 빠지지 않으므로 프롬프트 문안은 바이트 동일하다.
        from src.relations.relation_type_catalog import fetch_active_relation_kinds
        rows = [
            {"type_code": "duplicate_near", "type_name": "유사 근접", "description": "d",
             "is_symmetric": True},
            {"type_code": "same_domain", "type_name": "같은 분야", "description": "s",
             "is_symmetric": True},
        ]
        conn, _cur = self._conn_returning(rows)
        out = fetch_active_relation_kinds(conn)
        self.assertEqual([r["type_code"] for r in out], ["duplicate_near", "same_domain"])
