"""084 T012 — 점수 축 소급 처리 스크립트가 **소속 엣지(mm_member)를 지우지 않는지**.

무엇을 막는 테스트인가(spec 084 착수 전 결정 ③): 대기 중인 v4 소급 처리
(`scripts/reset_relations_for_rescore.py`)의 두 파괴적 단계가 084 소속 엣지와 **정확히 겹친다**.

    - ``--phase purge`` : ``DELETE … WHERE confidence IS NULL AND reviewed_by IS NULL``
      소속 엣지는 신뢰도를 매기지 않으므로(``confidence`` NULL · spec §4) 사람이 만지지 않은
      전건이 이 술어에 걸린다 → **소속이 통째로 사라진다.**
    - ``--phase clear`` : ``graph_edge`` 전량 DELETE → 마찬가지.

관계 점수 축을 되돌리는 작업이 **다른 기능의 데이터를 함께 지우는** 것이 문제의 본질이다. 비유하면
사무실 서류를 연도별로 폐기하는데 같은 캐비닛에 든 남의 부서 서류까지 버리는 것이다 — 캐비닛
(테이블)이 같을 뿐 폐기 대상이 아니다. 그래서 세 phase 의 술어에 "소속 엣지는 건드리지 않는다"
가드를 넣고, 이 파일이 그 가드를 봉인한다.

DB·네트워크 불필요 — 아래 ``_FakeDb`` 가 실행된 SQL·파라미터만 모으는 가짜 핸들이다. 실제로
무엇이 지워지는지는 실 DB 의 몫이고(사람 실행), 여기서는 **술어에 가드가 실렸는지**를 본다.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import scripts.reset_relations_for_rescore as rr

from src.relations.schema import MM_MEMBER_KIND_CODE


class _Cur:
    """가짜 커서 — 실행 SQL·파라미터를 로그에 쌓고, 카운트 질의에는 0을 돌려준다."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append((" ".join(str(sql).split()), params))

    def fetchone(self) -> tuple[int]:
        return (0,)

    def fetchall(self) -> list[tuple[Any, ...]]:
        # _counts 의 GROUP BY 행 · 백업의 전 열 조회 둘 다 빈 결과로 둔다.
        return []

    @property
    def description(self) -> list[Any]:
        return []

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _FakeDb:
    """``PostgresUtil`` 의 최소 형상(``connection()``·``transaction()``)만 흉내낸다."""

    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    @contextlib.contextmanager
    def _conn(self):  # noqa: ANN202 - 테스트 헬퍼
        outer = self

        class _Conn:
            def cursor(self, **_k: Any) -> _Cur:
                return _Cur(outer.log)

        yield _Conn()

    def connection(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._conn()

    def transaction(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._conn()

    # ── 단정 헬퍼 ────────────────────────────────────────────────────────────
    def writes(self) -> list[tuple[str, Any]]:
        """DB 를 바꾸는 문장만 골라 준다(dry-run 검증용)."""
        return [(s, p) for s, p in self.log if s.startswith(("DELETE", "UPDATE"))]

    def find(self, prefix: str) -> list[tuple[str, Any]]:
        """접두로 시작하는 실행 기록을 모아 준다.

        Args:
            prefix: SQL 접두(공백 정규화된 한 줄 기준).

        Returns:
            ``(sql, params)`` 목록.
        """
        return [(s, p) for s, p in self.log if s.startswith(prefix)]


def _guarded(sql: str) -> bool:
    """SQL 이 소속 엣지 보호 가드를 품고 있는가.

    Args:
        sql: 검사할 SQL(공백 정규화 전/후 무관).

    Returns:
        가드(``relation_kind`` 하위질의 + ``kind_code = %s`` 바인딩)가 있으면 ``True``.
    """
    flat = " ".join(sql.split())
    return "relation_kind" in flat and "kind_code = %s" in flat


class TestGuardedSql(unittest.TestCase):
    """SQL 상수 — 파괴적 술어에 가드가 **붙어 있어야** 한다."""

    def test_purge_술어에_가드가_있다(self):
        self.assertTrue(_guarded(rr._PURGE_SQL), rr._PURGE_SQL)

    def test_clear_삭제문에_가드가_있다(self):
        self.assertTrue(_guarded(rr._CLEAR_EDGES_SQL), rr._CLEAR_EDGES_SQL)

    def test_reset_갱신문에_가드가_있다(self):
        # 소속 엣지의 status·confidence 는 관계 점수 축과 무관하다 — 리셋의 대상이 아니다.
        self.assertTrue(_guarded(rr._RESET_SQL), rr._RESET_SQL)

    def test_kind_코드를_하드코딩하지_않는다(self):
        # 문자열 사본이 생기면 언젠가 한쪽만 고쳐진다 — 정본은 src/relations/schema.py 상수다.
        for sql in (rr._PURGE_SQL, rr._CLEAR_EDGES_SQL, rr._RESET_SQL):
            with self.subTest(sql[:30]):
                self.assertNotIn(f"'{MM_MEMBER_KIND_CODE}'", sql)


class TestPhasePurge(unittest.TestCase):
    def test_삭제문이_kind_코드를_바인딩한다(self):
        db = _FakeDb()
        rr.phase_purge(db, apply=True)
        deletes = db.find("DELETE FROM graph_edge")
        self.assertEqual(len(deletes), 1)
        sql, params = deletes[0]
        self.assertTrue(_guarded(sql), sql)
        self.assertEqual(params, (MM_MEMBER_KIND_CODE,))

    def test_대상_건수_질의도_같은_가드를_쓴다(self):
        # 보고 숫자와 삭제 숫자가 갈리면 "소거 대상 N건" 을 신뢰할 수 없다.
        db = _FakeDb()
        rr.phase_purge(db, apply=True)
        counts = [(s, p) for s, p in db.log
                  if s.startswith("SELECT COUNT(*) FROM graph_edge WHERE confidence IS NULL")]
        self.assertEqual(len(counts), 1)
        self.assertTrue(_guarded(counts[0][0]), counts[0][0])
        self.assertEqual(counts[0][1], (MM_MEMBER_KIND_CODE,))

    def test_dry_run_은_아무것도_쓰지_않는다(self):
        db = _FakeDb()
        rr.phase_purge(db, apply=False)
        self.assertEqual(db.writes(), [])


class TestPhaseClear(unittest.TestCase):
    def test_전량_삭제도_소속_엣지는_남긴다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            # 백업 파일 크기 가드(100바이트)를 통과시키려고 빈 결과 대신 실제 파일을 쓰게 두고,
            # 백업 성공 여부와 무관하게 **삭제문 형태**만 본다(백업 실패면 삭제가 없어 테스트가 잡는다).
            rc = rr.phase_clear(db, apply=True, backup_dir=Path(tmp) / "b")
        deletes = db.find("DELETE FROM graph_edge")
        self.assertEqual(rc, 1)  # 빈 백업(100바이트 미만) → 삭제 중단이 정상 동작
        self.assertEqual(deletes, [])

    def test_백업이_있으면_가드된_삭제문을_실행한다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(rr, "_write_backup",
                                            return_value=Path(tmp) / "backup.json"):
                rc = rr.phase_clear(db, apply=True, backup_dir=Path(tmp))
        self.assertEqual(rc, 0)
        deletes = db.find("DELETE FROM graph_edge")
        self.assertEqual(len(deletes), 1)
        sql, params = deletes[0]
        self.assertTrue(_guarded(sql), sql)
        self.assertEqual(params, (MM_MEMBER_KIND_CODE,))

    def test_dry_run_은_아무것도_쓰지_않는다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            rr.phase_clear(db, apply=False, backup_dir=Path(tmp))
        self.assertEqual(db.writes(), [])


class TestPhaseReset(unittest.TestCase):
    def test_갱신문이_소속_엣지를_건드리지_않는다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(rr, "_write_backup",
                                            return_value=Path(tmp) / "backup.json"):
                rr.phase_reset(db, apply=True, backup_dir=Path(tmp))
        updates = db.find("UPDATE graph_edge")
        self.assertEqual(len(updates), 1)
        sql, params = updates[0]
        self.assertTrue(_guarded(sql), sql)
        self.assertEqual(params, (MM_MEMBER_KIND_CODE,))

    def test_대상_건수_질의도_같은_가드를_쓴다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            rr.phase_reset(db, apply=False, backup_dir=Path(tmp))
        counts = [(s, p) for s, p in db.log
                  if s.startswith("SELECT COUNT(*) FROM graph_edge WHERE reviewed_by IS NULL")]
        self.assertEqual(len(counts), 1)
        self.assertTrue(_guarded(counts[0][0]), counts[0][0])

    def test_dry_run_은_아무것도_쓰지_않는다(self):
        db = _FakeDb()
        with tempfile.TemporaryDirectory() as tmp:
            rr.phase_reset(db, apply=False, backup_dir=Path(tmp))
        self.assertEqual(db.writes(), [])


if __name__ == "__main__":
    unittest.main()
