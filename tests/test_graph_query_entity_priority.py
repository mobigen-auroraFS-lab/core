"""099 G7 — 개체 목록의 **우선 티어**(이름이 걸린 개체를 맨 앞으로) · mock conn 단위(DB 불필요).

왜 필요한가(실측 2026-09-17): 이름으로 찾아도 그 개체가 위에 오지 않았다 — `숭례문` 은 **7위**,
`경포대` 는 **15위**였다(1~3위는 서울특별시·운문사·화엄사). 정렬이 구성 자산 수 하나뿐이라 **큰
개체가 늘 위**로 오기 때문이다(spec §3-2a 결정의 부작용). 도서관 비유로, 제목이 정확히 같은 책이
있는데도 "두꺼운 책부터" 꽂아 둔 셈이다.

그래서 정렬을 **3단**으로 바꾼다: ``우선티어 DESC → 구성 자산 수 DESC → 표기 키 ASC``.
🔴 **누가 우선인지는 코어가 정하지 않는다** — 호출부(화면)가 집합(``uid_first``)으로 준다.
"이름이 정확히 같다"는 판정은 화면 정책이고, 코어에 넣으면 화면이 바뀔 때마다 코어를 고쳐야 한다.

🔴 여기서 가장 조심하는 것은 **회귀**다. 커서(keyset)는 099 G1 에서 가장 공들여 검증한 자리이고,
실측상 구성 자산 3건짜리 동점 무더기가 **291개**라 쪽 경계는 거의 늘 동점 한가운데 떨어진다.
정렬 키가 하나 늘면 이어읽기 조건도 함께 3단이 되어야 하며, 한 단이라도 빠지면 그 무더기를
**통째로 잃거나 통째로 중복**한다(둘 다 오류가 나지 않아 아무도 모른다).

봉인 목록: ① 정렬 3단 ② 커서 조건 3단 ③ ``None``/``set()`` 이면 종전과 같다 ④ 책갈피 세 값 전부
있어야 한다 ⑤ 총계는 우선 티어와 무관하다.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq


def _conn_returning(rows: list[dict]):
    """``conn.cursor(row_factory=dict_row)`` 컨텍스트매니저를 흉내내는 mock conn.

    Args:
        rows: 질의가 돌려줄 행들.

    Returns:
        ``(conn, cur)`` 짝.
    """
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql_and_params(cur) -> tuple[str, dict]:
    """실행된 SQL(공백 정규화)과 바인딩을 꺼낸다.

    Args:
        cur: mock 커서.

    Returns:
        ``(정규화된 SQL, 바인딩 dict)``.
    """
    sql, params = cur.execute.call_args.args
    return " ".join(sql.split()), params


class TestPriorityOrder(unittest.TestCase):
    """① 정렬이 3단이다 — 우선 티어가 **맨 앞**."""

    def test_정렬은_우선티어_구성수_표기키_순이다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50, uid_first={("장소", "숭례문")})
        sql, _p = _sql_and_params(cur)
        self.assertIn(
            "ORDER BY ent.prio_tier DESC, ent.confirmed_count DESC, ent.entity_uid ASC "
            "LIMIT %(limit)s", sql)

    def test_우선_집합은_타입과_표기를_짝으로_맞춘다(self) -> None:
        """한 배열로 표기만 맞추면 **타입이 다른 동명 개체**가 함께 올라온다(`김밥` 음식/작품 실례)."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         uid_first={("작품", "김밥"), ("음식", "김밥")})
        sql, p = _sql_and_params(cur)
        self.assertIn("fs.t = n.entity_type AND fs.u = n.entity_uid", sql)
        # 같은 집합이면 같은 바인딩이어야 한다(헌법 3조) — 정렬해 넘긴다.
        self.assertEqual(p["first_types"], ["음식", "작품"])
        self.assertEqual(p["first_uids"], ["김밥", "김밥"])

    def test_티어는_0_또는_1_이며_NULL_이면_전부_0_이다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        sql, _p = _sql_and_params(cur)
        self.assertIn("CASE WHEN %(first_types)s::text[] IS NULL THEN 0", sql)
        self.assertIn("AS prio_tier", sql)


class TestPriorityKeyset(unittest.TestCase):
    """② 커서 조건도 3단 — 티어 경계와 동점 무더기를 함께 다룬다."""

    def test_이어읽기_조건이_3단이다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         after_tier=1, after_count=5, after_uid="숭례문",
                         uid_first={("장소", "숭례문")})
        sql, p = _sql_and_params(cur)
        # ⓐ 티어가 낮은 개체는 전부 다음 쪽(우선 무리를 다 읽은 뒤 평범한 무리로 넘어간다).
        self.assertIn("ent.prio_tier < %(after_tier)s::int", sql)
        # ⓑ 같은 티어 안에서는 종전 2단 규칙 그대로 — 구성 자산 수, 그리고 동점이면 표기 키.
        self.assertIn(
            "ent.prio_tier = %(after_tier)s::int AND (ent.confirmed_count < %(after_count)s::bigint "
            "OR (ent.confirmed_count = %(after_count)s::bigint "
            "AND ent.entity_uid > %(after_uid)s::text))", sql)
        # 🔴 느슨한 비교는 금물 — `<=` 면 동점 무더기를 통째로 다시 읽는다(중복).
        self.assertNotIn("ent.prio_tier <= %(after_tier)s", sql)
        self.assertNotIn("ent.confirmed_count <= %(after_count)s", sql)
        self.assertNotIn("ent.entity_uid >= %(after_uid)s", sql)
        self.assertEqual((p["after_tier"], p["after_count"], p["after_uid"]), (1, 5, "숭례문"))

    def test_책갈피는_세_값을_함께_줘야_한다(self) -> None:
        """반쪽 책갈피를 조용히 무시하면 첫 쪽을 다시 읽어 **중복**이 나는데 오류가 없다."""
        conn, _cur = _conn_returning([])
        for kw in ({"after_tier": 0}, {"after_count": 3}, {"after_uid": "나주"},
                   {"after_count": 3, "after_uid": "나주"},
                   {"after_tier": 0, "after_count": 3}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                gq.list_entities(conn, min_bundle_size=3, limit=50, **kw)

    def test_세_값을_모두_주면_통과한다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         after_tier=0, after_count=3, after_uid="나주")
        _sql, p = _sql_and_params(cur)
        self.assertEqual((p["after_tier"], p["after_count"], p["after_uid"]), (0, 3, "나주"))


class TestNoPriorityRegression(unittest.TestCase):
    """③ ``None``·``set()`` 이면 종전과 **완전히 같은 결과**여야 한다(회귀)."""

    def test_주지_않으면_바인딩이_NULL_이다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200)
        _sql, p = _sql_and_params(cur)
        self.assertIsNone(p["first_types"])
        self.assertIsNone(p["first_uids"])
        self.assertIsNone(p["after_tier"])

    def test_빈_집합이면_우선_대상이_없다(self) -> None:
        """🔴 ``uid_allow`` 와 다르다 — 저쪽은 빈 집합이 **0건**이지만, 여기 빈 집합은 **순서만**
        의 이야기라 "앞세울 개체가 없다"(= 종전 순서)일 뿐이다. 둘을 섞으면 검색 결과가 사라진다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=200, uid_first=set())
        _sql, p = _sql_and_params(cur)
        self.assertEqual(p["first_types"], [])
        self.assertEqual(p["first_uids"], [])

    def test_우선_집합이_없어도_SQL_은_한_가지다(self) -> None:
        """커서 유무·우선 유무로 SQL 을 갈라 두면 한쪽만 고쳐져 경로에 따라 결과가 달라진다."""
        conn_a, cur_a = _conn_returning([])
        gq.list_entities(conn_a, min_bundle_size=3, limit=200)
        conn_b, cur_b = _conn_returning([])
        gq.list_entities(conn_b, min_bundle_size=3, limit=200, uid_first={("장소", "숭례문")})
        self.assertEqual(_sql_and_params(cur_a)[0], _sql_and_params(cur_b)[0])

    def test_반환_모양은_종전과_같다(self) -> None:
        """우선 티어는 **순서**를 위한 내부 값이라 행 계약을 늘리지 않는다."""
        conn, _cur = _conn_returning([{
            "entity_type": "장소", "entity_uid": "숭례문", "node_id": 7, "name": "숭례문",
            "source": "auto", "description": None, "confirmed_count": 4, "total_count": 4,
            "modalities": ["image"], "keywords": None, "topics": None, "forms": None,
            "areas": None, "prio_tier": 1,
        }])
        [row] = gq.list_entities(conn, min_bundle_size=3, limit=1,
                                 uid_first={("장소", "숭례문")})
        self.assertEqual(set(row) - {
            "entity_type", "entity_uid", "node_id", "name", "source", "description",
            "confirmed_count", "total_count", "modalities", "keywords", "topics", "forms",
            "areas"}, set())


class TestCountUnaffected(unittest.TestCase):
    """⑤ 총계는 **순서와 무관**하다 — 같은 인자 묶음을 그대로 넘길 수 있게 받기만 한다."""

    def test_우선_집합을_줘도_같은_수를_센다(self) -> None:
        conn_a, cur_a = _conn_returning([{"n": 822}])
        plain = gq.count_entities(conn_a, min_bundle_size=3)
        conn_b, cur_b = _conn_returning([{"n": 822}])
        first = gq.count_entities(conn_b, min_bundle_size=3, uid_first={("장소", "숭례문")})
        self.assertEqual(plain, first)
        # 세는 질의 자체가 달라지면 "N건 중 M건"의 N 이 조건에 따라 흔들린다.
        self.assertEqual(_sql_and_params(cur_a)[0], _sql_and_params(cur_b)[0])

    def test_세는_질의에는_우선_티어가_없다(self) -> None:
        conn, cur = _conn_returning([{"n": 5}])
        gq.count_entities(conn, min_bundle_size=3, uid_first={("장소", "숭례문")})
        sql, _p = _sql_and_params(cur)
        self.assertNotIn("prio_tier", sql)


if __name__ == "__main__":
    unittest.main()
