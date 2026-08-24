"""v302 — 분류 스킬 테이블 2종 신설: mm_skill + asset_mm_skill_label (spec 085)

Revision ID: v302_mm_skill
Revises: v301_relation_kind_desc

사용자 정의 분류 설정셋(스킬)의 정본 레지스트리(mm_skill)와 배치 판정 결과·이력
(asset_mm_skill_label · multi 기본이라 자산당 스킬당 1..N행, PK(asset_id, skill_code, label_code))를
추가한다. DDL 본문은 migrations/sql/302_mm_skill.sql 단일 출처(run_sql_file 관례).
신규 테이블 2·인덱스 1만 추가하고 다른 스키마는 무접촉이다.

downgrade 는 두 테이블을 FK 역순(label → skill)으로 DROP — 가역. 데이터가 있어도 신규 테이블만
제거하므로 다른 테이블·이력에 영향 없다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op
from migrations.alembic._runsql import run_sql_file

revision = "v302_mm_skill"
down_revision = "v301_relation_kind_desc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("302_mm_skill.sql")


def downgrade() -> None:
    # 신규 테이블만 제거(가역) — asset_mm_skill_label 이 mm_skill.skill_code 를 FK 참조하므로
    # 참조하는 쪽(label)을 먼저 지운다. 인덱스는 테이블과 함께 소멸.
    op.execute("DROP TABLE IF EXISTS asset_mm_skill_label")
    op.execute("DROP TABLE IF EXISTS mm_skill")
