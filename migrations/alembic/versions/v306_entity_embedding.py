"""v306 — 개체 의미 검색: entity_embedding 신설 (spec 090)

Revision ID: v306_entity_embedding
Revises: v305_drop_legacy_schema_reg

개체마다 벡터를 두어 **글자가 겹치지 않아도** 찾게 한다. 089(질의 토큰 분리)로 다어절 질의가
0% → 43.3% 가 됐지만 남은 실패 30건은 **어휘 불일치**여서 문자열로는 방법이 없다
(`발효`→김치 0건). DDL 본문은 migrations/sql/306_entity_embedding.sql 단일 출처(run_sql_file 관례).

🔴 **착수 전 측정(090 G0 · 코드 0줄)으로 설계가 정해졌다** — 예측 C1 80% · C2 83.3% · C3 100%.
   유사도 컷오프를 두지 않고 상위 3 을 취한다(컷오프는 정답 22건 중 16건을 버렸다).

⚠️ FK 를 걸 수 없다 — `node(entity_type, entity_uid)` 의 유니크가 부분 인덱스이고 PostgreSQL 은
   부분 유니크를 FK 대상으로 받지 않는다. v304 와 같은 이유로 앱 검증을 택한다.

신규 테이블 1 + 인덱스 1만 추가하고 다른 스키마는 무접촉이다 — downgrade 가 `DROP TABLE`
하나로 끝난다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v306_entity_embedding"
down_revision = "v305_drop_legacy_schema_reg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("306_entity_embedding.sql")


def downgrade() -> None:
    # 신규 테이블만 제거(가역) — 인덱스는 테이블과 함께 소멸한다. 자식 테이블이 없어
    # FK 역순을 챌 것이 없다(v304 와 같은 모양).
    op.execute("DROP TABLE IF EXISTS entity_embedding")
