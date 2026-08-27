"""v305 — 레거시 schema_registry 제거(중복 레지스트리 정리)

Revision ID: v305_drop_legacy_schema_reg
Revises: v304_entity_skill_label

ext_meta 거버넌스 레지스트리가 두 벌이었다 — 정본 `ext_meta_field_registry`(v291)와 레거시
`schema_registry`(v100 신설 · v220 시드 · v280·v290·v298 개정). 실측(2026-08-27):
`(domain, meta_key)` 14쌍 전수 대조에서 값 차이 **0건** · 3레포 코드 쿼리 **0건** · 유지 이유
("main 호환")는 2026-07-22 분리본 전환으로 소멸. DDL 본문은
migrations/sql/305_drop_legacy_schema_registry.sql 단일 출처(run_sql_file 관례).

가역: downgrade 가 **현행 모양 그대로** 테이블을 만들고 `ext_meta_field_registry` 에서 행을
복사한다. 리비전 5개를 재현하지 않는 이유는 두 테이블 내용이 같음을 실측했기 때문이고, 짧은
쪽이 되돌릴 때 실수가 적다. `schema_id` 는 `field_id` 값을 그대로 쓴다(PK 를 참조하는 FK 없음).

⚠️ **운영 적용 전 확인**(사용자 결정): dev 코드에는 참조가 없으나 운영 DB·외부 도구가 읽는
경로는 이 리비전이 확인할 수 없다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v305_drop_legacy_schema_reg"
down_revision = "v304_entity_skill_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("305_drop_legacy_schema_registry.sql")


def downgrade() -> None:
    # 현행 모양 그대로 복원한다(v100+v280+v290+v298 누적 결과 = 아래 DDL).
    #   컬럼·제약은 삭제 직전 실측값과 같다: schema_id UUID PK · (domain, meta_key) UNIQUE ·
    #   status CHECK(active/inactive) · access_tier CHECK(public/authenticated/authorized/regulated).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_registry (
            schema_id   UUID PRIMARY KEY,
            domain      VARCHAR(50)  NOT NULL,
            meta_key    VARCHAR(100) NOT NULL,
            json_schema JSONB        NOT NULL DEFAULT '{}'::jsonb,
            description TEXT,
            status      VARCHAR(20)  NOT NULL DEFAULT 'active',
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            access_tier VARCHAR(20)  NOT NULL DEFAULT 'authenticated',
            CONSTRAINT schema_registry_status_check
                CHECK (status IN ('active', 'inactive')),
            CONSTRAINT schema_registry_access_tier_check
                CHECK (access_tier IN ('public', 'authenticated', 'authorized', 'regulated')),
            CONSTRAINT uq_schema_registry_domain_key UNIQUE (domain, meta_key)
        )
        """
    )
    # 행 복원 — 정본에서 그대로 복사한다(내용이 같음을 실측했다). `field_id` 를 `schema_id` 로
    # 쓰는 이유는 새 UUID 를 발급하면 두 테이블의 같은 행이 다른 id 를 갖게 되기 때문이다.
    #   ON CONFLICT DO NOTHING: 되돌리기를 두 번 돌려도 안전하다.
    op.execute(
        """
        INSERT INTO schema_registry
            (schema_id, domain, meta_key, json_schema, description, status, created_at, access_tier)
        SELECT field_id, domain, meta_key, json_schema, description, status, created_at, access_tier
          FROM ext_meta_field_registry
            ON CONFLICT (schema_id) DO NOTHING
        """
    )
