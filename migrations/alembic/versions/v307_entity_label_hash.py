"""v307 — 개체 갈래 증분 재판정: entity_mm_skill_label.material_hash 추가 (spec 104)

Revision ID: v307_entity_label_hash
Revises: v306_entity_embedding

개체 갈래 판정에 쓴 **재료의 지문**(SHA-256 64자)을 라벨 행에 남길 칸을 만든다. 지문이 없으면
배치가 「다시 판정할 필요가 있는가」를 알 수 없어, 재료가 그대로여도 매시간 다시 판정하고
상위 N개 밖의 개체는 영영 판정하지 않았다. DDL 본문은 migrations/sql/307_entity_label_hash.sql
단일 출처(run_sql_file 관례).

🔴 **NULL 허용 · 소급 채움 없음** — NULL 은 「지문 없는 옛 판정」이라 배치가 재선별한다. 그래서
   이 리비전 뒤 첫 실행은 노출 개체 전부를 한 번 다시 판정한다(지금 재료로 지문을 채우면
   「확인 안 된 지문」이 생긴다).

칸 하나만 추가하고 다른 스키마는 무접촉이다 — downgrade 가 `DROP COLUMN` 하나로 끝나며
표와 기존 라벨 행은 보존된다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v307_entity_label_hash"
down_revision = "v306_entity_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("307_entity_label_hash.sql")


def downgrade() -> None:
    # 칸만 제거(가역) — 표와 기존 라벨 행은 남는다(지문 값은 사라져 재적용 뒤 전량 재판정). 칸 주석은 칸과 함께
    # 소멸한다. 🔴 새 코어가 이 칸을 읽고 쓰므로 **코드(파이프 이미지)를 먼저 되돌린 뒤** 실행한다(SQL 헤더 순서 참조).
    op.execute("ALTER TABLE entity_mm_skill_label DROP COLUMN IF EXISTS material_hash")
