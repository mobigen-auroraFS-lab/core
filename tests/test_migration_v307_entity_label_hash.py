"""v307 entity_label_hash 마이그레이션 스키마 테스트 (코어 — DDL 파일 존재·컬럼 형상·리비전 체인).

무엇을 봉인하나: 개체 갈래 표에 「판정 재료 지문」 칸(``material_hash``)을 더하는 마이그레이션이
아래 계약을 지키는지 본다. 실 DB upgrade/downgrade 왕복은 사람 몫이라(실 DB 확인 단계), CI 가
체인 드리프트·DDL 형상 파손을 잡을 그물이 이 정적 테스트다(v302 ``test_migration_v302_mm_skill``
와 같은 방식 — 파일 파싱·DB 불요).

    ① NULL 허용 — 기존 행은 지문이 없다(NULL = 「어떤 재료로 판정했는지 모르는 옛 판정」 → 재선별).
       NOT NULL 이면 기존 행 때문에 upgrade 가 실패하거나, 기본값으로 「확인 안 된 지문」이 생긴다.
    ② 멱등 — ``ADD COLUMN IF NOT EXISTS`` (재실행 안전).
    ③ 가역 — downgrade 가 ``DROP COLUMN IF EXISTS`` 로 칸만 지운다(라벨 행은 보존 · 지문 값은 사라져
       재적용 뒤 첫 실행에서 전량 다시 판정된다 — 「무손실」은 라벨 행에 대해서만 맞다).
    ④ 체인 — ``down_revision`` 이 v306 리비전 값이고, 리비전 ID 는 32자 이하 · 레포 전체 head 가 하나.

문자열 포함 검사만으로는 이름값을 못 한다(2026-10-07 검토: 결함 12종 중 4종만 잡혔다 — import 줄·docstring 이
대신 맞아떨어졌다). 그래서 함수 본문은 ``ast`` 로 보고, SQL 은 주석을 뺀 뒤 **허용 목록**(문장 2개)으로 본다.
"""
from __future__ import annotations

import ast
import os
import re
import unittest

# 마이그레이션 파일 경로(레포 루트 기준·CI 무관). 이 테스트 파일: tests/test_migration_v307_entity_label_hash.py
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL_PATH = os.path.join(_REPO_ROOT, "migrations", "sql", "307_entity_label_hash.sql")
_ALEMBIC_PATH = os.path.join(
    _REPO_ROOT, "migrations", "alembic", "versions", "v307_entity_label_hash.py"
)


def _read(path: str) -> str:
    """파일 전문을 읽는다(없으면 테스트가 FileNotFoundError 로 실패한다).

    Args:
        path: 읽을 파일 경로.

    Returns:
        파일 내용 문자열.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_sql_comments(sql: str) -> str:
    """SQL 의 ``--`` 줄 주석을 지운다 — 주석 속 낱말(예: "NOT NULL 이 아니다")을 DDL 로 오인하지 않게.

    Args:
        sql: SQL 원문.

    Returns:
        주석을 뺀 SQL.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _statements(sql: str) -> list[str]:
    """주석을 뺀 SQL 을 문장 단위로 나눈다(공백 정규화 · 소문자 · 빈 문장 제외).

    Args:
        sql: SQL 원문.

    Returns:
        문장 목록.
    """
    flat = " ".join(_strip_sql_comments(sql).lower().split())
    return [st.strip() for st in flat.split(";") if st.strip()]


def _function_calls(src: str, func: str) -> list[ast.Call]:
    """모듈 소스에서 함수 ``func`` 본문 안의 호출 노드를 모은다(docstring·import 줄은 보지 않는다).

    Args:
        src: 파이썬 소스.
        func: 함수 이름(``upgrade``·``downgrade``).

    Returns:
        그 함수 본문의 ``ast.Call`` 목록. 함수가 없으면 빈 목록.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    return []


def _call_name(call: ast.Call) -> str:
    """호출 대상 이름(``run_sql_file`` · ``op.execute`` → ``execute``).

    Args:
        call: 호출 노드.

    Returns:
        이름 문자열(알 수 없으면 빈 문자열).
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class TestMigrationV307SQL(unittest.TestCase):
    """DDL 본문 — 칸 추가·NULL 허용·멱등."""

    def test_sql_file_exists(self) -> None:
        self.assertTrue(os.path.isfile(_SQL_PATH), f"307_entity_label_hash.sql 이 없다: {_SQL_PATH}")

    def test_컬럼_추가가_멱등이다(self) -> None:
        # IF NOT EXISTS — 사람이 같은 SQL 을 두 번 돌려도 깨지지 않는다.
        sql = " ".join(_strip_sql_comments(_read(_SQL_PATH)).lower().split())
        self.assertIn(
            "alter table entity_mm_skill_label add column if not exists material_hash char(64)",
            sql,
        )

    def test_지문_칸은_null_허용이다(self) -> None:
        # 🔴 기존 행(지문 없는 옛 판정)은 NULL 로 남아 첫 실행에서 재선별돼야 한다.
        #    NOT NULL·DEFAULT 를 걸면 「확인 안 된 지문」이 생겨 재판정이 영영 안 돈다.
        sql = " ".join(_strip_sql_comments(_read(_SQL_PATH)).lower().split())
        m = re.search(r"add column if not exists material_hash char\(64\)([^;]*);", sql)
        self.assertIsNotNone(m, "material_hash 칸 추가 문장을 찾지 못했다")
        tail = m.group(1)
        self.assertNotIn("not null", tail)
        self.assertNotIn("default", tail)

    def test_다른_스키마는_건드리지_않는다(self) -> None:
        # 칸 하나 추가가 전부다 — 테이블 생성·삭제·인덱스가 섞이면 가역성 판단이 달라진다.
        sql = _strip_sql_comments(_read(_SQL_PATH)).lower()
        self.assertNotIn("create table", sql)
        self.assertNotIn("drop ", sql)
        self.assertNotIn("create index", sql)

    def test_칸_주석이_있다(self) -> None:
        # DB 를 직접 보는 사람이 NULL 의 뜻(옛 판정 → 재선별)을 알 수 있어야 한다.
        #   주석을 뺀 본문에서 본다 — `--` 로 막아 둔 COMMENT 문은 실행되지 않는다.
        sql = _strip_sql_comments(_read(_SQL_PATH)).lower()
        self.assertIn("comment on column entity_mm_skill_label.material_hash", sql)

    def test_문장은_칸_추가와_칸_주석_둘뿐이다(self) -> None:
        # 허용 목록 — 금지어 목록은 `create unique index`·`alter column` 같은 변형을 놓친다.
        sts = _statements(_read(_SQL_PATH))
        self.assertEqual(len(sts), 2, f"문장이 2개가 아니다: {sts}")
        add_column = (
            "alter table entity_mm_skill_label add column if not exists material_hash char(64)"
        )
        self.assertTrue(sts[0].startswith(add_column), sts[0])
        self.assertTrue(
            sts[1].startswith("comment on column entity_mm_skill_label.material_hash is"),
            sts[1],
        )

    def test_소급_채움_기본값_not_null_이_어디에도_없다(self) -> None:
        # 🔴 ADD COLUMN 꼬리만 보면 별도 문장(UPDATE … SET material_hash · ALTER COLUMN … SET DEFAULT ·
        #    SET NOT NULL)을 놓친다. 셋 다 「확인 안 된 지문」을 만들거나 재선별을 막는다(spec 104 §2 · ADR D2).
        sql = " ".join(_strip_sql_comments(_read(_SQL_PATH)).lower().split())
        self.assertNotRegex(sql, r"\bupdate\b")
        self.assertNotRegex(sql, r"\bdefault\b")
        self.assertNotIn("not null", sql)


class TestMigrationV307Alembic(unittest.TestCase):
    """alembic 리비전 — 체인·가역."""

    def test_revision_chains_after_v306(self) -> None:
        src = _read(_ALEMBIC_PATH)
        self.assertRegex(src, r'(?m)^revision\s*=\s*"v307_entity_label_hash"')
        # down_revision 은 파일명이 아니라 v306 의 **리비전 값**이어야 체인이 이어진다.
        self.assertRegex(src, r'(?m)^down_revision\s*=\s*"v306_entity_embedding"')

    def test_revision_id_fits_version_num(self) -> None:
        # alembic_version.version_num 은 VARCHAR(32) — 넘으면 upgrade 기록 단계에서 실패한다.
        m = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', _read(_ALEMBIC_PATH), re.MULTILINE)
        self.assertIsNotNone(m, "revision id 를 찾지 못했다")
        self.assertLessEqual(len(m.group(1)), 32)

    def test_upgrade_runs_sql_file(self) -> None:
        # run_sql_file 관례 — DDL 본문은 SQL 파일 단일 출처. 🔴 import 줄·docstring 이 아니라
        #   **upgrade 본문의 호출**을 본다(본문을 `pass` 로 바꿔도 통과하던 결함 · 2026-10-07 검토).
        calls = [c for c in _function_calls(_read(_ALEMBIC_PATH), "upgrade")
                 if _call_name(c) == "run_sql_file"]
        self.assertEqual(len(calls), 1, "upgrade 본문에 run_sql_file 호출이 정확히 하나여야 한다")
        args = [a.value for a in calls[0].args if isinstance(a, ast.Constant)]
        self.assertEqual(args, ["307_entity_label_hash.sql"])

    def test_downgrade_drops_only_the_column(self) -> None:
        # 가역 — 칸만 지우고 표·라벨 행은 남긴다(지문 값은 사라져 재적용 뒤 전량 재판정 · 모듈 docstring ③).
        #   🔴 본문 호출을 ast 로 본다 — 주석 처리 + `pass`, TRUNCATE 추가를 문자열 검사는 못 잡았다.
        calls = _function_calls(_read(_ALEMBIC_PATH), "downgrade")
        executes = [c for c in calls if _call_name(c) == "execute"]
        self.assertEqual(len(executes), 1, "downgrade 는 op.execute 한 번이어야 한다")
        self.assertEqual(len(calls), 1, "downgrade 에 다른 호출이 섞였다")
        sql = " ".join(str(executes[0].args[0].value).lower().split())
        self.assertEqual(
            sql, "alter table entity_mm_skill_label drop column if exists material_hash"
        )
        self.assertNotIn("truncate", sql)
        self.assertNotIn("delete", sql)


class TestMigrationHeads(unittest.TestCase):
    """레포 전체 리비전 head 가 하나인지 — v307 체인 검사가 파일 하나만 보던 빈틈(다른 브랜치가 v306 을
    부모로 리비전을 하나 더 만들면 head 가 둘이 된다)."""

    def test_single_head(self) -> None:
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory
        except ImportError:  # pragma: no cover — [migrate] extra 미설치 환경
            self.skipTest("alembic 미설치")
        cfg = Config(os.path.join(_REPO_ROOT, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_REPO_ROOT, "migrations", "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        # 개수만 본다 — 이름을 박으면 다음 리비전(v308…)이 이 테스트를 고치게 만든다.
        self.assertEqual(len(heads), 1, f"head 가 하나가 아니다(분기된 리비전): {heads}")


if __name__ == "__main__":
    unittest.main()
