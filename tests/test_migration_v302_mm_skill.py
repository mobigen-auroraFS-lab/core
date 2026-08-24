"""v302 mm_skill 마이그레이션 스키마 테스트 (코어 — DDL 파일 존재·필수 컬럼·리비전 체인).

실 DB upgrade/downgrade 왕복은 사람 몫(085 T108 계열)이라, CI 가 체인 드리프트·DDL 형상 파손을
잡을 그물이 이 정적 테스트다(v299 `tests/test_migration_v299_asset_topic.py` 선례 동형 — 파일 파싱·DB 불요).
"""
from __future__ import annotations

import os
import re
import unittest

# 마이그레이션 파일 경로(레포 루트 기준·CI 무관). 이 테스트 파일: tests/test_migration_v302_mm_skill.py
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL_PATH = os.path.join(_REPO_ROOT, "migrations", "sql", "302_mm_skill.sql")
_ALEMBIC_PATH = os.path.join(
    _REPO_ROOT, "migrations", "alembic", "versions", "v302_mm_skill.py"
)


class TestMigrationV302(unittest.TestCase):
    """T101 — v302 mm_skill·asset_mm_skill_label DDL 존재 + 필수 컬럼/제약(파일 파싱·DB 불요)."""

    def test_sql_file_exists(self) -> None:
        self.assertTrue(os.path.isfile(_SQL_PATH), f"302_mm_skill.sql 이 없다: {_SQL_PATH}")

    def test_sql_defines_mm_skill_registry(self) -> None:
        with open(_SQL_PATH, encoding="utf-8") as fh:
            sql = fh.read().lower()
        # 레지스트리 형상: UUIDv7 PK + 자연키 UNIQUE(topic_registry 관례) + 선언 전문 JSONB.
        self.assertIn("create table if not exists mm_skill", sql)
        self.assertIn("skill_id   uuid primary key", sql)
        self.assertIn("skill_code text not null unique", sql)
        self.assertIn("policy", sql)
        self.assertIn("labels", sql)
        # status 닫힌 어휘 CHECK — MmSkillStatus 와의 값 교차검증은 test_status_vocab 담당.
        self.assertRegex(sql, r"check \(status in \(")

    def test_sql_defines_label_rows(self) -> None:
        with open(_SQL_PATH, encoding="utf-8") as fh:
            sql = fh.read().lower()
        self.assertIn("create table if not exists asset_mm_skill_label", sql)
        # multi: 자산당 스킬당 1..N행 — PK 3열.
        self.assertIn("primary key (asset_id, skill_code, label_code)", sql)
        # 자산 삭제 시 판정 행 동반 삭제 / 스킬 삭제는 수동 정리 강제(FK NO ACTION — CASCADE 아님).
        self.assertIn("references asset (asset_id) on delete cascade", sql)
        self.assertIn("references mm_skill (skill_code)", sql)
        self.assertNotRegex(sql, r"references mm_skill \(skill_code\)[^,\n]*cascade")
        # 판정 당시 버전(백필 재선별 기준)·문안 버전(이력 완전성 — policy_version 선례) + 관리 조회 인덱스.
        self.assertIn("skill_version", sql)
        self.assertIn("prompt_version", sql)
        self.assertIn("idx_asset_mm_skill_label_skill", sql)

    def test_alembic_revision_chains_and_reversible(self) -> None:
        with open(_ALEMBIC_PATH, encoding="utf-8") as fh:
            src = fh.read()
        # down_revision 이 실제 v301 revision id 로 체인 연결(파일명이 아니라 리비전 값 — 32자 절단형).
        self.assertIn('down_revision = "v301_relation_kind_desc"', src)
        # run_sql_file 관례로 SQL 실행.
        self.assertIn("run_sql_file", src)
        self.assertIn("302_mm_skill.sql", src)
        # downgrade 는 FK 역순 — 자식(label) DROP 이 부모(skill) DROP 보다 먼저 나와야 한다.
        drop_label = src.find("DROP TABLE IF EXISTS asset_mm_skill_label")
        drop_skill = src.find("DROP TABLE IF EXISTS mm_skill")
        self.assertGreater(drop_label, -1, "downgrade 에 asset_mm_skill_label DROP 이 없다")
        self.assertGreater(drop_skill, -1, "downgrade 에 mm_skill DROP 이 없다")
        self.assertLess(drop_label, drop_skill, "FK 역순 위반 — 자식을 먼저 지워야 가역이다")
        # revision id 는 alembic_version.version_num(VARCHAR(32)) 제약 — 32자 이하.
        m = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
        self.assertIsNotNone(m, "revision id 를 찾지 못했다")
        self.assertLessEqual(len(m.group(1)), 32)


if __name__ == "__main__":
    unittest.main()
