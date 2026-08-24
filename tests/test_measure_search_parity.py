"""083 T111 — SC-04 랭킹 불변 parity 하니스의 순수 로직 단위 테스트.

하니스(``scripts/measure_search_parity.py``)는 두 가지를 한다:
  - **capture**: 골든 질의 전량을 실제로 검색해 버킷별 결과 id 순서를 스냅으로 저장(실 OpenSearch 필요).
  - **compare**: 변경 전/후 두 스냅을 비교해 **id·순위가 완전히 같은지** 판정(순수 계산).

여기서는 실 OS 가 필요 없는 **compare 쪽 순수 로직만** 덮는다(capture 는 사람이 실 OS 환경에서
실행한다 — 083 tasks G2 코어 PR 경계).

무엇을 봉인하나:
  - ``load_queries``: 골든 두 형식(리스트 / ``{"queries": [...]}``)에서 질의 문자열을 순서대로 뽑고
    중복은 한 번만 — 같은 질의를 두 번 재면 스냅이 커지고 비교가 무의미해진다.
  - ``bucket_ids``: ``search_hybrid`` 결과에서 **버킷별 id 순서**만 남긴다(``meta`` 는 비교 대상이
    아니다 — 지연·게이트 메타는 실행마다 달라서 넣으면 매번 "차이 있음"이 된다).
  - ``compare_snapshots``: 완전 일치면 차이 0, 순서·구성·질의 집합이 다르면 **질의 단위로** 보고.
"""

from __future__ import annotations

import unittest

from scripts.measure_search_parity import (
    bucket_ids,
    capture_snapshot,
    compare_snapshots,
    load_queries,
)


def _snap(results: dict[str, dict[str, list[str]]]) -> dict:
    """비교용 스냅 dict 를 만든다(메타는 비교에 쓰이지 않는다).

    Args:
        results: ``{질의: {버킷: [id, ...]}}``.

    Returns:
        스냅 dict.
    """
    return {"limit_per_bucket": 50, "results": results}


class TestLoadQueries(unittest.TestCase):
    """골든 파일에서 질의 목록을 뽑는 규칙."""

    def test_리스트_형식(self) -> None:
        # fixtures/search/golden_ko.json 형식: [{"query": ..., "relevant_asset_ids": [...]}, ...]
        golden = [{"query": "3D 프린팅", "relevant_asset_ids": ["a"]}, {"query": "가상현실"}]
        self.assertEqual(load_queries(golden), ["3D 프린팅", "가상현실"])

    def test_queries_키_형식(self) -> None:
        # golden_os.json 형식: {"queries": [{"id": ..., "query": ...}, ...]}
        golden = {"queries": [{"id": "q1", "query": "김치"}, {"id": "q2", "query": "발효"}]}
        self.assertEqual(load_queries(golden), ["김치", "발효"])

    def test_중복_질의는_한번만_순서_보존(self) -> None:
        golden = [{"query": "김치"}, {"query": "발효"}, {"query": "김치"}]
        self.assertEqual(load_queries(golden), ["김치", "발효"])

    def test_빈_질의는_버린다(self) -> None:
        golden = [{"query": ""}, {"query": "  "}, {"query": "김치"}, {}]
        self.assertEqual(load_queries(golden), ["김치"])

    def test_모양이_다른_골든은_즉시_ValueError(self) -> None:
        # 잘못된 파일로 "질의 0건 = 차이 0건" 이 되면 parity 를 통과한 것처럼 보인다(가짜 초록).
        with self.assertRaises(ValueError):
            load_queries({"items": [{"query": "김치"}]})


class TestBucketIds(unittest.TestCase):
    """검색 결과에서 비교 대상(버킷별 id 순서)만 남기는 규칙."""

    def test_버킷별_id_순서를_그대로_뽑는다(self) -> None:
        # 실제 계약: 버킷은 최상위가 아니라 "results" 아래에 중첩(search_hybrid 반환부 실측).
        result = {
            "query": "김치",
            "results": {
                "text_documents": [{"id": "a", "similarity": 0.9}, {"id": "b", "similarity": 0.7}],
                "image": [{"id": "c"}],
                "audio": [],
                "video": [],
            },
            "meta": {"took_ms": 12},
        }
        self.assertEqual(
            bucket_ids(result),
            {"text_documents": ["a", "b"], "image": ["c"], "audio": [], "video": []},
        )

    def test_results_없는_평면_모양은_즉시_ValueError(self) -> None:
        # 2026-08-24 실캡처 함정: 평면 모양을 조용히 순회하면 253질의 전부 빈 스냅이 됐다.
        # 계약 밖 모양은 빈 dict 로 넘기지 않고 즉시 실패해야 거짓 초록을 못 만든다.
        with self.assertRaises(ValueError):
            bucket_ids({"text_documents": [{"id": "a"}], "meta": {}})

    def test_meta_는_비교_대상이_아니다(self) -> None:
        # 지연·게이트 메타는 실행마다 달라 넣으면 매번 불일치가 된다.
        self.assertNotIn("meta", bucket_ids({"results": {"meta": {"took_ms": 3}, "image": []}}))

    def test_id_는_문자열로_정규화(self) -> None:
        # UUID 객체/문자열 혼재(조회행 UUID→str 관례)로 스냅이 갈라지지 않게 한다.
        self.assertEqual(bucket_ids({"results": {"image": [{"id": 7}]}}), {"image": ["7"]})


class TestCompareSnapshots(unittest.TestCase):
    """두 스냅 비교 — 완전 일치 판정과 차이 보고."""

    def test_완전_일치면_차이_없음(self) -> None:
        a = _snap({"김치": {"text_documents": ["x", "y"], "image": []}})
        self.assertEqual(compare_snapshots(a, _snap({"김치": {"text_documents": ["x", "y"], "image": []}})), [])

    def test_순위가_바뀌면_차이로_보고(self) -> None:
        # SC-04 의 핵심: 구성이 같아도 **순서**가 달라지면 랭킹이 변한 것이다.
        a = _snap({"김치": {"text_documents": ["x", "y"]}})
        b = _snap({"김치": {"text_documents": ["y", "x"]}})
        diffs = compare_snapshots(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertIn("김치", diffs[0])

    def test_결과_구성이_달라지면_차이로_보고(self) -> None:
        a = _snap({"김치": {"text_documents": ["x", "y"]}})
        b = _snap({"김치": {"text_documents": ["x"]}})
        self.assertEqual(len(compare_snapshots(a, b)), 1)

    def test_한쪽에만_있는_질의도_차이(self) -> None:
        # 스냅을 만든 골든이 다르면 "차이 0" 이 나와도 의미가 없다 — 질의 집합 불일치를 반드시 알린다.
        a = _snap({"김치": {"image": ["x"]}, "발효": {"image": ["y"]}})
        b = _snap({"김치": {"image": ["x"]}})
        diffs = compare_snapshots(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertIn("발효", diffs[0])

    def test_버킷_키_차이도_차이(self) -> None:
        a = _snap({"김치": {"image": ["x"], "audio": []}})
        b = _snap({"김치": {"image": ["x"]}})
        self.assertEqual(len(compare_snapshots(a, b)), 1)

    def test_질의가_많아도_다른_질의만_보고한다(self) -> None:
        a = _snap({"김치": {"image": ["x"]}, "발효": {"image": ["y"]}, "장독": {"image": ["z"]}})
        b = _snap({"김치": {"image": ["x"]}, "발효": {"image": ["y2"]}, "장독": {"image": ["z"]}})
        diffs = compare_snapshots(a, b)
        self.assertEqual(len(diffs), 1)
        self.assertIn("발효", diffs[0])

    def test_보고_순서는_결정적(self) -> None:
        # 같은 입력이면 같은 보고(헌법 3조) — 질의 이름 오름차순.
        a = _snap({"나": {"image": ["1"]}, "가": {"image": ["1"]}})
        b = _snap({"나": {"image": ["2"]}, "가": {"image": ["2"]}})
        diffs = compare_snapshots(a, b)
        self.assertEqual(len(diffs), 2)
        self.assertTrue(diffs[0].startswith("가") or "가" in diffs[0].split()[0])

    def test_results_없는_스냅은_즉시_ValueError(self) -> None:
        # 빈 dict 를 통과시키면 "질의 0건 → 차이 0건" 으로 가짜 초록이 난다.
        with self.assertRaises(ValueError):
            compare_snapshots({"limit_per_bucket": 50}, _snap({}))


class TestCaptureSnapshot(unittest.TestCase):
    """스냅 생성 — 검색 호출은 주입 seam 이라 실 OS 없이 계약을 검증한다."""

    def test_주입한_검색_seam_으로_스냅을_만든다(self) -> None:
        calls: list[tuple[str, int]] = []

        def _fake_search(query: str, *, limit_per_bucket: int) -> dict:
            calls.append((query, limit_per_bucket))
            return {"results": {"text_documents": [{"id": f"{query}-1"}]}, "meta": {"took_ms": 1}}

        snap = capture_snapshot(["김치", "발효"], limit_per_bucket=50, search_fn=_fake_search)

        self.assertEqual(calls, [("김치", 50), ("발효", 50)])
        self.assertEqual(snap["limit_per_bucket"], 50)
        self.assertEqual(snap["results"]["김치"], {"text_documents": ["김치-1"]})
        # 만들어진 스냅이 compare 계약을 그대로 만족해야 한다(자기 자신과 비교 → 차이 0).
        self.assertEqual(compare_snapshots(snap, snap), [])


if __name__ == "__main__":
    unittest.main()
