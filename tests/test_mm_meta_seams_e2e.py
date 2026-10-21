"""095 FR-1·FR-2 — 개체 화면 seam 이 **데모 라우트의 직접 SQL 과 같은 결과**를 내는지 실 DB 로 대조한다.

RUN_DB_E2E 게이트(실 PostgreSQL 필요 · LLM 0 · 쓰기 0). 데모(`routes_mm_meta_demo.py` · 서비스 로컬 브랜치
`demo/mm-meta-reference`)의 SQL 을 **글자 그대로** 여기 옮겨 두고, 같은 파라미터로 코어 seam 과 비교한다 —
정식화의 조건 "응답 JSON == 데모 응답"(spec 095 SC-01)을 코어 층에서 먼저 증명한다. 데모 SQL 사본은 이
테스트에만 존재한다(코어 코드에는 사본을 두지 않는다).
개체 칩 한 질의(``count_entity_facets``)의 칩 숫자가 그 칩을 눌렀을 때 목록이 세는 결과 수와 같은지도 여기서
대조한다.
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


# 개체별 보유 자료 유형 — 백엔드 자료 유형 허용목록(캐시 질의)과 같은 정의: 소속 엣지 · 상태 active+proposed ·
# 구성 자산의 자료 유형. 노출 임계 · 종류 · 갈래는 걸지 않는다(허용목록을 받는 ``count_entities`` 가 건다).
_ENTITY_MODALITIES_SQL = """
SELECT n.entity_type, n.entity_uid, ARRAY_AGG(DISTINCT a.modality) AS modalities
  FROM node n
  JOIN graph_edge ge    ON ge.dst_node = n.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
  JOIN node sn          ON sn.node_id = ge.src_node
  JOIN asset a          ON a.asset_id = sn.asset_id
 WHERE n.node_kind = 'entity' AND rk.kind_code = 'mm_member'
   AND ge.status IN ('active', 'proposed')
 GROUP BY n.node_id, n.entity_type, n.entity_uid
"""

# 화면이 칩으로 세는 자료 유형(백엔드 화면 정책과 같은 값).
_FACET_MODALITY_CODES = ["text", "image", "video", "audio"]

# 종류 칩의 기대 범위 · 순서 — 함수가 줄 세우는 열(``node.entity_type``)을 DB 문자열 비교 규칙으로 줄 세운다(파이썬
# 정렬과 갈릴 수 있어 DB 에 맡긴다). 노출 개체가 없는 종류도 담긴다 — 눌렀을 때 0 이면 칩에 키가 없어야 한다.
_ENTITY_TYPES_SQL = "SELECT DISTINCT entity_type FROM node WHERE node_kind = 'entity' ORDER BY 1"


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


@unittest.skipUnless(_RUN_DB_E2E, "RUN_DB_E2E 미설정 — 실 DB e2e skip")
class TestEntityFacetsMatchClickResults(unittest.TestCase):
    """``count_entity_facets`` 의 칩 숫자 == 그 칩을 눌렀을 때 목록이 세는 결과 수(``count_entities``) — 실 dev DB.

    클릭 규칙은 화면과 같다. 종류 칩 ``t`` 는 종류만 ``t`` 로 **바꾸고** 갈래 · 고른 자료 유형은 그대로 둔 결과,
    갈래 칩 ``a`` 는 지금 갈래에 ``a`` 를 **더한** 결과, 자료 유형 칩 ``m`` 은 「고른 유형 ∪ {m}」을 모두 가진 개체만
    허용목록으로 준 결과다. 예: 갈래 「한식」을 고른 채 종류 칩 「인물」의 숫자 = 「한식」인 인물 수. 눌렀을 때 0 인
    종류는 칩에 키가 없어야 한다(함수 계약).
    바뀌면 안 되는 부분도 함께 본다 — 갈래 · 자료 유형을 고르지 않았으면 종류 칩 == ``count_entities_by_type``,
    자료 유형을 고르지 않았으면 갈래 칩 == ``count_entities_by_area``(순서까지).
    설계 배경: `specs/108-entity-facets-own-axis`
    """

    maxDiff = None  # 어긋나면 어느 칩이 몇으로 갈렸는지 전부 보여야 원인을 가른다

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

    def test_chip_counts_equal_click_results(self) -> None:
        from src.relations.graph_query import (
            count_entities,
            count_entities_by_area,
            count_entities_by_type,
            count_entity_facets,
        )

        with self.db.transaction() as conn:
            mods_of = {(str(t), str(u)): frozenset(str(m) for m in (ms or []) if m)
                       for t, u, ms in conn.execute(_ENTITY_MODALITIES_SQL).fetchall()}
            all_types = [str(r[0]) for r in conn.execute(_ENTITY_TYPES_SQL).fetchall()]

            def allow(want: set[str]) -> set[tuple[str, str]] | None:
                # 자료 유형을 하나도 고르지 않았으면 허용목록을 주지 않는다 — 목록 화면과 같다(빈 허용목록은 0건이다).
                if not want:
                    return None
                return {key for key, have in mods_of.items() if want <= have}

            def click(e: str | None, a: list[str] | None, want: set[str], *, ms: int) -> int:
                # 그 조건으로 목록을 띄웠을 때의 총계(화면 「N건」) — 칩 숫자가 맞춰야 할 정답.
                return count_entities(conn, entity_type=e, area_names=a, min_bundle_size=ms, uid_allow=allow(want))

            # 조건 값은 DB 에서 고른다 — 가장 큰 종류 · 가장 큰 갈래 · 그 갈래와 함께 붙은 다음 갈래 · 가장 흔한 자료 유형.
            types = count_entities_by_type(conn, min_bundle_size=3)
            self.assertTrue(types, "노출 개체가 0건 — dev DB 에 개체가 없다")
            etype = max(types, key=lambda t: (types[t], t))
            areas = [a["name"] for a in count_entities_by_area(conn, min_bundle_size=3) if a["count"] > 0]
            self.assertTrue(areas, "갈래가 붙은 노출 개체가 0건")
            area1 = areas[0]
            with_area1 = [a["name"] for a in count_entities_by_area(conn, area_names=[area1], min_bundle_size=3)
                          if a["count"] > 0 and a["name"] != area1]
            self.assertTrue(with_area1, f"「{area1}」 와 함께 붙은 다른 갈래가 없다")
            area2 = with_area1[0]
            mod1 = max(_FACET_MODALITY_CODES, key=lambda m: (click(None, None, {m}, ms=3), m))
            others = [c for c in _FACET_MODALITY_CODES if c != mod1]
            # 세 조건을 함께 걸 종류 — 「area1 + mod1」 안에서 가장 많은 종류(0 이면 갈래 · 자료 유형 칩이 모두 0 이라
            # 종류 조건이 그 두 축에 걸리는지 가려내지 못한다).
            in_both = {t: click(t, [area1], {mod1}, ms=3) for t in all_types}
            etype3 = max(in_both, key=lambda t: (in_both[t], t))
            self.assertGreater(in_both[etype3], 0, f"「{area1}」 + {mod1} 인 노출 개체가 0건")

            cases = [  # (이름, 종류, 갈래, 고른 자료 유형, 칩 자료 유형)
                ("조건 없음", None, None, [], _FACET_MODALITY_CODES),
                ("종류 하나", etype, None, [], _FACET_MODALITY_CODES),
                ("갈래 하나", None, [area1], [], _FACET_MODALITY_CODES),
                ("갈래 둘", None, [area1, area2], [], _FACET_MODALITY_CODES),
                ("자료 유형 하나", None, None, [mod1], _FACET_MODALITY_CODES),
                ("갈래+자료 유형", None, [area1], [mod1], _FACET_MODALITY_CODES),
                ("종류+갈래", etype3, [area1], [], _FACET_MODALITY_CODES),
                ("종류+자료 유형", etype3, None, [mod1], _FACET_MODALITY_CODES),
                ("종류+갈래+자료 유형", etype3, [area1], [mod1], _FACET_MODALITY_CODES),
                ("고른 유형이 칩 목록 밖", None, None, [mod1], others),
            ]
            # 대조가 새 뜻을 가려내는지 — 옛 뜻(종류 칩 = 조건 없이 · 갈래 칩 = 자료 유형 없이 센 수)이 클릭 결과와
            # 갈리는 조건 수, 조건 없이는 보이던 종류가 눌렀을 때 0 이라 키가 빠져야 하는 조건 수. 0 이면 옛 함수로
            # 돌아가도 이 대조가 통과한다.
            old_differs = {"types": 0, "areas": 0}
            zero_type_cases = 0
            for minsize in (2, 3):
                labels = count_entities_by_area(conn, min_bundle_size=minsize)  # 갈래 칩 전체(0건 포함)
                for name, et, ar, picked, codes in cases:
                    with self.subTest(case=name, minsize=minsize):
                        chosen = set(picked)
                        clicked = {t: click(t, ar, chosen, ms=minsize) for t in all_types}  # 종류를 바꾼다
                        want_types = {t: n for t, n in clicked.items() if n}
                        want_areas = {lb["name"]: click(et, sorted({*(ar or []), lb["name"]}), chosen, ms=minsize)
                                      for lb in labels}                                       # 갈래를 더한다
                        want_mods = {m: click(et, ar, {*chosen, m}, ms=minsize) for m in codes}  # 유형을 더한다
                        facets = count_entity_facets(conn, entity_type=et, area_names=ar, modalities=picked,
                                                     min_bundle_size=minsize, modality_codes=codes)
                        # 옛 뜻의 값 — 아래 단언보다 먼저 센다(단언이 실패해도 「갈리는 조건」 수는 데이터로 남게).
                        by_type = count_entities_by_type(conn, min_bundle_size=minsize)
                        by_area = count_entities_by_area(conn, entity_type=et, area_names=ar, min_bundle_size=minsize)
                        old_differs["types"] += want_types != by_type
                        old_differs["areas"] += want_areas != {a["name"]: a["count"] for a in by_area}
                        zero_type_cases += bool(set(by_type) - set(want_types))

                        # 종류 칩 — 수 · 0 인 종류의 키 없음 · DB 이름순
                        self.assertEqual(facets["types"], want_types)
                        self.assertEqual(list(facets["types"]), list(want_types))
                        # 갈래 칩 — 이름이 겹치면 이름으로 누르는 클릭과 1:1 이 아니어서 대조가 성립하지 않는다
                        got_areas = {a["name"]: a["count"] for a in facets["areas"]}
                        self.assertEqual(len(got_areas), len(facets["areas"]), "갈래 칩 이름이 겹친다")
                        self.assertEqual(got_areas, want_areas)
                        self.assertEqual({(a["name"], a["skill"], a["skill_code"]) for a in facets["areas"]},
                                         {(lb["name"], lb["skill"], lb["skill_code"]) for lb in labels})
                        counts = [a["count"] for a in facets["areas"]]
                        self.assertEqual(counts, sorted(counts, reverse=True))  # 건수 내림차순
                        # 자료 유형 칩 — 수 · ``modality_codes`` 순서
                        self.assertEqual(facets["modalities"], want_mods)
                        self.assertEqual(list(facets["modalities"]), list(codes))
                        self.assertTrue(want_types, "종류 칩이 모두 0 — 대조가 무의미하다(조건 값을 다시 고를 것)")
                        self.assertGreater(sum(want_mods.values()), 0,
                                           "자료 유형 칩이 모두 0 — 대조가 무의미하다(조건 값을 다시 고를 것)")

                        # 바뀌면 안 되는 부분 — 갈래 · 자료 유형이 없으면 종류 칩, 자료 유형이 없으면 갈래 칩이 옛 값 그대로
                        if not ar and not picked:
                            self.assertEqual(facets["types"], by_type)
                            self.assertEqual(list(facets["types"]), list(by_type))
                        if not picked:
                            self.assertEqual(facets["areas"], by_area)  # 순서까지

            # 아래 가드는 데이터 가드다 — 함수가 옛 뜻으로 돌아가면 위 칩 단언이 먼저 실패한다. 칩 단언이 모두 통과했는데
            # 가드만 실패하면 회귀가 아니라 dev DB 데이터가 바뀐 것이니 조건 값을 고르는 방식을 다시 본다.
            hint = " — 칩 단언은 통과했으므로 회귀가 아니라 데이터 문제다(조건 값을 다시 고를 것)"
            self.assertGreater(old_differs["types"], 0, "종류 칩이 옛 뜻과 갈리는 조건이 없다" + hint)
            self.assertGreater(old_differs["areas"], 0, "갈래 칩이 옛 뜻과 갈리는 조건이 없다" + hint)
            self.assertGreater(zero_type_cases, 0, "눌렀을 때 0 인 종류가 생긴 조건이 없어 키 없음 계약을 못 본다" + hint)


@unittest.skipUnless(_RUN_DB_E2E, "RUN_DB_E2E 미설정 — 실 DB e2e skip")
class TestListWithTotalMatchesListAndCount(unittest.TestCase):
    """``list_entities_with_total`` == ``list_entities`` + ``count_entities``(같은 조건) — 실 dev DB · 끝까지 넘기며.

    쪽마다 행(순서 · 구성 자산 수 · 카드 재료)과 총계를 대조하고, 끝을 넘긴 책갈피(빈 쪽)의 총계와 0건 조건도 본다.
    쪽 크기 97 은 동점 무더기 한가운데서 끊기게 일부러 고른 홀수다.
    설계 배경: `specs/113-entity-list-with-total`
    """

    maxDiff = None

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

    def test_pages_and_total_equal_list_plus_count(self) -> None:
        from src.relations.graph_query import (
            count_entities,
            count_entities_by_area,
            count_entities_by_type,
            list_entities,
            list_entities_with_total,
        )

        with self.db.transaction() as conn:
            mods_of = {(str(t), str(u)): frozenset(str(m) for m in (ms or []) if m)
                       for t, u, ms in conn.execute(_ENTITY_MODALITIES_SQL).fetchall()}
            types = count_entities_by_type(conn, min_bundle_size=3)
            self.assertTrue(types, "노출 개체가 0건 — dev DB 에 개체가 없다")
            etype = max(types, key=lambda t: (types[t], t))
            areas = [a["name"] for a in count_entities_by_area(conn, min_bundle_size=3) if a["count"] > 0]
            self.assertTrue(areas, "갈래가 붙은 노출 개체가 0건")
            image = {key for key, have in mods_of.items() if "image" in have}
            first = set(sorted(image)[:5])  # 앞세울 개체 — 총계는 그대로여야 한다

            cases = [  # (이름, 조건)
                ("조건 없음", {}),
                ("종류 하나", {"entity_type": etype}),
                ("갈래 하나", {"area_names": [areas[0]]}),
                ("종류+갈래", {"entity_type": etype, "area_names": [areas[0]]}),
                ("허용 목록(이미지)", {"uid_allow": image}),
                ("허용 목록+앞세우기", {"uid_allow": image, "uid_first": first, "uid_semantic": set(sorted(image)[5:9])}),
                ("빈 허용 목록(0건)", {"uid_allow": set()}),
                ("상태 지정", {"statuses": ["active", "proposed"], "entity_type": etype}),
                ("상태 active 만", {"statuses": ["active"]}),
            ]
            multi_page = 0
            for name, cond in cases:
                with self.subTest(case=name):
                    base = {"min_bundle_size": 3, "limit": 97, "form_skill_codes": ["content_form"], **cond}
                    count_kw = {k: v for k, v in cond.items()
                                if k in ("entity_type", "area_names", "uid_allow", "statuses")}
                    want_total = count_entities(conn, min_bundle_size=3, **count_kw)
                    bookmark: dict = {}
                    seen = 0
                    pages = 0
                    while True:
                        want_rows = list_entities(conn, **base, **bookmark)
                        rows, total = list_entities_with_total(conn, **base, **bookmark)
                        self.assertEqual(want_rows, rows, f"{name} · {pages + 1}쪽 행이 다르다")
                        self.assertEqual(want_total, total, f"{name} · {pages + 1}쪽 총계가 다르다")
                        seen += len(rows)
                        pages += 1
                        if len(rows) < base["limit"]:
                            break
                        last = rows[-1]
                        key = (last["entity_type"], last["entity_uid"])
                        tier = 2 if key in cond.get("uid_first", set()) else 1 if key in cond.get("uid_semantic", set()) else 0
                        bookmark = {"after_tier": tier, "after_count": last["confirmed_count"],
                                    "after_uid": last["entity_uid"], "after_type": last["entity_type"]}
                    self.assertEqual(want_total, seen, f"{name} — 끝까지 넘긴 행 수 ≠ 총계")
                    multi_page += pages > 1
                    # 끝을 넘긴 책갈피 — 빈 쪽이라 총계를 따로 센다.
                    beyond = {"after_tier": -1, "after_count": -1, "after_uid": "", "after_type": ""}
                    self.assertEqual(([], want_total), list_entities_with_total(conn, **base, **beyond))
            self.assertGreater(multi_page, 0, "여러 쪽으로 넘어간 조건이 없다 — 이어 받기 쪽 총계를 못 봤다")


if __name__ == "__main__":
    unittest.main()
