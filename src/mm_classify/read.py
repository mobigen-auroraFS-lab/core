"""분류 스킬 라벨 **읽기** seam — 자산에 붙은 라벨 이름을 화면용으로 읽는다(095 FR-2 · 조회 전용).

무엇을 하는 모듈인가: 분류 스킬(085)이 자산에 붙여 둔 라벨 코드(``asset_mm_skill_label``)를 사람이 읽는
**이름**으로 바꿔 돌려준다. 코드→이름 표의 정본은 ``mm_skill.labels`` 다 — 화면이 그 표를 따로 들고 있으면
스킬 라벨을 고칠 때 두 곳을 고쳐야 한다. 그래서 이름 변환은 여기서 한 번만 한다.

왜 코어인가(093 세 질문 ①·③): 라벨 이름의 순서(스킬 정의 순서)와 "미부여(``unassigned``)는 라벨이 아니라
라벨이 없다는 사실" 이라는 규칙은 어디서 읽어도 같아야 하고, 잘못 짜면(정의 순서 대신 코드순 · 미부여를
칩으로) 조용히 다른 화면이 된다. 어느 스킬을 보일지·칩을 어떻게 그릴지는 호출자(백엔드) 몫이다.

활성 스킬 목록은 ``mm_classify.persist.fetch_active_skills`` 가 정본이며 여기서 재수출만 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from src.mm_classify.persist import fetch_active_skills

__all__ = ["fetch_active_skills", "label_names_of_assets"]

# 자산 → 라벨 이름. ``names`` 가 스킬 정의 순서(ord)를 들고 있어 한 자산의 라벨이 **정의 순서대로** 나온다
# (실행마다 순서가 바뀌면 화면 칩이 흔들린다). 미부여는 라벨이 아니므로 뺀다 — 라벨 JSON 에 그 코드가 없어
# 조인만으로도 빠지지만, 규칙을 SQL 에 적어 두어 JSON 이 바뀌어도 새지 않게 한다.
_LABEL_NAMES_OF_ASSETS_SQL = """
WITH names AS (
    SELECT s.skill_code, (lb->>'code') AS code, (lb->>'name') AS name, ord
      FROM mm_skill s,
           LATERAL jsonb_array_elements(s.labels) WITH ORDINALITY AS t(lb, ord)
     WHERE s.skill_code = ANY(%(skills)s)
)
SELECT l.asset_id::text AS asset_id, n.name
  FROM asset_mm_skill_label l
  JOIN names n ON n.skill_code = l.skill_code AND n.code = l.label_code
 WHERE l.skill_code = ANY(%(skills)s)
   AND l.label_code <> 'unassigned'
   AND l.asset_id::text = ANY(%(ids)s)
 ORDER BY l.asset_id, n.skill_code, n.ord
"""


def label_names_of_assets(
    conn: Connection[Any], asset_ids: Sequence[str], *, skill_codes: Sequence[str]
) -> dict[str, list[str]]:
    """자산들의 스킬 라벨 **이름**을 한 번에 읽는다(조회 전용).

    자산마다 묻지 않는다 — 묶음이 커지면 질의 수가 자산 수를 따라간다. 라벨이 없는 자산은 키가 아예 없고,
    화면은 그것을 "미분류"로 읽는다.

    Args:
        conn: DB 커넥션.
        asset_ids: 조회할 자산 id 목록. 빈 목록이면 DB 를 건드리지 않고 빈 dict.
        skill_codes: 읽을 스킬 코드들(예: ``["content_form"]``). 빈 목록이면 빈 dict — 어느 스킬을 형식 축으로
            보이나는 화면 정책이라 호출자가 반드시 정한다(전부를 뜻하는 기본값을 두지 않는다).

    Returns:
        ``{asset_id: [라벨 이름…]}``. 이름은 스킬 코드 순 → 스킬 정의 순서다. 미부여(``unassigned``)는 없다.
    """
    ids = [str(a) for a in asset_ids if str(a)]
    skills = [str(s) for s in skill_codes if str(s)]
    if not ids or not skills:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LABEL_NAMES_OF_ASSETS_SQL, {"skills": skills, "ids": ids})
        rows = cur.fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["asset_id"]), []).append(str(r["name"]))
    return out
