"""099 G4/T019a — 개체 목록·총계의 **개체 id 화이트리스트 필터**. mock conn 단위(DB 불필요).

무엇을 하는 필터인가: 검색(``entity_search_os.match_entity_keys``)이 고른 **매칭 개체 집합**을 목록
질의에 얹는 자리다. 검색은 "누가 맞나"만 정하고, 순서(구성 자산 수 내림차순)·쪽 나누기(커서)·카드
재료는 종전대로 DB 가 만든다(spec 099 §3-2a).

🔴 **빈 집합과 ``None`` 은 다른 값이다.**

| 준 값 | 뜻 | 결과 |
|---|---|---|
| ``None`` | 필터 없음(검색을 안 했다) | 전체 — 종전과 **완전히 같은 의미** |
| ``set()`` | 매칭 0건(검색했는데 없다) | **0건** |

둘을 섞으면 "검색했는데 전체가 나오는" 최악의 조용한 오류가 된다(오류가 안 나므로 아무도 모른다).
그래서 판정을 파이썬 ``if not …`` 에 두지 않고 **SQL 에서** 가른다 — 배열이 ``NULL`` 이면 조건이
열리고, 빈 배열이면 ``unnest`` 가 0행이라 ``EXISTS`` 가 거짓이 된다.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq


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


class TestListEntitiesAllowFilter(unittest.TestCase):
    """목록 — 화이트리스트가 CTE 안쪽 ``WHERE`` 에 걸린다."""

    def test_주지_않으면_조건이_열려_있다(self) -> None:
        """회귀 — 종전 호출(백엔드 ``/mm-meta``)은 의미가 그대로여야 한다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200)
        sql, p = _sql_and_params(cur)
        self.assertIsNone(p["allow_types"])
        self.assertIsNone(p["allow_uids"])
        self.assertIn("%(allow_types)s::text[] IS NULL", sql)

    def test_빈_집합은_0건이다(self) -> None:
        """🔴 ``set()`` 은 "매칭 없음"이다 — ``None``(필터 없음)으로 접히면 안 된다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200, uid_allow=set())
        _sql, p = _sql_and_params(cur)
        self.assertEqual(p["allow_types"], [])
        self.assertEqual(p["allow_uids"], [])
        self.assertIsNotNone(p["allow_types"])   # NULL 로 새면 전체가 나온다

    def test_개체_키가_두_배열로_나란히_실린다(self) -> None:
        """(타입, 표기) 짝을 유지해야 한다 — 타입만 맞고 표기가 다른 개체가 새어 들면 안 된다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200,
                         uid_allow={("장소", "제주도"), ("작품", "훈민정음")})
        sql, p = _sql_and_params(cur)
        self.assertEqual(p["allow_types"], ["작품", "장소"])      # 정렬 = 결정적 바인딩
        self.assertEqual(p["allow_uids"], ["훈민정음", "제주도"])
        self.assertIn("unnest(%(allow_types)s::text[], %(allow_uids)s::text[])", sql)

    def test_집합_순서가_달라도_같은_질의가_된다(self) -> None:
        """헌법 3조 — 같은 집합이면 같은 SQL·같은 바인딩."""
        seen = []
        for pairs in ([("장소", "제주도"), ("작품", "훈민정음")],
                      [("작품", "훈민정음"), ("장소", "제주도")]):
            conn, cur = _conn_returning([])
            gq.list_entities(conn, min_bundle_size=3, limit=200, uid_allow=set(pairs))
            seen.append(_sql_and_params(cur))
        self.assertEqual(seen[0], seen[1])

    def test_필터는_CTE_안쪽에_있다(self) -> None:
        """개체 키는 집계가 아니라 ``node`` 컬럼이라 안쪽에서 거른다 — 먼저 줄여야 집계가 싸고,
        커서 조건(바깥)과 섞이면 G1 이 만든 구조가 깨진다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200, uid_allow={("작품", "훈민정음")})
        sql, _p = _sql_and_params(cur)
        self.assertLess(sql.index("%(allow_types)s"),
                        sql.index("HAVING COUNT(DISTINCT ge.src_node)"),
                        "화이트리스트가 CTE 바깥으로 나갔다 — 안쪽 WHERE 가 제자리다")

    def test_커서와_함께_쓸_수_있다(self) -> None:
        """검색 결과를 커서로 이어 읽는 것이 099 의 목적이다(spec §3-2a)."""
        conn, cur = _conn_returning([])
        # 099 G7 로 정렬 키가 셋이 되어 책갈피도 세 값이다(우선 티어·구성 자산 수·표기 키).
        gq.list_entities(conn, min_bundle_size=3, limit=50, uid_allow={("작품", "훈민정음")},
                         after_tier=0, after_count=5, after_uid="가")
        sql, p = _sql_and_params(cur)
        self.assertEqual((p["after_tier"], p["after_count"], p["after_uid"]), (0, 5, "가"))
        self.assertLess(sql.index("%(allow_types)s"), sql.index("%(after_count)s"))


class TestCountEntitiesAllowFilter(unittest.TestCase):
    """총계 — 목록과 **같은 모수**여야 한다."""

    def test_총계도_같은_필터를_건다(self) -> None:
        """총계와 목록이 다른 모수를 말하면 "N건 중 M건"이 거짓말이 된다."""
        conn, cur = _conn_returning([{"n": 3}])
        out = gq.count_entities(conn, min_bundle_size=3, uid_allow={("작품", "훈민정음")})
        sql, p = _sql_and_params(cur)
        self.assertEqual(out, 3)
        self.assertIn("unnest(%(allow_types)s::text[], %(allow_uids)s::text[])", sql)
        self.assertEqual((p["allow_types"], p["allow_uids"]), (["작품"], ["훈민정음"]))

    def test_빈_집합이면_0건_조건이_된다(self) -> None:
        conn, cur = _conn_returning([{"n": 0}])
        self.assertEqual(gq.count_entities(conn, min_bundle_size=3, uid_allow=set()), 0)
        _sql, p = _sql_and_params(cur)
        self.assertEqual(p["allow_types"], [])

    def test_주지_않으면_종전과_같다(self) -> None:
        conn, cur = _conn_returning([{"n": 822}])
        self.assertEqual(gq.count_entities(conn, min_bundle_size=3), 822)
        sql, p = _sql_and_params(cur)
        self.assertIsNone(p["allow_types"])
        self.assertIn("%(allow_types)s::text[] IS NULL", sql)


class TestChipCountsUnchanged(unittest.TestCase):
    """칩 집계는 **손대지 않는다**(현행 유지 · 경계가 애매하면 유지)."""

    def test_종류_칩은_화이트리스트를_모른다(self) -> None:
        """종류는 좁히는 축이 아니라 **갈아타는 축**이다 — 검색으로 좁히면 갈아탈 칩이 사라진다
        (spec 087 2차 정정과 같은 사유). 검색 결과를 칩에 반영할지는 화면 정책(G5)이다."""
        conn, cur = _conn_returning([])
        gq.count_entities_by_type(conn, min_bundle_size=3)
        sql, _p = _sql_and_params(cur)
        self.assertNotIn("allow_types", sql)

    def test_갈래_칩도_그대로다(self) -> None:
        conn, cur = _conn_returning([])
        gq.count_entities_by_area(conn, min_bundle_size=3)
        sql, _p = _sql_and_params(cur)
        self.assertNotIn("allow_types", sql)


if __name__ == "__main__":
    unittest.main()
