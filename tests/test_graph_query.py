"""그래프 read seam ``graph_query`` 단위 테스트 (mock conn, DB 불필요).

검증 의도
    - 대칭 엣지는 캐논 1행으로만 저장되므로(graph_persist._canonical_pair),
      "자산 X의 이웃"을 순진하게 ``WHERE src_node=X`` 로만 찾으면 X가 dst로 접힌
      대칭 엣지를 누락한다(ADR 2026-05-28). 그래서 SQL이 ``sn.asset_id OR dn.asset_id``
      양방향 매칭 + ``relation_kind`` 조인 + status 바인딩을 갖는지(T001) 검증한다.
    - 질의 자산 관점 정규화(대칭→undirected·반대편, 비대칭 src→outbound·dst→inbound)와
      반환 dict 키 세트가 계약대로인지(T002) 검증한다.
    - 결정성(헌법 3조): ``ORDER BY confidence DESC NULLS LAST, edge_id`` 2차 정렬 키 존재.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


def _conn_returning(rows: list[dict]):
    """``conn.cursor(row_factory=dict_row)`` 컨텍스트매니저를 흉내내는 mock conn.

    relation_type_catalog/review 단위 테스트와 동형 패턴: ``__enter__`` 가 cur 를 돌려주고
    ``fetchall`` 이 주입한 행을 반환한다. ``execute`` 인자는 call_args 로 캡처해 SQL·바인딩 검증.
    """
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    return conn, cur


class TestGraphQuerySQL(unittest.TestCase):
    """T001 — 실행 SQL이 양방향·relation_kind 조인·status 바인딩·결정적 정렬을 갖는가."""

    def test_sql_matches_both_directions_and_joins_relation_kind(self) -> None:
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="A")

        sql = cur.execute.call_args[0][0]
        compact = " ".join(sql.split())  # 줄바꿈·들여쓰기 정규화로 견고한 부분문자열 검사

        # ① 대칭 엣지 누락 방지를 위한 양방향 매칭(src/dst 어느 쪽이든 X면 매칭)
        self.assertIn("sn.asset_id = %s OR dn.asset_id = %s", compact)
        # ② is_symmetric·kind_code 는 relation_kind 에만 있으므로 반드시 조인
        self.assertIn("JOIN relation_kind", compact)
        # 양 끝점 asset 해소를 위한 node 역조인(asset 노드만)
        self.assertIn("node_kind = 'asset'", compact)

    def test_status_is_bound_parameter_not_hardcoded(self) -> None:
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="A", status="proposed")

        sql = cur.execute.call_args[0][0]
        params = cur.execute.call_args[0][1]
        compact = " ".join(sql.split())
        # ③ status 는 바인딩(%s) — 호출자가 active/proposed 등 선택 가능
        # 081: 상태 다중 조회(2단 노출)로 ANY 바인딩이 됐다 — 하드코딩이 아니라는 의도는 그대로.
        self.assertIn("ge.status = ANY(%s)", compact)
        # 양방향 asset_id 2개 + status 1개 = (X, X, status) 순서
        # 081: 상태를 목록으로 바인딩한다(2단 노출은 active+proposed 를 함께 읽는다).
        self.assertEqual(params, ("A", "A", ["proposed"]))

    def test_order_by_has_edge_id_secondary_for_determinism(self) -> None:
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="A")

        sql = cur.execute.call_args[0][0]
        compact = " ".join(sql.split())
        # ④ confidence 동점 시 순서 불안정 방지를 위한 edge_id 2차 정렬(헌법 3조, plan R-3)
        self.assertIn("ORDER BY ge.confidence DESC NULLS LAST, ge.edge_id", compact)

    def test_no_domain_exclusion(self) -> None:
        # 2026-07-23: 도메인 제외 전면 제거 — 관계 조회 SQL 이 medical 을 배제하지 않는다(의료 복귀 시 재도입).
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="A")
        compact = " ".join(cur.execute.call_args[0][0].split())
        self.assertNotIn("medical", compact)


class TestGraphQueryNormalize(unittest.TestCase):
    """T002 — 질의 자산 관점 정규화 분기와 반환 dict 키 세트."""

    _EXPECTED_KEYS = {
        "asset_id", "kind_code", "is_symmetric", "direction",
        "confidence", "status", "topic", "reason", "edge_id",
        # 057 FR-102: 이웃 표시필드 하향(하위호환 필드 추가)
        "file_name", "modality",
        # 081 조각③: 노출 등급(strong/weak) — 역시 하위호환 필드 추가다(기존 키 불변).
        "tier",
        # 081 조각⑤: 동시보유 접기에서 **접힌 종류**(대개 빈 리스트). 접힌 쪽에만 실으면
        # 소비처가 `.get()` 유무로 분기하므로 **모든 행에** 싣는다.
        "folded_kind_codes",
    }

    def _row(self, **over):
        """SQL(dict_row) 한 행을 흉내. graph_query 가 select 하는 컬럼명 그대로.

        057 FR-102: node→asset 조인으로 양끝 자산의 modality·fs_path 를 함께 끌어온다.
        """
        base = {
            "edge_id": "e1",
            "kind_code": "duplicate_near",
            "is_symmetric": True,
            "confidence": 0.95,
            "reason": "유사",
            "topic": {"topic_ko": "사진"},
            "status": "active",
            "src_asset": "A",
            "dst_asset": "B",
            "src_modality": "text",
            "dst_modality": "image",
            "src_fs_path": "/data/raw/문서A.txt",
            "dst_fs_path": "/data/raw/사진B.png",
        }
        base.update(over)
        return base

    def test_symmetric_edge_is_undirected_and_returns_other_side(self) -> None:
        # 대칭 kind: 방향은 무방향, 이웃 asset_id 는 질의 자산의 반대편
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=True, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="A")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["direction"], "undirected")
        self.assertEqual(out[0]["asset_id"], "B")  # 반대편

    def test_symmetric_edge_query_from_dst_side_still_returns_other(self) -> None:
        # X 가 dst 로 접힌 대칭 엣지(ADR 핵심 시나리오): 이웃은 src 쪽이어야 함
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=True, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="B")
        self.assertEqual(out[0]["direction"], "undirected")
        self.assertEqual(out[0]["asset_id"], "A")  # 질의 자산(B)의 반대편 = A

    def test_asymmetric_src_is_outbound(self) -> None:
        # 비대칭이며 질의 자산이 src → outbound, 이웃은 dst
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=False, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="A")
        self.assertEqual(out[0]["direction"], "outbound")
        self.assertEqual(out[0]["asset_id"], "B")

    def test_asymmetric_dst_is_inbound(self) -> None:
        # 비대칭이며 질의 자산이 dst → inbound, 이웃은 src
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=False, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="B")
        self.assertEqual(out[0]["direction"], "inbound")
        self.assertEqual(out[0]["asset_id"], "A")

    def test_return_dict_has_exact_contract_keys(self) -> None:
        # 반환 dict 키 세트가 계약(plan D-1)과 정확히 일치 — 소비자(상세·묶음) 일관성
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row()])
        out = fetch_active_relations_for_asset(conn, asset_id="A")
        self.assertEqual(set(out[0].keys()), self._EXPECTED_KEYS)
        # 통과 필드는 원본 보존
        self.assertEqual(out[0]["kind_code"], "duplicate_near")
        self.assertEqual(out[0]["confidence"], 0.95)
        self.assertEqual(out[0]["topic"], {"topic_ko": "사진"})
        self.assertEqual(out[0]["edge_id"], "e1")

    def test_neighbor_carries_other_side_file_name_and_modality(self) -> None:
        # FR-102(057): 이웃 dict 에 상대 자산의 file_name(fs_path basename)·modality 를 싣는다.
        # 질의 자산 A(src) → 이웃은 dst(B): dst 쪽 modality/fs_path 를 취해야 한다.
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=True, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="A")
        self.assertEqual(out[0]["asset_id"], "B")
        self.assertEqual(out[0]["file_name"], "사진B.png")  # dst basename
        self.assertEqual(out[0]["modality"], "image")       # dst modality

    def test_neighbor_from_dst_side_takes_src_file_name_and_modality(self) -> None:
        # 질의 자산이 dst 인 경우(대칭 엣지가 접힘): 이웃은 src → src 쪽 modality/fs_path.
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, _ = _conn_returning([self._row(is_symmetric=True, src_asset="A", dst_asset="B")])
        out = fetch_active_relations_for_asset(conn, asset_id="B")
        self.assertEqual(out[0]["asset_id"], "A")
        self.assertEqual(out[0]["file_name"], "문서A.txt")  # src basename
        self.assertEqual(out[0]["modality"], "text")        # src modality

    def test_sql_joins_asset_for_both_endpoints(self) -> None:
        # FR-102: modality·fs_path 는 asset 에만 있으므로 양끝 node→asset 조인 필요(assets_in_topic 패턴).
        from src.relations.graph_query import fetch_active_relations_for_asset

        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="A")
        compact = " ".join(cur.execute.call_args[0][0].split())
        self.assertIn("sa.modality", compact)
        self.assertIn("da.modality", compact)
        self.assertIn("sa.fs_path", compact)
        self.assertIn("da.fs_path", compact)

    def test_determinism_same_input_same_output(self) -> None:
        # 같은 입력 2회 → 같은 결과(헌법 3조). 정규화는 순수하므로 안정적.
        from src.relations.graph_query import fetch_active_relations_for_asset

        rows = [
            self._row(edge_id="e1", confidence=0.9, src_asset="A", dst_asset="B"),
            self._row(edge_id="e2", confidence=0.9, src_asset="C", dst_asset="A", is_symmetric=False),
        ]
        conn1, _ = _conn_returning([dict(r) for r in rows])
        conn2, _ = _conn_returning([dict(r) for r in rows])
        self.assertEqual(
            fetch_active_relations_for_asset(conn1, asset_id="A"),
            fetch_active_relations_for_asset(conn2, asset_id="A"),
        )


if __name__ == "__main__":
    unittest.main()

# ── 081 노출 2단 ────────────────────────────────────────────────────────────────
def _row(*, status="active", kind="same_domain", conf=0.9, edge="e1",
         src="a1", dst="a2"):
    """노출 등급 테스트용 최소 행(기존 _conn_returning 이 그대로 먹는 모양)."""
    return {"edge_id": edge, "kind_code": kind, "is_symmetric": True,
            "confidence": conf, "reason": None, "topic": None, "status": status,
            "src_asset": src, "dst_asset": dst,
            "src_modality": "text", "src_fs_path": "/d/a1.txt",
            "dst_modality": "text", "dst_fs_path": "/d/a2.txt"}


class TestExposureTiers(unittest.TestCase):
    """강칸(연관 자료)·약칸(참고 자료) 2단 노출.

    약칸이 필요한 이유: 자동승인 게이트가 `same_domain` 을 강등하면 관계 보유 자산이 크게 줄어
    화면이 빈다. 강등분을 약칸으로 살려 커버리지를 지키면서 강칸 정밀도만 올린다.
    """

    def test_active_행에_strong_등급이_붙는다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="active")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual([r["tier"] for r in rows], ["strong"])

    def test_include_weak_이면_proposed_고신뢰도_함께_온다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="proposed", conf=0.9)])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.75)
        self.assertEqual([r["tier"] for r in rows], ["weak"])

    def test_include_weak_이_아니면_proposed_를_조회하지_않는다(self):
        # 상태 바인딩 자체가 active 뿐이어야 한다 — 읽어와서 버리면 쓸데없이 무겁다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, cur = _conn_returning([])
        fetch_relations_for_asset(conn, asset_id="a1", include_weak=False)
        params = cur.execute.call_args[0][1]
        self.assertEqual(params[2], ["active"])

    def test_include_weak_이면_두_상태를_바인딩한다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, cur = _conn_returning([])
        fetch_relations_for_asset(conn, asset_id="a1", include_weak=True)
        self.assertEqual(cur.execute.call_args[0][1][2], ["active", "proposed"])

    def test_저신뢰_proposed_는_include_weak_에도_안_온다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="proposed", conf=0.6)])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.75)
        self.assertEqual(rows, [])

    def test_명시적_계열_저신뢰_proposed_는_약칸으로_온다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="proposed", kind="references", conf=0.2)])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.75)
        self.assertEqual([r["tier"] for r in rows], ["weak"])

    def test_강칸이_약칸보다_먼저_온다(self):
        # 신뢰도만으로 정렬하면 고신뢰 약칸이 저신뢰 강칸을 밀어낸다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="proposed", conf=0.99, edge="e-weak", dst="a3"),
            _row(status="active", conf=0.80, edge="e-strong", dst="a2")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.75)
        self.assertEqual([r["tier"] for r in rows], ["strong", "weak"])

    def test_같은_등급_안에서는_DB_정렬을_보존한다(self):
        # 신뢰도·edge_id 정렬은 SQL 이 한다(mock 은 ORDER BY 를 적용하지 않으므로 여기서
        # 검증할 수 없다 — SQL 쪽은 TestGraphQuerySQL 이 본다). 여기서 봉인하는 것은
        # **등급 정렬이 안정 정렬이라 DB 순서를 흩뜨리지 않는다**는 점이다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="active", conf=0.9, edge="e-hi", dst="a2"),
            _row(status="active", conf=0.7, edge="e-lo", dst="a3")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual([r["edge_id"] for r in rows], ["e-hi", "e-lo"])

    def test_등급_정렬이_섞인_입력에서도_안정적이다(self):
        # 약칸이 앞에 와도 강칸이 올라오되, 같은 등급 내부 순서는 입력(=DB) 순서를 지킨다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="proposed", conf=0.99, edge="w1", dst="a3"),
            _row(status="active", conf=0.90, edge="s1", dst="a2"),
            _row(status="proposed", conf=0.80, edge="w2", dst="a4"),
            _row(status="active", conf=0.75, edge="s2", dst="a5")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.75)
        self.assertEqual([r["edge_id"] for r in rows], ["s1", "s2", "w1", "w2"])

    def test_질의_자산_관점_정규화가_유지된다(self):
        # 기존 seam 의 핵심 계약 — 이웃은 늘 반대편이고 대칭이면 undirected.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="active", src="a2", dst="a1")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual(rows[0]["asset_id"], "a2")
        self.assertEqual(rows[0]["direction"], "undirected")


class TestBackwardCompatibleWrapper(unittest.TestCase):
    """기존 함수의 계약 불변 — 포탈 상세·다운로드 번들이 이 키들을 쓴다."""

    def test_기존_함수가_그대로_동작한다(self):
        from src.relations.graph_query import fetch_active_relations_for_asset
        conn, _ = _conn_returning([_row(status="active")])
        rows = fetch_active_relations_for_asset(conn, asset_id="a1")
        for key in ("asset_id", "kind_code", "is_symmetric", "direction", "confidence",
                    "status", "topic", "reason", "edge_id", "file_name", "modality"):
            self.assertIn(key, rows[0], f"기존 키 {key} 가 사라졌다")

    def test_기존_함수는_status_인자를_그대로_받는다(self):
        from src.relations.graph_query import fetch_active_relations_for_asset
        conn, cur = _conn_returning([])
        fetch_active_relations_for_asset(conn, asset_id="a1", status="rejected")
        self.assertEqual(cur.execute.call_args[0][1][2], ["rejected"])


class TestNeighborFolding(unittest.TestCase):
    """같은 이웃에 붙은 이름표 여럿을 하나로 접는다 — 동시보유 접기.

    규칙·근거는 `approval_policy` 상단 "동시보유 접기" 주석이 정본이고, 규칙 자체의 검증은
    `test_approval_policy.TestChooseFoldedEdge` 가 한다. 여기서 보는 것은 **조회 경로에
    실제로 걸리는가**와 **기존 계약이 안 깨지는가** 둘이다.
    """

    def test_같은_이웃의_두_이름표가_한_건으로_접힌다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="proposed", kind="duplicate_near", conf=0.7, edge="e1", dst="a2"),
            _row(status="proposed", kind="same_domain", conf=0.7, edge="e2", dst="a2")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.70)
        self.assertEqual(len(rows), 1, "같은 이웃이 두 번 나오면 화면에 모순된 이름표가 붙는다")
        self.assertEqual(rows[0]["kind_code"], "same_domain")
        self.assertEqual(rows[0]["folded_kind_codes"], ["duplicate_near"])

    def test_이웃이_다르면_접지_않는다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="active", kind="duplicate_near", edge="e1", dst="a2"),
            _row(status="active", kind="same_domain", edge="e2", dst="a3")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual(len(rows), 2)

    def test_접힘이_없어도_folded_kind_codes_는_실린다(self):
        # 접힌 쪽에만 넣으면 소비처가 `.get()` 유무로 분기하고, 그 분기가 "접힘을 모르는
        # 코드"를 만든다. 항상 실어 두면 소비처는 리스트만 보면 된다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([_row(status="active")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual(rows[0]["folded_kind_codes"], [])

    def test_양방향_중복도_한_건으로_접힌다(self):
        # 비대칭 kind 는 캐논 순서 봉인 대상이 아니라 A→B·B→A 두 행이 생길 수 있다
        # (v4 에 `derived_from` 실사례 존재). 쌍 단위로 접으면 이웃이 두 번 나온다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="active", kind="derived_from", edge="e1", src="a1", dst="a2"),
            _row(status="active", kind="derived_from", edge="e2", src="a2", dst="a1")])
        rows = fetch_relations_for_asset(conn, asset_id="a1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_id"], "a2")

    def test_기존_키가_하나도_사라지지_않는다(self):
        # 하위호환 봉인 — 접기는 **추가**만 하고 기존 계약을 건드리지 않는다.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="proposed", kind="duplicate_near", conf=0.7, edge="e1", dst="a2"),
            _row(status="proposed", kind="same_domain", conf=0.7, edge="e2", dst="a2")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.70)
        for key in ("asset_id", "kind_code", "is_symmetric", "direction", "confidence",
                    "status", "topic", "reason", "edge_id", "file_name", "modality", "tier"):
            self.assertIn(key, rows[0], f"기존 키 {key} 가 사라졌다")

    def test_접혀서_대표_신뢰도가_낮아져도_같은_등급_안은_신뢰도순이다(self):
        # 접기가 남기는 행의 신뢰도는 그 이웃의 최댓값이 아닐 수 있다(약한 주장이 낮은
        # 점수로 남는 경우). 마지막 정렬이 등급만 보면 SQL 이 정해 준 "첫 등장 순"이
        # 남아 **같은 등급 안에서 신뢰도 역순**이 된다 — 반환 계약 위반.
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            # SQL 은 confidence DESC 로 준다: a2 의 dup(0.95)가 먼저, a2 의 sd(0.72)는 마지막.
            _row(status="proposed", kind="duplicate_near", conf=0.95, edge="e1", dst="a2"),
            _row(status="proposed", kind="duplicate_near", conf=0.80, edge="e2", dst="a3"),
            _row(status="proposed", kind="same_domain", conf=0.72, edge="e3", dst="a2")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.70)
        # a2 는 약한 주장(sd·0.72)으로 접힌다 → 0.80 인 a3 이 먼저 와야 한다.
        self.assertEqual([(r["asset_id"], r["confidence"]) for r in rows],
                         [("a3", 0.80), ("a2", 0.72)])

    def test_접은_뒤에도_등급_정렬이_유지된다(self):
        from src.relations.graph_query import fetch_relations_for_asset
        conn, _ = _conn_returning([
            _row(status="proposed", kind="same_domain", conf=0.9, edge="w1", dst="a3"),
            _row(status="active", kind="duplicate_near", conf=0.8, edge="s1", dst="a2"),
            _row(status="proposed", kind="same_domain", conf=0.8, edge="w2", dst="a2")])
        rows = fetch_relations_for_asset(conn, asset_id="a1",
                                         include_weak=True, min_conf_similarity=0.70)
        # a2 는 강칸(active)이 조합 규칙을 이겨 duplicate_near 로 남고, 강칸이 먼저 온다.
        self.assertEqual([(r["asset_id"], r["tier"]) for r in rows],
                         [("a2", "strong"), ("a3", "weak")])
        self.assertEqual(rows[0]["folded_kind_codes"], ["same_domain"])



def _conn_seq(results: list[list[dict]]):
    """실행 순서대로 **다른 결과**를 돌려주는 mock conn(질의 2개 이상인 함수용).

    위의 ``_conn_returning`` 은 모든 execute 에 같은 행을 준다. 메타 묶음 조회는 "노드 1건 →
    구성 자산 N건" 두 질의라 순서별 결과가 필요하다.

    Args:
        results: execute 순서대로 돌려줄 행 목록. 다 쓰면 빈 목록을 준다.

    Returns:
        ``(conn, cur)`` — ``cur.execute.call_args_list`` 로 SQL·바인딩을 검증한다.
    """
    from unittest.mock import MagicMock

    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__.return_value = cur
    queue = list(results)
    state: dict[str, list[dict]] = {"rows": []}

    def _execute(_sql, _params=None):
        state["rows"] = queue.pop(0) if queue else []
        return cur

    cur.execute.side_effect = _execute
    cur.fetchall.side_effect = lambda: list(state["rows"])
    cur.fetchone.side_effect = lambda: state["rows"][0] if state["rows"] else None
    conn.cursor.return_value = cur
    return conn, cur


class TestMmMetaOfAsset(unittest.TestCase):
    """084 T004 — 자산이 속한 **멀티모달 메타 목록**(``mm_meta_of_asset``).

    소속 엣지는 ``자산 → 개체`` **비대칭**이라 대칭 접힘(캐논 정렬)과 무관하다 — 그래서 여기서는
    양방향 매칭을 하지 않는다. 대신 조건 두 개가 반드시 SQL 에 있어야 한다: ``kind_code='mm_member'``
    (다른 종류 엣지가 섞이면 자산↔자산 관계가 메타로 보인다)와 ``dst 는 entity 노드``.

    노출 상태 기본값에 **``proposed`` 를 포함**하는 것이 이 기능의 생사다 — 초기에는 전건 proposed
    이므로 active 만 보면 화면이 영구히 빈다(081 번들 active-0 무동작의 재발 방지 · spec §7).
    """

    def _row(self, **over):
        """``mm_meta_of_asset`` SQL(dict_row) 한 행을 흉내.

        Args:
            **over: 덮어쓸 컬럼.

        Returns:
            행 dict.
        """
        base = {
            "entity_type": "장소",
            "entity_uid": "제주도",
            "name": "제주도",
            "edge_id": "018f0000-0000-7000-8000-0000000000e1",
            "status": "proposed",
            "reason": "kw=제주 장마|pv=mm_meta.v1|rv=1",
            "bundle_size": 14,
        }
        base.update(over)
        return base

    def test_sql_filters_kind_and_entity_dst(self) -> None:
        from src.relations.graph_query import mm_meta_of_asset

        conn, cur = _conn_seq([[]])
        mm_meta_of_asset(conn, asset_id="A")
        sql, params = cur.execute.call_args[0][0], cur.execute.call_args[0][1]
        compact = " ".join(sql.split())

        self.assertIn("rk.kind_code = %s", compact)          # 종류는 바인딩(하드코딩 금지)
        self.assertIn("node_kind = 'entity'", compact)        # dst 는 개체 노드
        self.assertIn("node_kind = 'asset'", compact)         # src 는 자산 노드
        self.assertIn("ge.status = ANY(%s)", compact)
        self.assertIn("mm_member", params)
        # 대칭 접힘 대상이 아니므로 양방향 매칭을 하지 않는다(했다면 개체가 src 인 엣지를 찾게 된다).
        self.assertNotIn("OR dn.asset_id", compact)

    def test_default_statuses_include_proposed(self) -> None:
        from src.relations.graph_query import mm_meta_of_asset

        conn, cur = _conn_seq([[]])
        mm_meta_of_asset(conn, asset_id="A")
        params = cur.execute.call_args[0][1]
        statuses = [p for p in params if isinstance(p, list)]
        self.assertTrue(statuses)
        for wanted in statuses:
            self.assertIn("proposed", wanted)

    def test_statuses_are_overridable(self) -> None:
        from src.relations.graph_query import mm_meta_of_asset

        conn, cur = _conn_seq([[]])
        mm_meta_of_asset(conn, asset_id="A", statuses=["active"])
        for param in cur.execute.call_args[0][1]:
            if isinstance(param, list):
                self.assertEqual(param, ["active"])

    def test_order_is_deterministic(self) -> None:
        from src.relations.graph_query import mm_meta_of_asset

        conn, cur = _conn_seq([[]])
        mm_meta_of_asset(conn, asset_id="A")
        compact = " ".join(cur.execute.call_args[0][0].split())
        self.assertIn("ORDER BY", compact)
        self.assertIn("en.entity_type, en.entity_uid", compact)

    def test_row_contract_and_uuid_to_str(self) -> None:
        import uuid as _uuid

        from src.relations.graph_query import mm_meta_of_asset

        edge_id = _uuid.UUID("018f0000-0000-7000-8000-0000000000e1")
        conn, _ = _conn_seq([[self._row(edge_id=edge_id)]])
        out = mm_meta_of_asset(conn, asset_id="A")

        self.assertEqual(set(out[0]), {"entity_type", "entity_uid", "name", "bundle_size",
                                      "edge_id", "status", "reason"})
        # 조회행 id → str(graph_query 관례 — 모의로는 못 잡는 실 DB 결함의 예방선).
        self.assertIsInstance(out[0]["edge_id"], str)
        self.assertEqual(out[0]["edge_id"], str(edge_id))
        self.assertEqual(out[0]["bundle_size"], 14)
        self.assertEqual(out[0]["name"], "제주도")

    def test_name_falls_back_to_uid(self) -> None:
        # canonical.name 이 없는 행(손 SQL·구버전)이라도 빈 라벨을 내보내지 않는다.
        from src.relations.graph_query import mm_meta_of_asset

        conn, _ = _conn_seq([[self._row(name=None)]])
        out = mm_meta_of_asset(conn, asset_id="A")
        self.assertEqual(out[0]["name"], "제주도")

    def test_bundle_size_counts_with_same_status_filter(self) -> None:
        # "확인된 N건"의 N 은 묶음 화면이 보여 줄 건수와 같은 기준이어야 한다 — 상태 필터를 함께 쓴다.
        from src.relations.graph_query import mm_meta_of_asset

        conn, cur = _conn_seq([[]])
        mm_meta_of_asset(conn, asset_id="A", statuses=["active", "proposed"])
        compact = " ".join(cur.execute.call_args[0][0].split())
        self.assertIn("count(*)", compact)
        self.assertEqual(sum(1 for p in cur.execute.call_args[0][1]
                             if p == ["active", "proposed"]), 2)


class TestMmMetaBundle(unittest.TestCase):
    """084 T004 — 메타 하나의 **구성 자산**(모달리티별 그룹 · ``mm_meta_bundle``).

    이 기능의 존재 이유가 크로스모달 응집이므로(텍스트·이미지·영상·오디오가 한 묶음) 반환은
    **모달리티별 그룹 + 건수**다. 두 가지 빈 경우를 구분한다: 메타 자체가 없으면 ``None``(호출부는
    404), 메타는 있고 자산이 0건이면 ``total=0``(수동 선등록한 빈 메타 · spec §6-1).
    """

    def _node_row(self, **over):
        """개체 노드 조회 행.

        Args:
            **over: 덮어쓸 컬럼.

        Returns:
            행 dict.
        """
        base = {"node_id": "018f0000-0000-7000-8000-0000000000f1",
                "name": "제주도",
                "canonical": {"name": "제주도"}}
        base.update(over)
        return base

    def _member(self, asset_id: str, modality: str, fs_path: str, **over):
        """구성 자산 행.

        Args:
            asset_id: 자산 id.
            modality: 모달리티.
            fs_path: 원본 경로(파일명 표시용).
            **over: 덮어쓸 컬럼.

        Returns:
            행 dict.
        """
        base = {"edge_id": f"e-{asset_id}", "status": "proposed",
                "reason": "kw=제주 장마|pv=mm_meta.v1|rv=1",
                "asset_id": asset_id, "modality": modality, "fs_path": fs_path}
        base.update(over)
        return base

    def test_missing_meta_returns_none(self) -> None:
        from src.relations.graph_query import mm_meta_bundle

        conn, cur = _conn_seq([[]])
        self.assertIsNone(mm_meta_bundle(conn, entity_type="장소", entity_uid="없는곳"))
        # 없는 메타에 구성 자산을 묻지 않는다(질의 1회로 끝난다).
        self.assertEqual(len(cur.execute.call_args_list), 1)

    def test_empty_meta_returns_zero_bundle(self) -> None:
        from src.relations.graph_query import mm_meta_bundle

        conn, _ = _conn_seq([[self._node_row()], []])
        out = mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도")
        assert out is not None
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["modalities"], [])
        self.assertEqual(out["name"], "제주도")
        # 등록/발굴 구분(spec §6-1) — canonical.source 부재는 발굴(auto)이다.
        self.assertEqual(out["source"], "auto")

    def test_groups_by_modality_with_counts(self) -> None:
        from src.relations.graph_query import mm_meta_bundle

        conn, _ = _conn_seq([
            [self._node_row()],
            [self._member("a1", "image", "/data/raw/사진1.png"),
             self._member("a2", "image", "/data/raw/사진2.png"),
             self._member("a3", "text", "/data/raw/문서.txt")],
        ])
        out = mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도")
        assert out is not None
        self.assertEqual(out["total"], 3)
        self.assertEqual([(g["modality"], g["count"]) for g in out["modalities"]],
                         [("image", 2), ("text", 1)])
        first = out["modalities"][0]["assets"][0]
        self.assertEqual(first["file_name"], "사진1.png")   # fs_path basename(기존 관례)
        self.assertEqual(first["asset_id"], "a1")
        self.assertEqual(set(first), {"asset_id", "modality", "file_name", "edge_id",
                                      "status", "reason"})

    def test_uuid_to_str_contract(self) -> None:
        import uuid as _uuid

        from src.relations.graph_query import mm_meta_bundle

        asset_id = _uuid.UUID("018f0000-0000-7000-8000-0000000000a1")
        edge_id = _uuid.UUID("018f0000-0000-7000-8000-0000000000e1")
        conn, _ = _conn_seq([
            [self._node_row()],
            [self._member(asset_id, "audio", "/data/raw/소리.mp3", edge_id=edge_id)],
        ])
        out = mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도")
        assert out is not None
        row = out["modalities"][0]["assets"][0]
        self.assertEqual(row["asset_id"], str(asset_id))
        self.assertEqual(row["edge_id"], str(edge_id))

    def test_sql_binds_kind_and_statuses(self) -> None:
        from src.relations.graph_query import mm_meta_bundle

        conn, cur = _conn_seq([[self._node_row()], []])
        mm_meta_bundle(conn, entity_type="장소", entity_uid="제주도", statuses=["active"])
        member_sql, member_params = (cur.execute.call_args_list[1][0][0],
                                     cur.execute.call_args_list[1][0][1])
        compact = " ".join(member_sql.split())
        self.assertIn("rk.kind_code = %s", compact)
        self.assertIn("node_kind = 'asset'", compact)
        self.assertIn("ge.status = ANY(%s)", compact)
        self.assertIn("mm_member", member_params)
        self.assertIn(["active"], member_params)
        # 모달리티·자산 순 결정적 정렬(같은 묶음이 매번 같은 순서로 보여야 한다).
        self.assertIn("ORDER BY", compact)

    def test_uid_is_matched_as_normalized_key(self) -> None:
        # entity_uid 컬럼에는 **정규화 키**만 들어 있다(persist 계약). 표기 그대로 들어온 값도
        # 같은 규칙으로 눌러 대조한다 — 멱등이라 이미 키인 값은 그대로다.
        from src.relations.graph_query import mm_meta_bundle

        conn, cur = _conn_seq([[]])
        mm_meta_bundle(conn, entity_type="장소", entity_uid=" 제주 도 ")
        self.assertIn("제주도", cur.execute.call_args_list[0][0][1])


class TestMmMetaDoesNotDisturbSymmetricQueries(unittest.TestCase):
    """084 회귀 가드 — 신규 조회가 기존 **대칭 엣지** 경로를 건드리지 않는다."""

    def test_existing_sql_constant_unchanged(self) -> None:
        from src.relations.graph_query import _FETCH_RELATIONS_SQL

        compact = " ".join(_FETCH_RELATIONS_SQL.split())
        # ADR 2026-05-28 의 핵심 두 줄이 그대로 있는가(양방향 매칭 · 양끝 asset 노드 조인).
        self.assertIn("sn.asset_id = %s OR dn.asset_id = %s", compact)
        self.assertIn("JOIN node dn ON dn.node_id = ge.dst_node AND dn.node_kind = 'asset'",
                      compact)

    def test_mm_meta_sql_is_separate_statement(self) -> None:
        # 두 경로가 같은 SQL 을 공유하면 한쪽 수정이 다른 쪽을 조용히 바꾼다 — 별 상수여야 한다.
        from src.relations import graph_query

        self.assertNotIn("mm_member", graph_query._FETCH_RELATIONS_SQL)
        self.assertNotEqual(graph_query._FETCH_RELATIONS_SQL, graph_query._MM_META_OF_ASSET_SQL)
