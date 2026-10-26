"""v308 — ext_meta 키 등록 title_match · title_only (spec 114 제목 참고 요약)

Revision ID: v308_title_hint_ext_keys
Revises: v307_entity_label_hash

요약기가 제목을 참고할 때 같은 호출에서 판단한 「제목-내용 일치」(title_match)와 「제목만으로 요약」(title_only)을
ext_meta 에 남긴다. 쓰기 경로가 등록된 키만 받으므로(validate_ext_meta) general · medical 에 두 키를 등록한다.
DML 본문은 migrations/sql/308_title_hint_ext_keys.sql 단일 출처(run_sql_file 관례 · 멱등 INSERT).

downgrade 는 등록 행만 지운다(가역). 이미 두 키가 적재된 ext_meta 가 있으면 그 데이터는 남지만, 이후 같은 키를
쓰는 적재는 다시 미등록 키로 거부된다 — 되돌리기 전에 제목 참고 설정(SUMMARY_TITLE_HINT)을 끈다.
⚠️ 등록이 지워진 키는 읽기 경로(``project_ext_meta``)가 등급 확인 없이 내보낸다 — 값(yes/no · 참/거짓)의 민감도는
낮지만, 막아야 하면 되돌리기 절차에 두 키 데이터 정리(``ext_meta - 'title_match' - 'title_only'``)를 따로 넣는다.

주의: revision ID 는 alembic_version.version_num(VARCHAR(32)) 에 저장되므로 32자 이하로 유지한다.
"""
from __future__ import annotations

from alembic import op

from migrations.alembic._runsql import run_sql_file

revision = "v308_title_hint_ext_keys"
down_revision = "v307_entity_label_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("308_title_hint_ext_keys.sql")


def downgrade() -> None:
    op.execute(
        "DELETE FROM ext_meta_field_registry "
        "WHERE domain IN ('general', 'medical') AND meta_key IN ('title_match', 'title_only')"
    )
