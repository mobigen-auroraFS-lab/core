"""주제 목록(``list_topics``)을 DB 가 센 값이 **자산 행을 하나씩 센 값과 같은지** 실 DB 로 대조한다.

RUN_DB_E2E 게이트(실 PostgreSQL 필요 · LLM 0 · 쓰기 0). ``list_topics`` 는 ``COUNT(*)`` 로 세는데, 이것이
distinct 자산 수와 같은 것은 ``asset_topic`` 이 자산당 1행이라는 전제 덕분이다. 그 전제와 결과를 함께 본다 —
기준값은 날 행(``asset_topic`` ⋈ ``asset``)을 가져와 파이썬 집합으로 센 것이다.
설계 배경: `specs/109-topics-list-speed`
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

_RUN_DB_E2E = os.environ.get("RUN_DB_E2E")

# 기준값용 날 행 — 집계하지 않고 자산 행을 그대로 가져온다(이 테스트에만 있는 사본).
_RAW_ROWS_SQL = """
SELECT at.topic_ko, at.subtopic_ko, at.asset_id
  FROM asset_topic at
  JOIN asset a ON a.asset_id = at.asset_id
 WHERE COALESCE(at.topic_ko, '') <> ''
"""


def _count_by_hand(rows: list[tuple]) -> list[dict]:
    """날 행을 (주제, 하위주제)별 · 주제별 distinct 자산 집합으로 센다 — ``list_topics`` 와 같은 정렬 · 정규화."""
    groups: dict[tuple, set[str]] = {}
    topic_assets: dict[str, set[str]] = {}
    for topic_ko, subtopic_ko, asset_id in rows:
        sub = subtopic_ko or None
        groups.setdefault((topic_ko, sub), set()).add(str(asset_id))
        topic_assets.setdefault(topic_ko, set()).add(str(asset_id))
    out = [{"topic_ko": ko, "subtopic_ko": sub, "asset_count": len(ids), "topic_asset_count": len(topic_assets[ko])}
           for (ko, sub), ids in groups.items()]
    out.sort(key=lambda o: (o["topic_ko"], o["subtopic_ko"] or ""))
    return out


def _sid(n: int) -> str:
    """테스트용 합성 자산 id(관례 접두어 ``018f0000-0000-7000-8000-`` — 실 자산 id 와 섞이지 않게)."""
    return f"018f0000-0000-7000-8000-{n:012d}"


def _lit(v: str | None) -> str:
    """고른 행을 SQL VALUES 리터럴로(고정 테스트 값만 들어온다 — 따옴표가 없는 값)."""
    return "NULL" if v is None else f"'{v}'"


@unittest.skipUnless(_RUN_DB_E2E, "RUN_DB_E2E 미설정 — 실 DB e2e skip")
class TestListTopicsMatchesRowByRowCount(unittest.TestCase):
    """``list_topics`` == 날 행을 하나씩 센 값 — 실 dev DB."""

    @classmethod
    def setUpClass(cls) -> None:
        from dotenv import load_dotenv

        from src.config.settings import init_settings
        from src.database.postgres_util import PostgresUtil

        dp = Path(__file__).resolve().parents[1] / ".env.dev"
        if dp.is_file():
            load_dotenv(dotenv_path=dp, override=False)
        init_settings("dev")
        cls.db = PostgresUtil()
        cls.db.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.__exit__(None, None, None)

    def test_one_row_per_asset_premise(self) -> None:
        """``COUNT(*)`` = distinct 자산 수의 전제 — ``asset_topic`` 에 같은 자산이 두 번 나오지 않는다."""
        with self.db.transaction() as conn:
            total, distinct = conn.execute("SELECT COUNT(*), COUNT(DISTINCT asset_id) FROM asset_topic").fetchone()
        self.assertGreater(total, 0, "asset_topic 이 비었다 — dev DB 에 주제가 붙은 자산이 없다")
        self.assertEqual(total, distinct)

    def test_list_topics_equals_row_by_row_count(self) -> None:
        from src.topic.asset_topic_query import list_topics

        with self.db.transaction() as conn:
            want = _count_by_hand(conn.execute(_RAW_ROWS_SQL).fetchall())
            got = list_topics(conn)
            again = list_topics(conn)
        self.assertTrue(want, "주제가 붙은 자산이 0건 — 대조가 무의미하다")
        self.assertEqual(got, want)                # 필드 · 값 · 순서까지
        self.assertEqual(again, got)               # 같은 입력 같은 출력(헌법 3조)
        for o in got:
            self.assertIs(type(o["asset_count"]), int)
            self.assertIs(type(o["topic_asset_count"]), int)
        # 주제마다 하위주제 수의 합 = 주제 전체 수(자산당 1행이면 성립 — 깨지면 위 전제 테스트도 실패한다)
        by_topic: dict[str, int] = {}
        for o in got:
            by_topic[o["topic_ko"]] = by_topic.get(o["topic_ko"], 0) + o["asset_count"]
        self.assertEqual(by_topic, {o["topic_ko"]: o["topic_asset_count"] for o in got})


    def test_grouping_rules_on_chosen_rows(self) -> None:
        """빈 하위주제 · NULL 섞임 · 빈 주제 · 자산 표에 없는 자산을 **일부러 고른 행**으로 같은 SQL 을 실 DB 에서 돌린다.

        dev DB 에 그런 행이 있는지는 보장되지 않으므로, 같은 이름의 CTE 로 ``asset_topic`` · ``asset`` 을 가린다 —
        쓰기 없이 실제 PostgreSQL 의 ``NULLIF`` 묶음 · 창 합 · 조인을 그대로 거친다.
        """
        from unittest.mock import patch

        from src.topic import asset_topic_query as atq

        chosen = [  # (topic_ko, subtopic_ko, asset_id)
            ("역사", "", _sid(1)),     # 빈 하위주제
            ("역사", None, _sid(2)),   # NULL — 위와 한 묶음
            ("역사", "유적", _sid(3)),
            ("역사", "유적", _sid(4)),
            ("음악", None, _sid(5)),
            ("", "무시", _sid(6)),      # 빈 주제 — 빠진다
            (None, "무시", _sid(7)),    # NULL 주제 — 빠진다
            ("음악", "가요", _sid(8)),  # 자산 표에 없다 — 조인으로 빠진다
        ]
        registered = [r[2] for r in chosen if r[2] != _sid(8)]
        values = ", ".join(f"({_lit(ko)}, {_lit(sub)}, '{aid}'::uuid)" for ko, sub, aid in chosen)
        assets = ", ".join(f"('{a}'::uuid)" for a in registered)
        masked = (f"WITH asset_topic(topic_ko, subtopic_ko, asset_id) AS (VALUES {values}),\n"
                  f"     asset(asset_id) AS (VALUES {assets})\n" + atq._LIST_TOPICS_SQL)
        want = _count_by_hand([r for r in chosen if r[0] and r[2] in registered])
        with self.db.transaction() as conn, patch.object(atq, "_LIST_TOPICS_SQL", masked):
            got = atq.list_topics(conn)
        self.assertEqual(got, want)
        self.assertEqual(got, [
            {"topic_ko": "역사", "subtopic_ko": None, "asset_count": 2, "topic_asset_count": 4},
            {"topic_ko": "역사", "subtopic_ko": "유적", "asset_count": 2, "topic_asset_count": 4},
            {"topic_ko": "음악", "subtopic_ko": None, "asset_count": 1, "topic_asset_count": 1},
        ])

if __name__ == "__main__":
    unittest.main()
