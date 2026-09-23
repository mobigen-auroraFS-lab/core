"""검색 파이프라인을 **만드는 주인**을 되찾는다(2026-09-23 · spec 101 G2).

무엇이 문제였나: 파일 검색은 순위를 **검색 엔진이 섞도록** 맡긴다(`assets-hybrid` 파이프라인).
그런데 그 파이프라인을 **만드는 코드가 어디에도 없다.** 027 이 재색인 도구에서 등록 기능을
지웠는데(그때는 쓰는 곳이 없었다) 그 뒤 파일 검색이 생기며 필요가 되살아났고, **쓰는 쪽만 남았다.**

빈 환경에서의 증상 —

    RequestError(400, 'illegal_argument_exception',
                 'Pipeline assets-hybrid is not defined')

`/file-search` 가 500 을 낸다. 2026-09-22 k8s 신규 환경에서 실제로 그랬고, 사람이 손으로
`PUT _search/pipeline/assets-hybrid` 를 해서 넘겼다. **그 손을 없애는 것**이 이 변경이다.

🔴 **가중치는 설정이 정본이다**(`OPENSEARCH_FUSION_WEIGHTS`). 등록 본문의 값이 그와 다르면
검색 순위가 **조용히** 어긋난다 — 오류가 안 나서 아무도 모른다.
"""
from __future__ import annotations

import unittest
from typing import Any

from src.search import opensearch_search as oss


class _Pipelines:
    """`search_pipeline.get/put` 만 있는 최소 대역."""

    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(existing or {})
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def get(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self.store)

    def put(self, *, id: str, body: dict[str, Any]) -> None:  # noqa: A002 - 엔진 API 이름
        self.puts.append((id, body))
        self.store[id] = body


class _Client:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.search_pipeline = _Pipelines(existing)


class TestSearchPipelineBody(unittest.TestCase):
    """본문은 **순수·결정적**이다 — 같은 가중치면 같은 문서."""

    def test_가중치가_그대로_실린다(self) -> None:
        body = oss.search_pipeline_body((0.5, 0.5))
        got = body["phase_results_processors"][0]["normalization-processor"]
        self.assertEqual(got["combination"]["parameters"]["weights"], [0.5, 0.5])
        self.assertEqual(got["normalization"]["technique"], "min_max")

    def test_순서는_BM25_다음_kNN_이다(self) -> None:
        """🔴 질의 본문의 서브쿼리 순서와 **같아야** 한다 — 뒤집히면 순위가 조용히 어긋난다."""
        body = oss.search_pipeline_body((0.3, 0.7))
        weights = body["phase_results_processors"][0]["normalization-processor"][
            "combination"]["parameters"]["weights"]
        self.assertEqual(weights, [0.3, 0.7], "앞이 BM25, 뒤가 kNN")

    def test_같은_입력이면_같은_본문이다(self) -> None:
        """헌법 3조 — 결정적이어야 멱등 등록이 성립한다."""
        self.assertEqual(oss.search_pipeline_body((0.5, 0.5)),
                         oss.search_pipeline_body((0.5, 0.5)))


class _NotFound(Exception):
    """opensearch-py 의 `NotFoundError` 흉내 — `status_code` 로 구분한다."""

    status_code = 404


class _Boom(Exception):
    """404 가 아닌 오류(권한·네트워크) 흉내."""

    status_code = 403


class _RaisingPipelines:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def get(self, **_kwargs: Any) -> dict[str, Any]:
        raise self._exc

    def put(self, *, id: str, body: dict[str, Any]) -> None:  # noqa: A002
        self.puts.append((id, body))


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self.search_pipeline = _RaisingPipelines(exc)


class TestEmptyEngineRaises404(unittest.TestCase):
    """🔴 파이프라인이 **하나도 없으면** 엔진이 404 를 던진다(2026-09-23 실측).

    빈 환경이 바로 그 상태다 — **이 함수가 가장 필요한 순간**. 404 를 「없음」으로 받지 않으면
    등록 자체가 실패한다. T023 빈 환경 재현 시험에서 드러났다(그 전 확인은 이미 하나가 있는
    상태여서 못 잡았다).
    """

    def test_404_면_없는_것으로_보고_만든다(self) -> None:
        client = _RaisingClient(_NotFound("empty"))
        self.assertEqual(
            oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5)), "created")
        self.assertEqual([pid for pid, _ in client.search_pipeline.puts], ["assets-hybrid"])

    def test_404_가_아니면_올린다(self) -> None:
        """권한·네트워크 문제까지 삼키면 조용한 실패가 된다."""
        client = _RaisingClient(_Boom("forbidden"))
        with self.assertRaises(_Boom):
            oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5))


class TestEnsureSearchPipeline(unittest.TestCase):
    """없으면 만들고, 있으면 그냥 둔다(멱등) — ``ensure_index`` 와 같은 규약."""

    def test_없으면_만든다(self) -> None:
        client = _Client()
        self.assertEqual(
            oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5)), "created")
        self.assertEqual([pid for pid, _ in client.search_pipeline.puts], ["assets-hybrid"])

    def test_있으면_그냥_둔다(self) -> None:
        """🔴 멱등 — 재색인을 돌릴 때마다 덮어쓰면 운영 중 순위가 흔들린다."""
        client = _Client({"assets-hybrid": {"description": "이미 있음"}})
        self.assertEqual(
            oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5)), "exists")
        self.assertEqual(client.search_pipeline.puts, [], "쓰기가 일어나면 안 된다")

    def test_두_번_불러도_한_번만_쓴다(self) -> None:
        client = _Client()
        oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5))
        second = oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5))
        self.assertEqual(second, "exists")
        self.assertEqual(len(client.search_pipeline.puts), 1)

    def test_다른_이름은_따로_만든다(self) -> None:
        client = _Client({"other": {}})
        self.assertEqual(
            oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.5, 0.5)), "created")

    def test_등록_본문에_가중치가_실린다(self) -> None:
        """🔴 설정과 등록값이 갈리면 검색 순위가 조용히 어긋난다 — 그 연결을 봉인한다."""
        client = _Client()
        oss.ensure_search_pipeline(client, "assets-hybrid", weights=(0.3, 0.7))
        (_pid, body), = client.search_pipeline.puts
        self.assertEqual(
            body["phase_results_processors"][0]["normalization-processor"]
            ["combination"]["parameters"]["weights"],
            [0.3, 0.7])


if __name__ == "__main__":
    unittest.main()
