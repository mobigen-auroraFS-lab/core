"""v304 — 묶음의 상위 계층: entity_mm_skill_label 신설 (spec 087)

Revision ID: v304_entity_skill_label
Revises: v303_entity_type_vocab

분류 스킬 라벨을 **개체**에 붙일 자리를 만든다. 지금은 라벨이 자산에 붙어 있어 갈래로 좁히면
한 대상이 갈래마다 쪼개진다(실측 `경주시` 6건 = 5+1+1 → 카드 5건인데 상세 6건 · 같은 결함 7개).
DDL 본문은 migrations/sql/304_entity_skill_label.sql 단일 출처(run_sql_file 관례).

🔴 **자산 라벨을 대체하지 않는다** — 「한식 자료 90건 통째 받기」는 자산 라벨이 하는 일이다.
신규 테이블 1 + 인덱스 1만 추가하고 다른 스키마는 무접촉이다.

🔴 **시험 전제**(spec 087 §3 합격선 A4·A5 미달이면 폐기) — downgrade 가 `DROP TABLE` 하나로
끝나도록 설계했다. 데이터가 있어도 신규 테이블만 사라지므로 다른 테이블·이력에 영향이 없다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v304_entity_skill_label"
down_revision = "v303_entity_type_vocab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("304_entity_skill_label.sql")


def downgrade() -> None:
    # 신규 테이블만 제거(가역) — 인덱스는 테이블과 함께 소멸한다. 자식 테이블이 없으므로
    # v302 처럼 FK 역순을 챌 것이 없다.
    op.execute("DROP TABLE IF EXISTS entity_mm_skill_label")
