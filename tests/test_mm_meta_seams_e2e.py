"""095 FR-1·FR-2 — 개체 화면 seam 이 **데모 라우트의 직접 SQL 과 같은 결과**를 내는지 실 DB 로 대조한다.

RUN_DB_E2E 게이트(실 PostgreSQL 필요 · LLM 0 · 쓰기 0). 데모(`routes_mm_meta_demo.py` · 서비스 로컬 브랜치
`demo/mm-meta-reference`)의 SQL 을 **글자 그대로** 여기 옮겨 두고, 같은 파라미터로 코어 seam 과 비교한다 —
정식화의 조건 "응답 JSON == 데모 응답"(spec 095 SC-01)을 코어 층에서 먼저 증명한다. 데모 SQL 사본은 이
테스트에만 존재한다(코어 코드에는 사본을 두지 않는다).
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

_RUN_DB_E2E = os.environ.get("RUN_DB_E2E")

# ── 데모 SQL 사본(2026-09-02 tip · 갈래 축 = 개체 라벨 · 자산 라벨 필터 없음) ───────────────────────
_DEMO_LIST_SQL = """
    SELECT n.entity_type, n.entity_uid,
           COALESCE(n.canonical->>'name', n.entity_uid) AS name,
           COALESCE(n.canonical->>'source', 'auto')     AS source,
           n.canonical->>'description'                  AS description,
           COUNT(DISTINCT ge.src_node)                  AS confirmed_count,
           (SELECT COUNT(DISTINCT ge2.src_node)
              FROM graph_edge ge2
              JOIN relation_kind rk2 ON rk2.relation_kind_id = ge2.relation_kind_id
             WHERE ge2.dst_node = n.node_id
               AND rk2.kind_code = 'mm_member'
               AND ge2.status IN ('active', 'proposed')) AS total_count,
           ARRAY_AGG(DISTINCT a.modality)               AS modalities,
           ARRAY_AGG(DISTINCT substring(ge.reason from 'kw=([^|]*)'))
               FILTER (WHERE ge.reason IS NOT NULL)     AS keywords,
           ARRAY_AGG(DISTINCT t.topic_ko)
               FILTER (WHERE t.topic_ko IS NOT NULL)    AS topics,
           ARRAY_AGG(DISTINCT fl.form_name)
               FILTER (WHERE fl.form_name IS NOT NULL)  AS forms,
           (SELECT ARRAY_AGG(DISTINCT (elb->>'name') ORDER BY (elb->>'name'))
              FROM entity_mm_skill_label el
              JOIN mm_skill es ON es.skill_code = el.skill_code
              CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
             WHERE el.entity_type = n.entity_type
               AND el.entity_uid = n.entity_uid
               AND (elb->>'code') = el.label_code
               AND el.label_code <> 'unassigned')          AS areas
      FROM node n
      JOIN graph_edge ge   ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
      JOIN node sn         ON sn.node_id = ge.src_node
      JOIN asset a         ON a.asset_id = sn.asset_id
      LEFT JOIN asset_topic t ON t.asset_id = sn.asset_id
      LEFT JOIN (
            SELECT l.asset_id, (lb->>'name') AS form_name
              FROM asset_mm_skill_label l
              JOIN mm_skill s ON s.skill_code = l.skill_code
              CROSS JOIN LATERAL jsonb_array_elements(s.labels) AS lb
             WHERE l.skill_code = 'content_form'
               AND (lb->>'code') = l.label_code
           ) fl ON fl.asset_id = sn.asset_id
     WHERE n.node_kind = 'entity'
       AND rk.kind_code = 'mm_member'
       AND ge.status IN ('active', 'proposed')
       AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)
       AND (%(labels)s::text[] IS NULL OR (
             CASE WHEN %(label_axis)s = 'entity' THEN EXISTS (
               SELECT 1
                 FROM entity_mm_skill_label el
                 JOIN mm_skill es ON es.skill_code = el.skill_code
                 CROSS JOIN LATERAL jsonb_array_elements(es.labels) AS elb
                WHERE el.entity_type = n.entity_type
                  AND el.entity_uid = n.entity_uid
                  AND (elb->>'code') = el.label_code
                  AND (elb->>'name') = ANY(%(labels)s)
                GROUP BY el.entity_type, el.entity_uid
               HAVING COUNT(DISTINCT (elb->>'name')) = %(label_n)s
             ) ELSE FALSE END
           ))
     GROUP BY n.node_id, n.entity_type, n.entity_uid, n.canonical
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
     ORDER BY confirmed_count DESC, n.entity_uid ASC
     LIMIT %(limit)s
"""


def _demo_exposed(*, with_type: bool, with_areas: bool) -> str:
    type_cond = "AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)" if with_type else ""
    area_cond = (
        """
       AND (%(areas)s::text[] IS NULL OR EXISTS (
             SELECT 1 FROM entity_mm_skill_label el
               JOIN mm_skill s ON s.skill_code = el.skill_code
               CROSS JOIN LATERAL jsonb_array_elements(s.labels) AS lb
              WHERE el.entity_type = n.entity_type
                AND el.entity_uid = n.entity_uid
                AND (lb->>'code') = el.label_code
                AND (lb->>'name') = ANY(%(areas)s)
              GROUP BY el.entity_type, el.entity_uid
             HAVING COUNT(DISTINCT (lb->>'name')) = %(area_n)s))
        """
        if with_areas else ""
    )
    return f"""
    SELECT n.entity_type, n.entity_uid
      FROM node n
      JOIN graph_edge ge    ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
     WHERE n.node_kind = 'entity' AND rk.kind_code = 'mm_member'
       AND ge.status IN ('active', 'proposed')
       {type_cond}
       {area_cond}
     GROUP BY n.entity_type, n.entity_uid
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
    """


_DEMO_TYPE_COUNTS_SQL = """
SELECT entity_type, COUNT(*) FROM (
    SELECT n.entity_type
      FROM node n
      JOIN graph_edge ge ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
     WHERE n.node_kind = 'entity'
       AND rk.kind_code = 'mm_member'
       AND ge.status IN ('active', 'proposed')
     GROUP BY n.node_id, n.entity_type
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
) t
 GROUP BY entity_type
"""

_DEMO_AREA_COUNTS_SQL = """
WITH ex AS ({exposed}),
lab AS (
    SELECT s.skill_code, s.name AS skill,
           (lb->>'code') AS code, (lb->>'name') AS name, ord
      FROM mm_skill s,
           LATERAL jsonb_array_elements(s.labels) WITH ORDINALITY AS t(lb, ord)
     WHERE s.status = 'active' AND s.skill_code <> 'mm_meta_type'
),
hit AS (
    SELECT el.skill_code, el.label_code,
           COUNT(*) AS n
      FROM (
        SELECT DISTINCT el2.skill_code, el2.label_code,
               el2.entity_type, el2.entity_uid
          FROM entity_mm_skill_label el2
          JOIN ex ON ex.entity_type = el2.entity_type
                 AND ex.entity_uid = el2.entity_uid
      ) el
     GROUP BY 1, 2
)
SELECT lab.name, lab.skill, lab.skill_code, COALESCE(hit.n, 0) AS n
  FROM lab
  LEFT JOIN hit ON hit.skill_code = lab.skill_code
              AND hit.label_code = lab.code
 WHERE lab.code <> 'unassigned'
 ORDER BY n DESC, lab.skill_code, lab.ord
"""

_DEMO_COMBO_ASSETS_SQL = """
WITH ex AS (
    SELECT n.entity_type, n.entity_uid, n.node_id
      FROM node n
      JOIN graph_edge ge    ON ge.dst_node = n.node_id
      JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
     WHERE n.node_kind = 'entity' AND rk.kind_code = 'mm_member'
       AND ge.status IN ('active', 'proposed')
       AND (%(etype)s::text IS NULL OR n.entity_type = %(etype)s)
     GROUP BY n.entity_type, n.entity_uid, n.node_id
    HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
),
picked AS (
    SELECT ex.* FROM ex
     WHERE %(names)s::text[] IS NULL OR EXISTS (
           SELECT 1 FROM entity_mm_skill_label el
             JOIN mm_skill s ON s.skill_code = el.skill_code
             CROSS JOIN LATERAL jsonb_array_elements(s.labels) AS lb
            WHERE el.entity_type = ex.entity_type
              AND el.entity_uid = ex.entity_uid
              AND (lb->>'code') = el.label_code
              AND (lb->>'name') = ANY(%(names)s)
            GROUP BY el.entity_type, el.entity_uid
           HAVING COUNT(DISTINCT (lb->>'name')) = %(label_n)s)
)
SELECT DISTINCT a.asset_id::text, a.modality, a.fs_path, COALESCE(a.file_size, 0)
  FROM picked
  JOIN graph_edge ge    ON ge.dst_node = picked.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
  JOIN node sn          ON sn.node_id = ge.src_node
  JOIN asset a          ON a.asset_id = sn.asset_id
 WHERE rk.kind_code = 'mm_member' AND ge.status IN ('active', 'proposed')
   AND a.status = 'registered'
   {video}
 -- ⚠️ 데모 원문은 `ORDER BY a.asset_id` 였고 PG17 이 거부한다(DISTINCT 의 정렬 키는 선택 목록에 있어야 한다:
 --    InvalidColumnReference). 즉 데모의 `/mm-meta/bundle` 은 이 질의에서 500 이었다. 대조를 위해 **행 집합을
 --    바꾸지 않는 최소 수정**(선택 목록 첫 컬럼으로 정렬)만 했다.
 ORDER BY 1
"""

_DEMO_FORM_LABELS_SQL = """
WITH names AS (
    SELECT (lb->>'code') AS code, (lb->>'name') AS name, ord
      FROM mm_skill s,
           LATERAL jsonb_array_elements(s.labels) WITH ORDINALITY AS t(lb, ord)
     WHERE s.skill_code = %(skill)s
)
SELECT l.asset_id::text, n.name
  FROM asset_mm_skill_label l
  JOIN names n ON n.code = l.label_code
 WHERE l.skill_code = %(skill)s
   AND l.asset_id::text = ANY(%(ids)s)
 ORDER BY l.asset_id, n.ord
"""


def _demo_shape(r) -> dict:
    """데모 `list_mm_meta._query` 의 파이썬 정형(원문 그대로)."""
    return {
        "entity_type": str(r[0]), "entity_uid": str(r[1]), "name": str(r[2]), "source": str(r[3]),
        "description": (r[4] or None), "confirmed_count": int(r[5]), "total_count": int(r[6]),
        "modalities": sorted(str(m) for m in (r[7] or []) if m),
        "keywords": sorted((str(k) for k in (r[8] or []) if k), key=lambda x: (len(x), x))[:6],
        "topics": sorted(str(t) for t in (r[9] or []) if t),
        "forms": sorted(str(f) for f in (r[10] or []) if f),
        "areas": [str(x) for x in (r[11] or []) if x],
    }


def _core_as_demo(row: dict) -> dict:
    """코어 seam 행에 백엔드가 할 정형(키워드 상위 6·짧은 것부터)을 적용해 데모 모양으로."""
    return {
        **{k: row[k] for k in ("entity_type", "entity_uid", "name", "source", "description",
                              "confirmed_count", "total_count", "modalities", "topics", "forms", "areas")},
        "keywords": sorted(row["keywords"], key=lambda x: (len(x), x))[:6],
    }


@unittest.skipUnless(_RUN_DB_E2E, "RUN_DB_E2E 미설정 — 실 DB e2e skip")
class TestMmMetaSeamsParityWithDemo(unittest.TestCase):
    """코어 seam == 데모 SQL(같은 파라미터) — 실 dev DB."""

    @classmethod
    def setUpClass(cls) -> None:
        from dotenv import load_dotenv

        from src.config.settings import init_settings
        from src.database.postgres_util import PostgresUtil

        dp = Path(__file__).resolve().parents[1] / ".env.dev"
        if dp.is_file():
            load_dotenv(dotenv_path=dp, override=False)
        init_settings("dev")
        cls.db = PostgresUtil()
        cls.db.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.__exit__(None, None, None)

    _LIST_CASES = [
        (None, None, 3, 200), ("장소", None, 3, 200), ("인물", None, 2, 50),
        (None, ["여행·명소"], 2, 200), ("음식", ["한식"], 2, 500), (None, ["한식", "발효·저장식품"], 2, 200),
        (None, ["없는갈래"], 2, 200),
    ]

    def test_list_entities_equals_demo(self) -> None:
        from src.relations.graph_query import list_entities

        checked = 0
        with self.db.transaction() as conn:
            for etype, areas, minsize, limit in self._LIST_CASES:
                with self.subTest(etype=etype, areas=areas, minsize=minsize):
                    demo = [_demo_shape(r) for r in conn.execute(_DEMO_LIST_SQL, {
                        "etype": etype, "minsize": minsize, "limit": limit,
                        "labels": areas or None, "label_n": len(set(areas or [])), "label_axis": "entity",
                    }).fetchall()]
                    core = [_core_as_demo(r) for r in list_entities(
                        conn, entity_type=etype, area_names=areas, min_bundle_size=minsize, limit=limit,
                        form_skill_codes=["content_form"])]
                    self.assertEqual(core, demo)
                    checked += len(demo)
        self.assertGreater(checked, 0, "대조한 개체가 0건 — dev DB 에 개체가 없다")

    def test_count_by_type_equals_demo(self) -> None:
        from src.relations.graph_query import count_entities_by_type

        with self.db.transaction() as conn:
            for minsize in (2, 3):
                demo = dict(conn.execute(_DEMO_TYPE_COUNTS_SQL, {"minsize": minsize}).fetchall())
                self.assertEqual(count_entities_by_type(conn, min_bundle_size=minsize),
                                 {str(k): int(v) for k, v in demo.items()})

    def test_count_by_area_equals_demo(self) -> None:
        from src.relations.graph_query import count_entities_by_area

        with self.db.transaction() as conn:
            for etype, areas in ((None, None), ("장소", None), (None, ["여행·명소"]), ("음식", ["한식"]),
                                 ("인물", ["영화", "음악"])):
                with self.subTest(etype=etype, areas=areas):
                    sql = _DEMO_AREA_COUNTS_SQL.format(exposed=_demo_exposed(with_type=True, with_areas=True))
                    demo = [{"name": str(r[0]), "skill": str(r[1]), "skill_code": str(r[2]), "count": int(r[3])}
                            for r in conn.execute(sql, {"minsize": 3, "etype": etype,
                                                        "areas": areas or None,
                                                        "area_n": len(set(areas or []))}).fetchall()]
                    core = count_entities_by_area(conn, entity_type=etype, area_names=areas, min_bundle_size=3)
                    self.assertEqual(core, demo)
                    self.assertTrue(any(x["count"] == 0 for x in core) or not core,
                                    "0건 라벨이 하나도 없다 — 데이터가 달라졌으면 케이스를 조정")

    def test_assets_of_entities_equals_demo(self) -> None:
        from src.relations.graph_query import assets_of_entities

        with self.db.transaction() as conn:
            for etype, areas, excl in ((None, None, False), ("장소", None, True), (None, ["여행·명소"], False),
                                       ("음식", ["한식"], True)):
                with self.subTest(etype=etype, areas=areas, excl=excl):
                    sql = _DEMO_COMBO_ASSETS_SQL.format(video="AND a.modality <> 'video'" if excl else "")
                    demo = [{"asset_id": str(r[0]), "modality": str(r[1] or ""), "fs_path": r[2],
                             "file_size": int(r[3])}
                            for r in conn.execute(sql, {"etype": etype, "minsize": 3, "names": areas or None,
                                                        "label_n": len(set(areas or []))}).fetchall()]
                    core = assets_of_entities(conn, entity_type=etype, area_names=areas, min_bundle_size=3,
                                              exclude_video=excl)
                    self.assertEqual(core, demo)

    def test_label_names_equals_demo_form_labels(self) -> None:
        from src.mm_classify.read import label_names_of_assets

        with self.db.transaction() as conn:
            ids = [str(r[0]) for r in conn.execute(
                "SELECT DISTINCT asset_id FROM asset_mm_skill_label WHERE skill_code='content_form'"
                " ORDER BY asset_id LIMIT 300").fetchall()]
            self.assertTrue(ids)
            demo: dict[str, list[str]] = {}
            for aid, name in conn.execute(_DEMO_FORM_LABELS_SQL, {"skill": "content_form", "ids": ids}).fetchall():
                demo.setdefault(str(aid), []).append(str(name))
            self.assertEqual(label_names_of_assets(conn, ids, skill_codes=["content_form"]), demo)

    def test_bundle_description_equals_demo_lookup(self) -> None:
        from src.relations.graph_query import list_entities, mm_meta_bundle

        with self.db.transaction() as conn:
            for ent in list_entities(conn, min_bundle_size=3, limit=5):
                desc = conn.execute(
                    "SELECT canonical->>'description' FROM node"
                    " WHERE node_kind='entity' AND entity_type=%s AND entity_uid=%s",
                    (ent["entity_type"], ent["entity_uid"])).fetchone()
                bundle = mm_meta_bundle(conn, entity_type=ent["entity_type"], entity_uid=ent["entity_uid"])
                self.assertEqual(bundle["description"], (desc[0] if desc else None) or None)


if __name__ == "__main__":
    unittest.main()
