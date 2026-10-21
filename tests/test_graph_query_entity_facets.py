"""``count_entity_facets``(개체 칩 건수 — 종류 · 갈래 · 자료 유형을 한 질의로) mock conn 단위 테스트(DB 불필요).

봉인하는 것: ① 칩마다 정해진 범위에서 센다 — 칩 숫자 = 그 칩을 눌렀을 때 나올 결과 수(종류는 종류 조건만 빼고,
갈래 · 자료 유형은 지금 조건에 자기 값을 더해) ② 없는 조건은 문장에서 뺀다(``IS NULL OR`` 꼴을 두지 않는다)
③ 있는 조건은 정해진 조각 · 바인딩으로 들어간다 ④ 중간 집합이 ``MATERIALIZED`` 이고 갈래 칩은 라벨 표를 직접 읽지
않는다 ⑤ 반환은 축마다 SQL 이 준 ``ord`` 순서다.
칩 숫자가 실제로 클릭 결과와 같은지는 실 DB 대조(``tests/test_mm_meta_seams_e2e.py``)가 본다.
설계 배경: `specs/108-entity-facets-own-axis`
"""

from __future__ import annotations

import itertools
import re
import unittest
from unittest.mock import MagicMock

from src.mm_classify.model import NON_CLASSIFY_SKILL_CODES
from src.relations import graph_query as gq
from src.relations.schema import MM_MEMBER_KIND_CODE

_CODES = ["text", "image", "video", "audio"]

# 「값이 비면 조건 무시」 꼴 — ``… IS NULL OR …`` 과 ``… OR %(x)s IS NULL`` 을 대소문자 · 공백과 무관하게 잡는다.
_NULL_BYPASS_RX = re.compile(r"(?i)\bis\s+null\s+or\b|\bor\s+%\(\w+\)s\s+is\s+null")

# 조건 세 가지(종류 · 갈래 · 고른 자료 유형)를 켜고 끈 모든 조합 — 조립 규칙은 조합마다 달라진다.
_COMBOS = [
    {k: v for k, v, on in (("entity_type", "음식", t), ("area_names", ["한식"], a), ("modalities", ["image"], m))
     if on}
    for t, a, m in itertools.product((False, True), repeat=3)
]


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


def _cte_match(sql: str, name: str) -> re.Match[str] | None:
    """CTE ``name`` 의 머리(``name AS [MATERIALIZED] (``)를 찾는다.

    이름 앞이 낱말 문자가 아닌 자리만 본다 — ``ascoped`` 속 ``scoped`` · ``elab`` 속 ``lab`` 을 잘못 잡지 않게.
    """
    return re.search(rf"(?<!\w){name} AS (?:MATERIALIZED )?\(", sql)


def _cte(sql: str, name: str) -> str:
    """정규화 SQL 에서 CTE ``name`` 의 본문(바깥 괄호 안)을 돌려준다. 없거나 괄호가 안 닫히면 실패한다."""
    m = _cte_match(sql, name)
    if m is None:
        raise AssertionError(f"CTE {name} 가 SQL 에 없다")
    depth, i = 1, m.end()
    while depth:
        if i >= len(sql):
            raise AssertionError(f"CTE {name} 의 괄호가 닫히지 않는다")
        depth += {"(": 1, ")": -1}.get(sql[i], 0)
        i += 1
    return sql[m.end():i - 1].strip()


def _axis(sql: str, axis: str) -> str:
    """바깥 SELECT 에서 한 축(``type`` · ``area`` · ``modality``)을 세는 부분을 돌려준다."""
    parts = sql[sql.index("SELECT 'type' AS axis"):].split(" UNION ALL ")
    return dict(zip(("type", "area", "modality"), parts, strict=True))[axis]


def _reads(text: str, name: str) -> bool:
    """``text`` 가 이름 ``name`` 을 낱말로 담는가(``ascoped`` 는 ``scoped`` 로 치지 않는다)."""
    return re.search(rf"\b{name}\b", text) is not None


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
        """종류만 주면 종류 조건은 ``scoped`` 한 곳에만 붙는다 — 종류 칩의 모수(``ascoped``)에는 걸지 않는다."""
        _out, sql, p, _cur = _call(entity_type="장소")
        self.assertIn("WHERE TRUE AND s.entity_type = %(etype)s", _cte(sql, "scoped"))
        self.assertEqual(sql.count("%(etype)s"), 1)
        self.assertEqual(p["etype"], "장소")
        self.assertNotIn("amatch", sql)
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))

    def test_area_names_add_match_list_and_bind_unique_count(self) -> None:
        """갈래를 주면 명단 CTE(``amatch``)와 그 명단으로 거르는 조건이 들어가고, AND 판정은 **고유** 이름 수다."""
        _out, sql, p, _cur = _call(area_names=["여행·명소", "여행·명소"])
        self.assertIn("amatch AS MATERIALIZED (", sql)
        self.assertIn("AND b.node_id IN (SELECT node_id FROM amatch)", _cte(sql, "ascoped"))
        self.assertIn("HAVING COUNT(DISTINCT (elb->>'name')) = %(area_n)s", sql)
        self.assertEqual(p["areas"], ["여행·명소", "여행·명소"])
        self.assertEqual(p["area_n"], 1)
        self.assertNotIn("%(etype)s", sql)
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
        """세 조건을 함께 주면 갈래는 ``ascoped``, 종류는 ``scoped`` 에 들어가고 CTE 는 읽는 순서대로 놓인다.

        CTE 는 앞에서 정의한 것만 읽을 수 있다 — 갈래 명단(``amatch``)은 ``base`` 뒤 · ``ascoped`` 앞이다.
        """
        _out, sql, p, _cur = _call(entity_type="음식", area_names=["한식", "음료"], modalities=["text"])
        self.assertIn("WHERE TRUE AND b.node_id IN (SELECT node_id FROM amatch)", _cte(sql, "ascoped"))
        self.assertNotIn("%(etype)s", _cte(sql, "ascoped"))  # 종류 칩의 모수에는 종류 조건이 없다
        self.assertIn("WHERE TRUE AND s.entity_type = %(etype)s", _cte(sql, "scoped"))
        self.assertIn("(%(picked)s::text[] || c.code)", sql)
        names = ("base", "amatch", "ascoped", "scoped", "have", "pick", "elab", "hit")
        at = [_cte_match(sql, name).start() for name in names]
        self.assertEqual(at, sorted(at), f"CTE 순서가 {names} 가 아니다")
        self.assertEqual((p["etype"], p["areas"], p["area_n"], p["picked"]),
                         ("음식", ["한식", "음료"], 2, ["text"]))
        self.assertIsNone(_NULL_BYPASS_RX.search(sql))
        self.assertNotIn("{", sql)

    def test_every_combination_fills_slots_and_binds_placeholders(self) -> None:
        """조건 조합 여덟 가지 모두 자리(``{…}``)가 채워지고, SQL 이 쓰는 바인딩 이름이 모두 바인딩에 있다.

        조합마다 조각이 달라져 한 조합에서만 자리가 남거나 바인딩이 빠지면 그 조건으로 부를 때만 실행 오류가 난다.
        """
        for given in _COMBOS:
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, p, _cur = _call(**given)
                self.assertNotIn("{", sql)
                self.assertNotIn("}", sql)
                self.assertIsNone(_NULL_BYPASS_RX.search(sql))
                self.assertLessEqual(set(re.findall(r"%\((\w+)\)s", sql)), set(p))

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
        self.assertIn("WHERE TRUE AND s.entity_type = %(etype)s", _cte(sql, "scoped"))
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


class TestEntityFacetsAxisScope(unittest.TestCase):
    """칩마다 어느 범위에서 세나 — 칩 숫자 = 그 칩을 눌렀을 때 나올 결과 수.

    종류는 하나만 고르는 축이라 누르면 종류가 **바뀐다** → 종류 조건만 빼고 갈래 · 자료 유형은 적용한다.
    갈래 · 자료 유형은 여럿을 함께 고르는 축이라 누르면 조건이 **더해진다** → 지금 조건을 모두 적용한 위에 센다.
    예: 갈래 「한식」을 고른 채 종류 칩 「인물」의 숫자는 「한식」인 인물 수다(갈래를 무시한 인물 수가 아니다).
    """

    def test_axes_read_the_right_scope(self) -> None:
        """자료 유형을 고르지 않았으면 종류 칩은 ``ascoped``(갈래만 적용)에서, 갈래 · 자료 유형 칩은 ``scoped`` 에서 센다."""
        for given in (c for c in _COMBOS if "modalities" not in c):
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, _p, _cur = _call(**given)
                self.assertIn("FROM ascoped t GROUP BY t.entity_type", _axis(sql, "type"))
                self.assertNotIn("%(etype)s", _axis(sql, "type"))
                self.assertIn("WHERE e.node_id IN (SELECT node_id FROM scoped) GROUP BY", _cte(sql, "hit"))
                self.assertIn("FROM scoped s JOIN graph_edge ge", _cte(sql, "have"))
                self.assertIn("(SELECT COUNT(*) FROM have h WHERE h.mods @> ARRAY[c.code])",
                              _axis(sql, "modality"))
                self.assertIsNone(_cte_match(sql, "pick"))
                self.assertIn("lab.code <> 'unassigned'", sql)
                self.assertIn("s.skill_code <> ALL(%(reserved)s)", sql)

    def test_axes_read_pick_when_modalities_picked(self) -> None:
        """자료 유형을 골랐으면 ``have`` 는 ``ascoped`` 에서 모으고, 종류 · 갈래 칩은 ``pick``(고른 유형을 모두 가진 개체)에서 센다.

        ``have`` 를 ``scoped``(종류 적용)에서 모으면 종류 칩이 고른 종류 밖의 개체를 볼 수 없어 다른 종류가 모두 0 이 된다.
        """
        for given in (c for c in _COMBOS if "modalities" in c):
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, p, _cur = _call(**given)
                self.assertIn("FROM ascoped s JOIN graph_edge ge", _cte(sql, "have"))
                self.assertEqual(_cte(sql, "pick"),
                                 "SELECT h.node_id, h.entity_type FROM have h WHERE h.mods @> %(picked)s::text[]")
                self.assertIn("FROM pick t GROUP BY t.entity_type", _axis(sql, "type"))
                self.assertNotIn("%(etype)s", _axis(sql, "type"))
                self.assertRegex(_cte(sql, "hit"), r"WHERE e\.node_id IN \(SELECT node_id FROM pick\b")
                self.assertIn("(SELECT COUNT(*) FROM have h WHERE h.mods @> (%(picked)s::text[] || c.code)",
                              _axis(sql, "modality"))
                self.assertEqual(p["picked"], ["image"])

    def test_type_with_picked_modalities_filters_pick_and_modality_axis(self) -> None:
        """종류 + 자료 유형이면 종류 조건은 갈래 칩의 ``pick`` 과 자료 유형 칩에 붙고, 종류 칩 · ``ascoped`` · ``have`` 에는 없다."""
        _out, sql, p, _cur = _call(entity_type="음식", modalities=["image"])
        self.assertIn("WHERE e.node_id IN (SELECT node_id FROM pick WHERE entity_type = %(etype)s) GROUP BY",
                      _cte(sql, "hit"))
        self.assertIn("h.mods @> (%(picked)s::text[] || c.code) AND h.entity_type = %(etype)s)",
                      _axis(sql, "modality"))
        self.assertIn("WHERE TRUE AND s.entity_type = %(etype)s", _cte(sql, "scoped"))
        for part in (_axis(sql, "type"), _cte(sql, "ascoped"), _cte(sql, "have"), _cte(sql, "pick")):
            self.assertNotIn("%(etype)s", part)
        self.assertEqual(sql.count("%(etype)s"), 3)
        self.assertEqual(p["etype"], "음식")

    def test_picked_modalities_without_type_leave_pick_unfiltered(self) -> None:
        """자료 유형만 고르면 ``pick`` 을 그대로 읽는다 — 종류 조건 조각이 어디에도 없다."""
        _out, sql, p, _cur = _call(modalities=["image"])
        self.assertIn("WHERE e.node_id IN (SELECT node_id FROM pick) GROUP BY", _cte(sql, "hit"))
        self.assertIn("h.mods @> (%(picked)s::text[] || c.code)), c.ord", _axis(sql, "modality"))
        self.assertNotIn("%(etype)s", sql)
        self.assertNotIn("etype", p)


class TestEntityFacetsPlanGuards(unittest.TestCase):
    """속도와 결과를 지키는 SQL 모양 — 지우면 조용히 느려지거나 칩 숫자가 클릭 결과와 갈린다."""

    def test_intermediate_sets_are_materialized(self) -> None:
        """``base`` · ``ascoped`` · ``scoped`` · ``have`` · ``elab``(갈래가 있으면 ``amatch`` · 자료 유형이 있으면 ``pick``)이
        ``MATERIALIZED`` 다.

        한 곳에서만 참조하는 CTE 는 지우면 그 자리에 펼쳐진다 — ``elab`` 은 갈래 칩이 라벨 인덱스를 개체마다 조회하는
        경로로 돌아가고, ``have`` 는 자료 유형 판정이 칩마다 반복되고, ``amatch`` 는 갈래 칩이 느려질 수 있다.
        """
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        for name in ("base", "amatch", "ascoped", "scoped", "have", "elab"):
            with self.subTest(cte=name):
                self.assertIn(f"{name} AS MATERIALIZED (", sql)
        _out, sql, _p, _cur = _call()
        for name in ("base", "ascoped", "scoped", "have", "elab"):
            with self.subTest(cte=name, areas=None):
                self.assertIn(f"{name} AS MATERIALIZED (", sql)
        _out, sql, _p, _cur = _call(entity_type="음식", area_names=["한식"], modalities=["image"])
        for name in ("base", "amatch", "ascoped", "scoped", "have", "pick", "elab"):
            with self.subTest(cte=name, modalities=["image"]):
                self.assertRegex(sql, rf"(?<!\w){name} AS MATERIALIZED \(")

    def test_pick_only_when_modalities_picked_and_elab_always(self) -> None:
        """``pick`` 은 자료 유형을 골랐을 때만 생기고, ``elab`` 은 조건과 무관하게 늘 같은 모양으로 있다."""
        _o, base_sql, _p, _c = _call()
        for given in _COMBOS:
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, _p, _cur = _call(**given)
                self.assertEqual(_cte_match(sql, "pick") is not None, "modalities" in given)
                self.assertEqual(_cte(sql, "elab"), _cte(base_sql, "elab"))

    def test_hit_counts_from_elab_not_label_table(self) -> None:
        """갈래 칩(``hit``)은 라벨 표를 직접 읽지 않고 ``elab``(노출 개체의 라벨 행)에서 센다.

        라벨 표를 직접 읽으면 범위를 아주 작게 짐작할 때 개체마다 라벨 인덱스를 조회하는 경로가 되어 크게 느려진다.
        """
        _o, base_sql, _p, _c = _call()
        self.assertEqual(
            _cte(base_sql, "elab"),
            "SELECT el.skill_code, el.label_code, b.node_id FROM entity_mm_skill_label el "
            "JOIN base b ON b.entity_type = el.entity_type AND b.entity_uid = el.entity_uid")
        for given in _COMBOS:
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, _p, _cur = _call(**given)
                hit = _cte(sql, "hit")
                self.assertTrue(hit.startswith("SELECT e.skill_code, e.label_code, COUNT(*) AS n FROM elab e WHERE"),
                                hit)
                self.assertNotIn("entity_mm_skill_label", hit)
                self.assertNotIn("EXISTS", hit)

    def test_pick_is_never_set_against_scoped(self) -> None:
        """``pick`` 과 ``scoped`` 를 한 자리에서 맞대지 않는다 — 종류 조건은 ``pick`` 에 실은 종류로 건다.

        두 CTE 를 서로 맞대는 반조인은 양쪽 짐작이 모두 작아 중첩 반복이 되고 개체 수의 제곱으로 커진다.
        """
        for given in (c for c in _COMBOS if "modalities" in c):
            with self.subTest(**{k: str(v) for k, v in given.items()}):
                _out, sql, _p, _cur = _call(**given)
                parts = {name: _cte(sql, name) for name in ("pick", "hit", "have")}
                parts.update({f"axis:{ax}": _axis(sql, ax) for ax in ("type", "area", "modality")})
                for label, part in parts.items():
                    self.assertFalse(_reads(part, "scoped"), f"{label} 가 scoped 를 읽는다: {part}")
                self.assertTrue(_reads(_cte(sql, "hit"), "pick"))

    def test_area_match_list_groups_by_node_id(self) -> None:
        """갈래 명단은 ``node_id``(uuid)로 묶는다 — (종류, 표기, 이름) 짝으로 중복 제거하면 한글 정렬이 붙는다."""
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        amatch = _cte(sql, "amatch")
        self.assertIn("GROUP BY b.node_id", amatch)
        self.assertNotIn("SELECT DISTINCT", amatch)

    def test_area_match_list_reads_base(self) -> None:
        """갈래 명단은 노출 개체(``base``)에서 개체 라벨을 붙여 만든다 — 노출되지 않은 개체를 다시 훑지 않는다."""
        _out, sql, _p, _cur = _call(area_names=["여행·명소"])
        amatch = _cte(sql, "amatch")
        self.assertIn("FROM base b", amatch)
        self.assertIn("JOIN entity_mm_skill_label el", amatch)

    def test_area_order_comes_from_db_row_number(self) -> None:
        """갈래 순서는 DB 정렬 규칙으로 매긴 ``row_number`` 로 받는다(파이썬 정렬은 밑줄 등에서 DB 와 갈린다)."""
        _out, sql, _p, _cur = _call()
        self.assertIn(
            "row_number() OVER (ORDER BY COALESCE(hit.n, 0) DESC, lab.skill_code, lab.ord)", sql)
        self.assertIn("unnest(%(codes)s::text[]) WITH ORDINALITY AS c(code, ord)", sql)

    def test_type_order_comes_from_db_row_number(self) -> None:
        """종류 순서도 DB 정렬 규칙으로 매긴다(``count_entities_by_type`` 의 ``ORDER BY entity_type`` 과 같은 키)."""
        _out, sql, _p, _cur = _call()
        self.assertIn("row_number() OVER (ORDER BY t.entity_type) AS ord", sql)
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
