"""spec 113 — 개체 목록과 총 건수를 한 질의로(``list_entities_with_total``) — mock conn 단위 테스트(DB 불필요).

무엇을 봉인하나

① **집합은 한 번** — 노출 개체 집합(``ent``)을 물질화하고 총계를 그 집합에서 센다. 총계가 ``page`` 에서
   세어지면 쪽 크기(30)가 나오고, 따로 집합을 다시 만들면 고치려던 중복 계산이 그대로다.
② **쪽 행은 ``list_entities`` 와 같다** — 같은 인자면 같은 바인딩 · 같은 행 모양 · 같은 순서.
③ **쪽이 비면** 행이 없어 총계를 실을 곳이 없다 — 첫 쪽이든 이어 받기든 같은 조건으로 총계를 따로 센다.
④ **``list_entities`` 는 글자 하나까지 그대로** — 백엔드가 갈아탈 때까지 지금 질의가 그대로 돈다.

실 DB 동치(행 · 총계가 ``list_entities`` + ``count_entities`` 와 같음)는 ``test_mm_meta_seams_e2e`` 가 맡는다.
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import MagicMock

from src.relations import graph_query as gq


def _row(uid: str, n: int, *, scope_n: int | None = None, etype: str = "장소") -> dict:
    """목록 질의가 돌려주는 한 행(DB 모양) — 총계 칸은 새 함수 질의에만 있다."""
    r = {
        "entity_type": etype, "entity_uid": uid, "node_id": f"n-{uid}", "name": uid, "source": "auto",
        "description": None, "confirmed_count": n, "total_count": n + 1,
        "modalities": ["text", "image"], "keywords": ["k"], "topics": [], "forms": [], "areas": ["갈래"],
        "prio_tier": 0,
    }
    if scope_n is not None:
        r["scope_n"] = scope_n
    return r


def _conn(*results: list[dict]):
    """execute 마다 다음 결과를 돌려주는 mock conn — ``fetchall`` · ``fetchone`` 둘 다 같은 결과를 본다."""
    cur = MagicMock()
    queue = list(results)
    state: dict[str, list[dict]] = {"rows": []}

    def _execute(sql, params=None):
        state["rows"] = queue.pop(0) if queue else []

    cur.execute.side_effect = _execute
    cur.fetchall.side_effect = lambda: state["rows"]
    cur.fetchone.side_effect = lambda: state["rows"][0] if state["rows"] else None
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _flat(sql: str) -> str:
    return " ".join(sql.split())


_KW = {"entity_type": "장소", "area_names": ["문화유산·역사"], "min_bundle_size": 3, "limit": 30,
       "statuses": ["active"], "form_skill_codes": ["content_form"], "uid_allow": {("장소", "경복궁")},
       "uid_first": {("장소", "경복궁")}, "uid_semantic": {("장소", "광화문")}}
# 총계에 걸리는 조건만 — 쪽이 빈 경우 따로 세는 질의가 이것과 같은 바인딩이어야 한다.
_COUNT_KW = {k: _KW[k] for k in ("entity_type", "area_names", "min_bundle_size", "statuses", "uid_allow")}


class TestOneSetSql(unittest.TestCase):
    """① 집합은 한 번 만들고 총계는 그 집합에서 센다."""

    def test_집합을_물질화하고_총계는_ent_에서_센다(self) -> None:
        conn, cur = _conn([_row("경복궁", 9, scope_n=17)])
        gq.list_entities_with_total(conn, **_KW)
        self.assertEqual(1, cur.execute.call_count, "쪽이 있으면 질의는 한 번이어야 한다")
        sql = _flat(cur.execute.call_args.args[0])
        self.assertIn("WITH ent AS MATERIALIZED (", sql)
        self.assertIn("cnt AS (SELECT COUNT(*) AS n FROM ent)", sql)
        self.assertIn("(SELECT c.n FROM cnt c) AS scope_n", sql)
        # 🔴 총계가 page(이어읽기 조건 · LIMIT 뒤)에서 세어지면 모수가 아니라 쪽 크기 · 남은 수가 나온다.
        cnt_body = re.search(r"cnt AS \((.*?)\) SELECT n\.entity_type", sql)
        self.assertIsNotNone(cnt_body, "총계 CTE 를 찾지 못했다")
        self.assertNotIn("page", cnt_body.group(1))
        self.assertEqual(1, sql.count("HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s"),
                         "노출 집합 정의가 두 번 들어갔다 — 집합을 다시 만들고 있다")

    def test_총계_CTE_는_쪽_고르기_뒤_바깥_SELECT_앞에_있다(self) -> None:
        conn, cur = _conn([_row("경복궁", 9, scope_n=1)])
        gq.list_entities_with_total(conn, **_KW)
        sql = _flat(cur.execute.call_args.args[0])
        self.assertLess(sql.index("page AS ("), sql.index("cnt AS ("))
        self.assertLess(sql.index("cnt AS ("), sql.index("SELECT n.entity_type"))


class TestRowsSameAsList(unittest.TestCase):
    """② 같은 인자면 같은 바인딩 · 같은 행."""

    def test_바인딩이_list_entities_와_같다(self) -> None:
        c1, cur1 = _conn([])
        gq.list_entities(c1, **_KW)
        c2, cur2 = _conn([_row("경복궁", 9, scope_n=1)])
        gq.list_entities_with_total(c2, **_KW)
        self.assertEqual(cur1.execute.call_args.args[1], cur2.execute.call_args.args[1])

    def test_행_모양과_순서가_list_entities_와_같고_총계는_scope_n(self) -> None:
        db = [_row("경복궁", 9, scope_n=17), _row("광화문", 5, scope_n=17)]
        c1, _ = _conn([{k: v for k, v in r.items() if k != "scope_n"} for r in db])
        want = gq.list_entities(c1, **_KW)
        c2, _ = _conn(db)
        rows, total = gq.list_entities_with_total(c2, **_KW)
        self.assertEqual(want, rows)
        self.assertNotIn("scope_n", rows[0], "총계 칸이 행에 새어 나왔다")
        self.assertEqual(17, total)
        self.assertIsInstance(total, int)

    def test_첫_행의_총계_칸을_총계로_쓴다(self) -> None:
        """행마다 같은 값이라 어느 행이든 같다(``cnt`` 는 한 행). 우선 티어와 무관함은 실DB e2e 가 본다."""
        conn, _ = _conn([_row("경복궁", 9, scope_n=2), _row("광화문", 5, scope_n=2)])
        _rows, total = gq.list_entities_with_total(conn, **_KW)
        self.assertEqual(2, total)


class TestEmptyPage(unittest.TestCase):
    """③ 쪽이 비면 같은 조건으로 따로 센다 — 첫 쪽이 비었다고 0 으로 단정하지 않는다."""

    def _assert_counted_like_count_entities(self, cur) -> None:
        sql, params = cur.execute.call_args.args
        self.assertIn("SELECT COUNT(*) AS n", _flat(sql))
        # 같은 조건(종류 · 갈래 · 임계 · 상태 · 허용 목록)으로 센다 — 하나라도 빠지면 다른 모수가 나온다.
        c3, cur3 = _conn([{"n": 0}])
        gq.count_entities(c3, **_COUNT_KW)
        self.assertEqual(cur3.execute.call_args.args, (sql, params))

    def test_첫_쪽이_비면_같은_조건으로_총계를_따로_센다(self) -> None:
        conn, cur = _conn([], [{"n": 0}])
        self.assertEqual(([], 0), gq.list_entities_with_total(conn, **_KW))
        self.assertEqual(2, cur.execute.call_count)
        self._assert_counted_like_count_entities(cur)

    def test_이어_받기_쪽이_비면_같은_조건으로_총계를_따로_센다(self) -> None:
        conn, cur = _conn([], [{"n": 41}])
        rows, total = gq.list_entities_with_total(
            conn, **_KW, after_tier=0, after_count=3, after_uid="종묘", after_type="장소")
        self.assertEqual(([], 41), (rows, total))
        self.assertEqual(2, cur.execute.call_count)
        self._assert_counted_like_count_entities(cur)


class TestGuards(unittest.TestCase):
    def test_반쪽_책갈피는_ValueError(self) -> None:
        conn, cur = _conn([])
        with self.assertRaises(ValueError):
            gq.list_entities_with_total(conn, min_bundle_size=3, limit=30, after_count=5)
        cur.execute.assert_not_called()

    def test_list_entities_질의는_그대로다(self) -> None:
        """④ 새 칸 · 물질화 · 총계 CTE 는 새 함수에만 — 기존 질의가 바뀌면 백엔드 동작이 따라 바뀐다."""
        conn, cur = _conn([])
        gq.list_entities(conn, **_KW)
        sql = _flat(cur.execute.call_args.args[0])
        self.assertIn("WITH ent AS (", sql)
        for absent in ("MATERIALIZED", "cnt AS", "scope_n"):
            self.assertNotIn(absent, sql)
        self.assertIn("p.prio_tier FROM page p", sql)

    def test_두_질의의_차이는_빈_자리_세_곳뿐이다(self) -> None:
        """새 함수 질의에서 세 자리(물질화 · 총계 CTE · 총계 칸)를 걷어 내면 ``list_entities`` 질의와 개행까지 같다."""
        c1, cur1 = _conn([])
        gq.list_entities(c1, **_KW)
        c2, cur2 = _conn([_row("경복궁", 9, scope_n=1)])
        gq.list_entities_with_total(c2, **_KW)
        stripped = (cur2.execute.call_args.args[0]
                    .replace(" MATERIALIZED", "", 1)
                    .replace(",\ncnt AS (SELECT COUNT(*) AS n FROM ent)", "", 1)
                    .replace(",\n       (SELECT c.n FROM cnt c) AS scope_n", "", 1))
        self.assertEqual(cur1.execute.call_args.args[0], stripped)


if __name__ == "__main__":
    unittest.main()
