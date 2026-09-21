"""우선 티어를 **3단**으로 — 뜻으로 상위인 개체도 앞자리에 세운다(2026-09-21).

무엇이 문제였나: 화면은 ``prio_tier DESC, confirmed_count DESC`` 로 정렬한다. 커서가 성립하려면
DB 정렬이어야 해서 099 가 그렇게 정했고, 그 판단 자체는 옳다. 그런데 **구성 자산 수**가 사실상
순서를 지배해, 뜻으로 14등인 개체가 자산이 많으면 1위로 올라온다.

실측(2026-09-21 · 「남자 배우」):

    kNN 순  : 하정우 · 정해인 · 이제훈 · 권수현 · 박정민 · 류승룡   ← 전부 남자
    화면 순 : 채원빈 · 서현철 · 김고은 · …                        ← 자산 수 순이라 뒤집힌다

사용자가 보는 것은 앞 5~7개다. 뒤에 맞는 것이 있어도 "잘못 나온다"가 된다.

어떻게 고치나: **이미 있는 티어 장치**를 재사용한다. 관련도로 정렬하는 것이 아니라 **관련도 상위를
앞자리로 승급**시키는 것이라, 099 가 피한 "관련도순은 커서를 못 만든다" 제약에 걸리지 않는다.
커서에 실리는 값의 개수도 그대로다(티어는 이미 들어 있다).

    티어 2 = 이름이 정확히 일치        ← 가장 확실한 신호
    티어 1 = 뜻으로 상위 N위
    티어 0 = 나머지

🔴 티어 **안에서는** 종전대로 구성 자산 수 순이다. 뜻 순서까지 반영하려면 티어를 잘게 쪼개야
하는데(1등=5·2등=4…) 그러면 커서 값이 복잡해지고 099 가 피한 자리로 되돌아간다. 앞자리에 맞는
것들이 모이기만 해도 체감은 크게 달라진다.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq


def _conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql_params(cur: MagicMock) -> tuple[str, dict]:
    sql, params = cur.execute.call_args.args
    return " ".join(sql.split()), params


class TestThreeTierOrder(unittest.TestCase):
    def test_이름_일치가_뜻_상위보다_앞이다(self) -> None:
        """둘 다 앞자리지만 **이름이 더 확실한 신호**다 — 같은 티어로 묶으면 그 차이가 사라진다."""
        conn, cur = _conn()
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         uid_first={("인물", "숭례문")}, uid_semantic={("인물", "하정우")})
        sql, p = _sql_params(cur)
        self.assertIn("THEN 2", sql)
        self.assertIn("THEN 1", sql)
        self.assertEqual(p["first_uids"], ["숭례문"])
        self.assertEqual(p["semantic_uids"], ["하정우"])

    def test_정렬_키는_늘지_않는다(self) -> None:
        """🔴 커서 계약 불변 — 티어는 이미 실려 있다. 키가 늘면 옛 커서가 전부 깨진다."""
        conn, cur = _conn()
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        sql, _p = _sql_params(cur)
        self.assertIn(
            "ORDER BY ent.prio_tier DESC, ent.confirmed_count DESC, "
            "ent.entity_uid ASC, ent.entity_type ASC LIMIT %(limit)s", sql)

    def test_뜻_집합만_줘도_동작한다(self) -> None:
        """검색어가 이름과 하나도 안 맞는 경우(개념 질의)가 오히려 흔하다."""
        conn, cur = _conn()
        gq.list_entities(conn, min_bundle_size=3, limit=50,
                         uid_semantic={("인물", "하정우"), ("인물", "정해인")})
        _sql, p = _sql_params(cur)
        self.assertEqual(p["semantic_uids"], ["정해인", "하정우"])
        self.assertIsNone(p["first_uids"])

    def test_아무것도_안_주면_종전과_같다(self) -> None:
        """회귀 — 우선 대상이 없으면 전원 티어 0 이라 순서가 종전 그대로다."""
        conn, cur = _conn()
        gq.list_entities(conn, min_bundle_size=3, limit=50)
        _sql, p = _sql_params(cur)
        self.assertIsNone(p["first_uids"])
        self.assertIsNone(p["semantic_uids"])

    def test_같은_집합이면_같은_바인딩이다(self) -> None:
        """헌법 3조 — 집합은 순서가 없으므로 정렬해 넘겨야 실행마다 같은 질의가 된다."""
        def run() -> dict:
            conn, cur = _conn()
            gq.list_entities(conn, min_bundle_size=3, limit=50,
                             uid_semantic={("인물", "하정우"), ("작품", "명량"), ("장소", "경주시")})
            return _sql_params(cur)[1]

        self.assertEqual(run()["semantic_uids"], run()["semantic_uids"])
        self.assertEqual(run()["semantic_types"], run()["semantic_types"])

    def test_총계는_인자를_받되_세는_데_쓰지_않는다(self) -> None:
        """🔴 호출부가 목록·총계에 **같은 묶음**을 넘길 수 있어야 하지만, 순서가 개수를 바꾸면 안 된다.

        그래서 인자는 받되 질의에는 바인딩하지 않는다 — 바인딩하면 언젠가 WHERE 로 새어 든다.
        """
        conn, cur = _conn()
        cur.fetchall.return_value = [{"n": 0}]
        gq.count_entities(conn, min_bundle_size=3,
                          uid_first={("인물", "숭례문")}, uid_semantic={("인물", "하정우")})
        sql, p = _sql_params(cur)
        self.assertNotIn("semantic_uids", p)
        self.assertNotIn("first_uids", p)
        self.assertNotIn("prio_tier", sql)


if __name__ == "__main__":
    unittest.main()
