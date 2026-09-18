"""개체 정렬의 **동점자 처리가 유일한가** — 표기만으로는 부족하다(2026-09-18).

무엇이 문제였나: 개체를 구별하는 자연키는 **(종류, 표기) 둘**인데 정렬의 마지막 기준은
``entity_uid`` 하나뿐이었다. 같은 표기가 서로 다른 종류로 갈린 개체가 실제로 있다 —
실측 4쌍(``김부장`` 인물/작품 · ``백두산`` 장소/작품 · ``현역가왕`` 사건/작품 ·
``레이디두아`` 인물/작품).

그 둘이 **같은 우선 티어·같은 구성 자산 수**가 되는 순간 순서가 정해지지 않는다. 이어읽기는
"직전 쪽 마지막 값 다음부터"로 자리를 잡으므로, 같은 값을 가진 행이 둘이면 DB 가 어느 쪽이
'다음'인지 고르지 못한다 — 하나를 **건너뛰거나 두 번 낸다.** 오류는 나지 않는다(099 G1 이
291개 동점 그룹에서 막은 것과 같은 계열의 조용한 사고).

지금 터지지 않는 것은 **우연**이다(실측: 겹치는 4쌍이 모두 구성 자산 수가 달라 앞 단에서
갈린다). 자료가 쌓여 한 번이라도 같아지면 그때부터 조용히 샌다.

🔴 ``entity_type`` 을 **``entity_uid`` 뒤에** 붙인다. 앞에 두면 동점 무리 안의 **보이는 순서가
지금과 달라진다**(종류별로 뭉친다) — 고치려는 것은 '정해지지 않은 경우'뿐이므로, 이미 정해져
있던 순서는 건드리지 않는 자리에 넣는다.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq


def _conn_returning(rows: list[dict]) -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql_and_params(cur: MagicMock) -> tuple[str, dict]:
    sql, params = cur.execute.call_args.args
    return " ".join(sql.split()), params


class TestEntitySortIsTotal(unittest.TestCase):
    """① 정렬이 **한 가지 순서로만** 확정되는가."""

    def test_정렬이_자연키_전체로_끝난다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        sql, _p = _sql_and_params(cur)
        self.assertIn(
            "ORDER BY ent.prio_tier DESC, ent.confirmed_count DESC, "
            "ent.entity_uid ASC, ent.entity_type ASC LIMIT %(limit)s", sql)


class TestEntityKeysetIsTotal(unittest.TestCase):
    """② 이어읽기 조건이 정렬과 **같은 단수**인가 — 어긋나면 경계에서 샌다."""

    def test_표기까지_같으면_종류가_뒤인_것만_잇는다(self) -> None:
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         after_tier=0, after_count=5, after_uid="백두산", after_type="장소")
        sql, p = _sql_and_params(cur)
        # 표기가 같은 자리에서는 종류로 가른다 — `>` 여야 한다(`>=` 면 같은 행을 다시 낸다).
        self.assertIn(
            "ent.entity_uid = %(after_uid)s::text "
            "AND ent.entity_type > %(after_type)s::text", sql)
        self.assertNotIn("ent.entity_type >= %(after_type)s", sql)
        # 표기가 뒤인 것은 종류와 무관하게 전부 다음 쪽이다.
        self.assertIn("ent.entity_uid > %(after_uid)s::text", sql)
        self.assertEqual((p["after_uid"], p["after_type"]), ("백두산", "장소"))

    def test_네_값을_함께_줘야_한다(self) -> None:
        """반쪽 책갈피는 조건이 성립하지 않는다 — 조용히 무시하면 중복이 난다."""
        conn, _cur = _conn_returning([])
        with self.assertRaises(ValueError):
            gq.list_entities(conn, min_bundle_size=3, limit=50,
                             after_tier=0, after_count=5, after_uid="백두산")
        with self.assertRaises(ValueError):
            gq.list_entities(conn, min_bundle_size=3, limit=50,
                             after_count=5, after_uid="백두산", after_type="장소")

    def test_커서를_안_주면_조건이_열려_있다(self) -> None:
        """첫 쪽 회귀 — 책갈피가 없으면 네 값 모두 NULL 이라 조건 전체가 참이다."""
        conn, cur = _conn_returning([])
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        _sql, p = _sql_and_params(cur)
        self.assertEqual(
            (p["after_tier"], p["after_count"], p["after_uid"], p["after_type"]),
            (None, None, None, None))


if __name__ == "__main__":
    unittest.main()
