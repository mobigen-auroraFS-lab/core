"""v303 — 개체 타입 어휘 분리: mm_meta_type_vocab 신설 + mm_skill 예약 행 이관 (spec 086)

Revision ID: v303_entity_type_vocab
Revises: v302_mm_skill

`mm_skill` 에 섞여 있던 두 가지를 갈라 놓는다 — **자산**을 분류하는 스킬(mm_skill 유지)과
**개체**를 판정하는 타입 어휘(신설 mm_meta_type_vocab). 컬럼은 도메인 언어로 바꾼다
(skill_code→vocab_code · labels→types). DDL·이관 본문은 migrations/sql/303_entity_type_vocab.sql
단일 출처(run_sql_file 관례).

신규 테이블 1개 추가 + mm_skill 의 **행 하나 이동**뿐 — 컬럼·제약 변경 0, 다른 테이블 무접촉.

가역: downgrade 가 그 행을 mm_skill 로 되돌려 INSERT 하고(같은 skill_id·skill_code='mm_meta_type')
테이블을 DROP 한다. vocab_id 에 원래 skill_id 를 보존했으므로 **왕복 후 행이 완전히 동일**하다.
어휘를 개정한 뒤 되돌리면 그 개정분이 mm_skill 로 넘어간다(정본이 옮겨 갔으니 의도된 동작이다).

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v303_entity_type_vocab"
down_revision = "v302_mm_skill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("303_entity_type_vocab.sql")


def downgrade() -> None:
    # 행을 mm_skill 로 되돌린 **뒤** 테이블을 지운다(순서가 바뀌면 데이터가 사라진다).
    #   · skill_code 는 코드 상수 `MM_META_TYPE_SKILL_CODE` 와 같은 'mm_meta_type' 로 복원한다 —
    #     되돌린 직후의 코드(v302 시점)가 그 값으로 읽기 때문이다.
    #   · ON CONFLICT DO NOTHING: 되돌리기를 두 번 돌려도 안전하다.
    #   · 어휘 테이블이 없으면(upgrade 전 상태) INSERT 는 그냥 0행이다 — IF EXISTS 로 감싼다.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'mm_meta_type_vocab') THEN
                INSERT INTO mm_skill
                    (skill_id, skill_code, name, version, policy, labels, status,
                     created_at, updated_at)
                SELECT vocab_id, 'mm_meta_type', name, version, policy, types, status,
                       created_at, updated_at
                  FROM mm_meta_type_vocab WHERE vocab_code = 'default'
                    ON CONFLICT (skill_code) DO NOTHING;
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE IF EXISTS mm_meta_type_vocab")
