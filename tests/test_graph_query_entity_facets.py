"""``count_entity_facets``(개체 칩 건수 — 종류 · 갈래 · 자료 유형을 한 질의로) mock conn 단위 테스트(DB 불필요).

봉인하는 것: ① 없는 조건은 문장에서 뺀다(``IS NULL OR`` 꼴을 두지 않는다) ② 있는 조건은 정해진 조각 · 바인딩으로
들어간다 ③ ``base`` · ``scoped`` · ``have`` · ``amatch`` 가 ``MATERIALIZED`` 다 ④ 반환은 축마다 SQL 이 준 ``ord`` 순서다.
기존 세 호출과 결과가 같은지는 실 DB 대조(``tests/test_mm_meta_seams_e2e.py``)가 본다.
설계 배경: `specs/107-entity-facets`
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import MagicMock

from src.mm_classify.model import NON_CLASSIFY_SKILL_CODES
from src.relations import graph_query as gq
from src.relations.schema import MM_MEMBER_KIND_CODE

_CODES = ["text", "image", "video", "audio"]

# 「값이 비면 조건 무시」 꼴 — ``… IS NULL OR …`` 과 ``… OR %(x)s IS NULL`` 을 대소문자 · 공백과 무관하게 잡는다.
_NULL_BYPASS_RX = re.compile(r"(?i)\bis\s+null\s+or\b|\bor\s+%\(\w+\)s\s+is\s+null")


def _conn_returning(rows: list[dict]):
    """``conn.cursor(row_factory=dict_row)`` 컨텍스트매니저를 흉내내는 mock conn."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _sql_and_params(cur) -> tuple[str, dict]:
    sql, params = cur.execute.call_args.args
    return " ".join(sql.split()), params


def _call(rows: list[dict] | None = None, **kwargs):
    """기본 인자(임계 3 · 자료 유형 4종)로 부르고 ``(반환, 정규화 SQL, 바인딩, cur)`` 를 돌려준다."""
    conn, cur = _conn_returning(rows or [])
    kwargs.setdefault("min_bundle_size", 3)
    kwargs.setdefault("modality_codes", list(_CODES))
    out = gq.count_entity_facets(conn, **kwargs)
    sql, params = _sql_and_params(cur)
    return out, sql, params, cur


class TestEntityFacetsAssembly(unittest.TestCase):
    """조건별 조립 — 없는 조건은 문장에서 빠지고, 있는 조건은 정해진 조각 · 바인딩으로 들어간다."""

    def test_no_conditions_leave_every_fragment_out(self) -> None:
        """조건이 없으면 종류 · 갈래 · 고른 자료 유형 조각이 하나도 없고, ``IS NULL OR`` 꼴도 없다.

        「값이 비면 조건 무시」를 SQL 안에 두면 같은 연결에서 반복 실행할 때 재사용 계획에서 크게 느려진다.
        """
        _out, sql, p, cur = _call()
        cur.execute.assert_called_once()  # 칩 세 축을 질의 한 번으로
        self.assertNotIn("amatch", sql)
        self.assertNotIn("b.entity_type = %(etype)s", sql)
        self.assertNotIn("%(etype)s", sql)
        self.assertNotIn("%(areas)s", sql)
        self.assertNotIn("%(picked)s", sql)
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))
        self.assertIn("ARRAY[c.code]", sql)  # 고른 유형이 없으면 칩 유형 하나만 가졌는지 본다
        self.assertNotIn("{", sql)  # 자리(``{…}``)가 전부 채워졌다
        # 기본 바인딩 — 노출 정의(소속 kind · 상태 · 임계)와 셀 자료 유형 · 예약 스킬은 늘 리터럴이 아니라 바인딩
        self.assertEqual(p["kind"], MM_MEMBER_KIND_CODE)
        self.assertEqual(p["statuses"], ["active", "proposed"])
        self.assertEqual(p["minsize"], 3)
        self.assertEqual(p["codes"], _CODES)
        self.assertEqual(p["reserved"], sorted(NON_CLASSIFY_SKILL_CODES))
        # 소속 엣지 상태는 노출 개체(base)와 자료 유형 판정(have) 두 곳 모두 바인딩으로 건다
        # (``'active'`` 리터럴은 스킬 정의 상태 ``s.status`` 에만 있다).
        self.assertEqual(sql.count("ge.status = ANY(%(statuses)s)"), 2)
        self.assertNotIn("ge.status = 'active'", sql)
        for key in ("etype", "areas", "area_n", "picked"):
            self.assertNotIn(key, p, f"조건이 없는데 {key} 가 바인딩됐다")

    def test_entity_type_adds_scope_condition(self) -> None:
        _out, sql, p, _cur = _call(entity_type="장소")
        self.assertIn("WHERE TRUE AND b.entity_type = %(etype)s", sql)
        self.assertEqual(p["etype"], "장소")
        self.assertNotIn("amatch", sql)
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))

    def test_area_names_add_match_list_and_bind_unique_count(self) -> None:
        """갈래를 주면 명단 CTE(``amatch``)와 그 명단으로 거르는 조건이 들어가고, AND 판정은 **고유** 이름 수다."""
        _out, sql, p, _cur = _call(area_names=["여행·명소", "여행·명소"])
        self.assertIn("amatch AS MATERIALIZED (", sql)
        self.assertIn("AND b.node_id IN (SELECT node_id FROM amatch)", sql)
        self.assertIn("HAVING COUNT(DISTINCT (elb->>'name')) = %(area_n)s", sql)
        self.assertEqual(p["areas"], ["여행·명소", "여행·명소"])
        self.assertEqual(p["area_n"], 1)
        self.assertNotIn("b.entity_type = %(etype)s", sql)
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))

    def test_empty_or_blank_area_names_equal_no_condition(self) -> None:
        """빈 목록 · 공백 이름은 갈래 조건이 없는 것과 같다(``_area_params`` 정규화 — 빈 목록 = 조건 없음)."""
        _o, base_sql, base_p, _c = _call()
        for given in ([], ["", "  "], None):
            with self.subTest(area_names=given):
                _out, sql, p, _cur = _call(area_names=given)
                self.assertEqual(sql, base_sql)
                self.assertEqual(p, base_p)

    def test_picked_modalities_extend_chip_array_sorted_unique(self) -> None:
        """이미 고른 자료 유형을 주면 칩마다 「고른 유형 ∪ 칩 유형」을 모두 가진 개체를 센다(바인딩은 정렬 · 고유)."""
        _out, sql, p, _cur = _call(modalities=["video", "image", "video"])
        self.assertIn("h.mods @> (%(picked)s::text[] || c.code)", sql)
        self.assertNotIn("ARRAY[c.code]", sql)
        self.assertEqual(p["picked"], ["image", "video"])
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))

    def test_empty_picked_modalities_equal_no_condition(self) -> None:
        _o, base_sql, base_p, _c = _call()
        _out, sql, p, _cur = _call(modalities=[])
        self.assertEqual(sql, base_sql)
        self.assertEqual(p, base_p)

    def test_all_conditions_together(self) -> None:
        """세 조건을 함께 주면 세 조각이 모두 들어가고, 갈래 명단은 ``base`` 뒤 · ``scoped`` 앞에 놓인다."""
        _out, sql, p, _cur = _call(entity_type="음식", area_names=["한식", "음료"], modalities=["text"])
        self.assertIn("WHERE TRUE AND b.entity_type = %(etype)s "
                      "AND b.node_id IN (SELECT node_id FROM amatch)", sql)
        self.assertIn("(%(picked)s::text[] || c.code)", sql)
        at = {name: sql.index(f"{name} AS MATERIALIZED (") for name in ("base", "amatch", "scoped")}
        self.assertLess(at["base"], at["amatch"])
        self.assertLess(at["amatch"], at["scoped"])
        self.assertEqual((p["etype"], p["areas"], p["area_n"], p["picked"]),
                         ("음식", ["한식", "음료"], 2, ["text"]))
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))
        self.assertNotIn("{", sql)

    def test_statuses_override_is_bound_as_given(self) -> None:
        _out, _sql, p, _cur = _call(statuses=["active"])
        self.assertEqual(p["statuses"], ["active"])

    def test_empty_statuses_bind_empty_not_default(self) -> None:
        """빈 상태 목록은 빈 결과다 — 기본값(active+proposed)으로 되돌리지 않는다(``_wanted_statuses`` 와 같은 처리)."""
        _out, _sql, p, _cur = _call(statuses=[])
        self.assertEqual(p["statuses"], [])
        self.assertEqual(p["statuses"], gq._wanted_statuses([]))

    def test_empty_entity_type_is_a_condition(self) -> None:
        """빈 문자열 종류도 조건으로 건다 — ``None`` 만 「조건 없음」이다(기존 함수의 ``IS NULL OR`` 판정과 같다)."""
        _out, sql, p, _cur = _call(entity_type="")
        self.assertIn("WHERE TRUE AND b.entity_type = %(etype)s", sql)
        self.assertEqual(p["etype"], "")

    def test_blank_area_name_is_dropped_from_binding(self) -> None:
        """공백 이름은 빼고 나머지로 조건을 건다(``area_n`` 도 남은 이름 기준)."""
        _out, sql, p, _cur = _call(area_names=["", "한식"])
        self.assertEqual(p["areas"], ["한식"])
        self.assertEqual(p["area_n"], 1)
        self.assertIn("amatch AS MATERIALIZED (", sql)
        self.assertIn("AND b.node_id IN (SELECT node_id FROM amatch)", sql)

    def test_empty_modality_codes_still_runs(self) -> None:
        """칩으로 셀 자료 유형이 없어도 깨지지 않고 빈 바인딩으로 돈다(자료 유형 축만 비어 온다)."""
        rows = [{"axis": "type", "name": "장소", "skill": None, "skill_code": None, "n": 4, "ord": 1}]
        out, _sql, p, _cur = _call(rows=rows, modality_codes=[])
        self.assertEqual(p["codes"], [])
        self.assertEqual(out["modalities"], {})
        self.assertEqual(out["types"], {"장소": 4})

    def test_have_collects_picked_modalities_outside_chip_codes(self) -> None:
        """고른 자료 유형이 칩 목록 밖이어도 ``have`` 가 그 유형을 모은다(빠지면 칩이 모두 0 이 된다).

        기존 허용목록은 「고른 유형 ∪ {칩}」을 모두 가진 개체라, 고른 유형이 칩 목록에 없어도 그 유형을 본다.
        """
        _out, sql, p, _cur = _call(modality_codes=["image", "video"], modalities=["text"])
        self.assertIn("a.modality = ANY(%(have_codes)s)", sql)
        self.assertNotIn("a.modality = ANY(%(codes)s)", sql)
        self.assertEqual(p["have_codes"], ["image", "text", "video"])
        self.assertEqual(p["codes"], ["image", "video"])  # 칩 목록 · 순서는 그대로
        self.assertEqual(p["picked"], ["text"])

    def test_have_codes_without_picked_are_chip_codes(self) -> None:
        _out, _sql, p, _cur = _call()
        self.assertEqual(p["have_codes"], sorted(_CODES))


class TestEntityFacetsPlanGuards(unittest.TestCase):
    """속도와 결과 동치를 지키는 SQL 모양 — 지우면 조용히 느려지거나 기존 호출과 결과가 갈린다."""

    def test_intermediate_sets_are_materialized(self) -> None:
        """``base`` · ``scoped`` · ``have``(갈래가 있으면 ``amatch``)가 ``MATERIALIZED`` 다.

        한 곳에서만 참조하는 ``have`` · ``amatch`` 는 지우면 그 자리에 펼쳐져 자료 유형 판정이 칩마다 반복되거나
        갈래 칩이 느려질 수 있다. 두 곳 이상에서 참조하는 ``base`` · ``scoped`` 는 명시로 고정해 둔 것이다.
        """
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        for name in ("base", "amatch", "scoped", "have"):
            with self.subTest(cte=name):
                self.assertIn(f"{name} AS MATERIALIZED (", sql)
        _out, sql, _p, _cur = _call()
        for name in ("base", "scoped", "have"):
            with self.subTest(cte=name, areas=None):
                self.assertIn(f"{name} AS MATERIALIZED (", sql)

    def test_area_match_list_groups_by_node_id(self) -> None:
        """갈래 명단은 ``node_id``(uuid)로 묶는다 — (종류, 표기, 이름) 짝으로 중복 제거하면 한글 정렬이 붙는다."""
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        amatch = sql.split("amatch AS MATERIALIZED (", 1)[1].split("scoped AS MATERIALIZED (", 1)[0]
        self.assertIn("GROUP BY b.node_id", amatch)
        self.assertNotIn("SELECT DISTINCT", amatch)

    def test_area_match_list_reads_base(self) -> None:
        """갈래 명단은 노출 개체(``base``)에서 개체 라벨을 붙여 만든다 — 노출되지 않은 개체를 다시 훑지 않는다."""
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        amatch = sql.split("amatch AS MATERIALIZED (", 1)[1].split("scoped AS MATERIALIZED (", 1)[0]
        self.assertIn("FROM base b", amatch)
        self.assertIn("JOIN entity_mm_skill_label el", amatch)

    def test_axes_read_the_right_scope(self) -> None:
        """종류 칩은 조건 없는 ``base`` 에서, 갈래 칩은 종류 · 갈래를 건 ``scoped`` 에서 센다(기존 두 함수와 같은 뜻)."""
        _out, sql, _p, _cur = _call(entity_type="장소", area_names=["여행·명소"])
        self.assertIn("FROM base b GROUP BY b.entity_type", sql)
        self.assertIn("EXISTS (SELECT 1 FROM scoped x WHERE", sql)
        self.assertIn("FROM scoped s JOIN graph_edge ge", sql)  # 자료 유형 판정도 scoped 범위
        self.assertIn("lab.code <> 'unassigned'", sql)
        self.assertIn("s.skill_code <> ALL(%(reserved)s)", sql)

    def test_area_order_comes_from_db_row_number(self) -> None:
        """갈래 순서는 DB 정렬 규칙으로 매긴 ``row_number`` 로 받는다(파이썬 정렬은 밑줄 등에서 DB 와 갈린다)."""
        _out, sql, _p, _cur = _call()
        self.assertIn(
            "row_number() OVER (ORDER BY COALESCE(hit.n, 0) DESC, lab.skill_code, lab.ord)", sql)
        self.assertIn("unnest(%(codes)s::text[]) WITH ORDINALITY AS c(code, ord)", sql)

    def test_type_order_comes_from_db_row_number(self) -> None:
        """종류 순서도 DB 정렬 규칙으로 매긴다(``count_entities_by_type`` 의 ``ORDER BY entity_type`` 과 같은 키)."""
        _out, sql, _p, _cur = _call()
        self.assertIn("row_number() OVER (ORDER BY b.entity_type) AS ord", sql)
        self.assertNotIn("0::bigint", sql)


class TestEntityFacetsShaping(unittest.TestCase):
    """반환 정형 — 세 축이 섞여 오는 행을 축별로 가른다."""

    # 세 축이 섞이고 축마다 ``ord`` 가 뒤섞인 순서로 온다(UNION ALL 은 순서를 보장하지 않는다).
    _ROWS = [
        {"axis": "area", "name": "음료", "skill": "음식", "skill_code": "food_content",
         "n": 0, "ord": 3},
        {"axis": "type", "name": "장소", "skill": None, "skill_code": None, "n": 40, "ord": 2},
        {"axis": "modality", "name": "video", "skill": None, "skill_code": None, "n": 7, "ord": 3},
        {"axis": "area", "name": "영화", "skill": "자료 성격", "skill_code": "content_form",
         "n": 5, "ord": 1},
        {"axis": "modality", "name": "text", "skill": None, "skill_code": None, "n": 12, "ord": 1},
        {"axis": "type", "name": "인물", "skill": None, "skill_code": None, "n": 3, "ord": 1},
        {"axis": "area", "name": "한식", "skill": "음식", "skill_code": "food_content",
         "n": 2, "ord": 2},
        {"axis": "modality", "name": "audio", "skill": None, "skill_code": None, "n": 1, "ord": 4},
        {"axis": "modality", "name": "image", "skill": None, "skill_code": None, "n": 0, "ord": 2},
    ]

    def test_rows_are_split_by_axis_in_given_order(self) -> None:
        out, _sql, _p, _cur = _call(rows=self._ROWS)
        self.assertEqual(set(out), {"types", "areas", "modalities"})
        self.assertEqual(out["types"], {"인물": 3, "장소": 40})
        self.assertEqual(list(out["types"]), ["인물", "장소"])  # SQL 이 준 ord 오름차순
        self.assertEqual(out["areas"], [  # SQL 이 준 ord 오름차순 — 0건 갈래도 담는다
            {"name": "영화", "skill": "자료 성격", "skill_code": "content_form", "count": 5},
            {"name": "한식", "skill": "음식", "skill_code": "food_content", "count": 2},
            {"name": "음료", "skill": "음식", "skill_code": "food_content", "count": 0},
        ])
        self.assertEqual(out["modalities"], {"text": 12, "image": 0, "video": 7, "audio": 1})
        self.assertEqual(list(out["modalities"]), _CODES)  # 호출자가 준 자료 유형 순서
        counts = [*out["types"].values(), *(a["count"] for a in out["areas"]),
                  *out["modalities"].values()]
        self.assertTrue(all(type(v) is int for v in counts))

    def test_type_keys_follow_sql_ord_not_python_name_order(self) -> None:
        """종류 키 순서는 SQL 이 준 ``ord``(DB 정렬 규칙)를 따른다 — 파이썬 이름순으로 다시 줄 세우지 않는다."""
        rows = [
            {"axis": "type", "name": "인물", "skill": None, "skill_code": None, "n": 3, "ord": 2},
            {"axis": "type", "name": "장소", "skill": None, "skill_code": None, "n": 40, "ord": 1},
        ]
        out, _sql, _p, _cur = _call(rows=rows)
        self.assertEqual(list(out["types"]), ["장소", "인물"])
        self.assertEqual(out["types"], {"장소": 40, "인물": 3})

    def test_no_rows_gives_empty_axes(self) -> None:
        out, _sql, _p, _cur = _call(rows=[])
        self.assertEqual(out, {"types": {}, "areas": [], "modalities": {}})


if __name__ == "__main__":
    unittest.main()
