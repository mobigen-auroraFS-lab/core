"""099 G1 — 개체 목록의 **커서 순회**(keyset)와 **모수 총계** — mock conn 단위 테스트(DB 불필요).

무엇을 봉인하나

① **집계는 ``WHERE`` 에 못 쓴다** — ``confirmed_count`` 는 ``HAVING COUNT(DISTINCT ge.src_node)`` 집계라
   같은 질의의 ``WHERE`` 절에서 비교할 수 없다. 그래서 기존 본문을 CTE 로 감싸고 **바깥에서** 이어읽기
   조건을 건다(plan 099 §1-②). 이 구조가 무너지면 SQL 이 실행조차 안 된다.
② **동점 그룹 한가운데서 끊겨도 겹치거나 건너뛰지 않는다** — 구성 자산 수가 같은 개체가 수십 개인
   것은 흔하다(3건짜리 개체 무더기). 조건이 ``confirmed_count < 기준`` 뿐이면 동점 무더기를 통째로
   잃고, ``<=`` 면 통째로 다시 읽는다. 그래서 **동점일 때는 표기 키가 뒤인 것만** 잇는다.
③ **커서를 주지 않으면 종전과 같다** — 기존 호출(백엔드 ``/mm-meta``)이 그대로 돌아야 한다(회귀).
④ **총계는 행을 조립하지 않는다** — ``scope_total``(지우면 N건)은 카드 재료가 아니라 숫자 하나다.

경계(실 DB 대조)는 G2 정지점의 822건 완주 수동 확인(SC-001)이 맡는다 — 여기서는 SQL 모양과
바인딩만 본다.
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


class TestListEntitiesCursorSql(unittest.TestCase):
    """① 집계를 바깥에서 비교하는 CTE 구조."""

    def test_집계는_CTE_안에_이어읽기_조건은_바깥에_있다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50, after_count=5, after_uid="제주도")
        sql, _p = _sql_and_params(cur)
        self.assertIn("WITH ent AS (", sql)
        # 노출 정의(HAVING 집계)는 그대로 안쪽에 남는다.
        self.assertIn("HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s", sql)
        # 🔴 이어읽기 조건이 HAVING 보다 **뒤**에 있어야 바깥 SELECT 다. 안쪽 WHERE 로 내려가면
        #    "집계 함수는 WHERE 에 올 수 없다"로 질의 자체가 실패한다.
        self.assertLess(sql.index("HAVING COUNT(DISTINCT ge.src_node)"), sql.index("%(after_count)s"),
                        "이어읽기 조건이 CTE 안쪽으로 들어갔다 — 집계는 WHERE 에서 비교할 수 없다")

    def test_정렬은_바깥에서_유일_tiebreaker_로_끝난다(self) -> None:
        """헌법 3조 — 같은 질의가 같은 순서를 내야 커서가 성립한다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        sql, _p = _sql_and_params(cur)
        self.assertIn("ORDER BY ent.confirmed_count DESC, ent.entity_uid ASC LIMIT %(limit)s", sql)


class TestListEntitiesTieBoundary(unittest.TestCase):
    """② 동점 그룹 가운데서 끊긴 경우."""

    def test_동점이면_표기_키가_뒤인_것만_잇는다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50, after_count=3, after_uid="나주")
        sql, p = _sql_and_params(cur)
        # 동점 아래(구성 자산 수가 더 적은 개체)는 전부 다음 쪽 대상.
        self.assertIn("ent.confirmed_count < %(after_count)s::bigint", sql)
        # 🔴 동점 무더기 안에서는 **표기 키가 뒤인 것만** — `<` 만 있으면 동점 나머지를 통째로 잃고
        #    `<=` 면 통째로 다시 읽는다(중복). 둘 다 사용자에겐 조용한 오류다.
        self.assertIn(
            "ent.confirmed_count = %(after_count)s::bigint AND ent.entity_uid > %(after_uid)s::text",
            sql)
        self.assertNotIn("ent.confirmed_count <= %(after_count)s", sql)
        self.assertNotIn("ent.entity_uid >= %(after_uid)s", sql)
        self.assertEqual((p["after_count"], p["after_uid"]), (3, "나주"))

    def test_한쪽만_주면_거부한다(self) -> None:
        """반쪽 커서는 조건이 성립하지 않는다 — 조용히 무시하면 처음부터 다시 읽어 **중복**이 난다."""
        conn, _cur = _conn_returning([])
        with self.assertRaises(ValueError):
            gq.list_entities(conn, min_bundle_size=3, limit=50, after_count=3)
        with self.assertRaises(ValueError):
            gq.list_entities(conn, min_bundle_size=3, limit=50, after_uid="나주")


class TestListEntitiesNoCursorRegression(unittest.TestCase):
    """③ 커서를 주지 않으면 종전과 같다."""

    def test_커서를_안_주면_조건이_열려_있다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, entity_type="장소", min_bundle_size=3, limit=200)
        sql, p = _sql_and_params(cur)
        self.assertIsNone(p["after_count"])
        self.assertIsNone(p["after_uid"])
        # NULL 이면 조건이 전부 참이 되도록 열어 둔다 — SQL 모양을 커서 유무로 갈라 두면
        # 한쪽만 고쳐져 경로에 따라 결과가 달라진다(plan 099 §1-⑤ 와 같은 계열의 함정).
        self.assertIn("%(after_count)s::bigint IS NULL", sql)
        # 종전 필터·바인딩은 그대로다.
        self.assertIn("n.node_kind = 'entity'", sql)
        self.assertIn("rk.kind_code = %(kind)s", sql)
        self.assertIn("ge.status = ANY(%(statuses)s)", sql)
        self.assertEqual(p["etype"], "장소")
        self.assertEqual((p["kind"], p["minsize"], p["limit"]), (MM_MEMBER_KIND_CODE, 3, 200))

    def test_반환_모양은_종전과_같다(self) -> None:
        conn, _cur = _conn_returning([{
            "entity_type": "장소", "entity_uid": "제주도", "node_id": 7, "name": "제주도", "source": "auto",
            "description": None, "confirmed_count": 14, "total_count": 14,
            "modalities": ["video"], "keywords": None, "topics": None, "forms": None, "areas": None,
        }])
        [row] = gq.list_entities(conn, min_bundle_size=3, limit=1, after_count=20, after_uid="가")
        self.assertEqual(row["entity_uid"], "제주도")
        self.assertEqual(row["confirmed_count"], 14)
        self.assertEqual(row["keywords"], [])


class TestCountEntities(unittest.TestCase):
    """④ 모수 총계 — 행을 조립하지 않는다."""

    def test_노출_정의와_필터가_같고_COUNT_만_한다(self) -> None:
        conn, cur = _conn_returning([{"n": 822}])
        out = gq.count_entities(conn, entity_type="장소", area_names=["여행·명소"], min_bundle_size=3)
        sql, p = _sql_and_params(cur)
        self.assertEqual(out, 822)
        self.assertIn("COUNT(*)", sql)
        self.assertIn("HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s", sql)
        self.assertIn("%(etype)s", sql)      # 종류 필터는 목록과 같은 조건
        self.assertIn("%(areas)s", sql)      # 갈래 필터도 같은 조건
        # 카드 재료(이름·모달리티·키워드…)는 세는 데 필요 없다 — 조립하면 822건분 배열 집계를 헛돈다.
        self.assertNotIn("ARRAY_AGG", sql)
        self.assertNotIn("%(limit)s", sql)   # 총계는 상한에 걸리면 안 된다(모수여야 한다)
        self.assertEqual((p["etype"], p["areas"], p["area_n"]), ("장소", ["여행·명소"], 1))
        self.assertEqual((p["kind"], p["minsize"]), (MM_MEMBER_KIND_CODE, 3))
        self.assertEqual(p["statuses"], ["active", "proposed"])

    def test_행이_없으면_0(self) -> None:
        conn, cur = _conn_returning([])
        cur.fetchone.return_value = None
        self.assertEqual(gq.count_entities(conn, min_bundle_size=3), 0)

    def test_상태를_주면_그대로_바인딩된다(self) -> None:
        conn, cur = _conn_returning([{"n": 4}])
        gq.count_entities(conn, min_bundle_size=2, statuses=["active"])
        _sql, p = _sql_and_params(cur)
        self.assertEqual(p["statuses"], ["active"])


if __name__ == "__main__":
    unittest.main()
