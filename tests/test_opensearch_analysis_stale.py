"""분석기 어긋남 판정이 **가장 나쁜 상태에서 침묵한다**(2026-09-23 · spec 101 G4).

무엇이 문제였나: `_analysis_stale` 이 「지금 색인의 분석기 설정이 비어 있으면 판단 보류」로
시작한다. 의도는 **읽지 못했을 때 괜한 경보를 울리지 말자**는 것이고 그 자체는 합리적이다.
그런데 **자동 생성된 색인은 분석기 설정이 애초에 없다** — 즉 *가장 나쁜 상태가 가장 조용하다.*

실측(2026-09-22 · k8s 신규 환경): 색인이 없는데 문서가 먼저 들어가 엔진이 값 모양만 보고
자동 생성했고, 1536차원 벡터가 `float` 으로 잡혀 벡터 검색이 전부 죽었다. 그 색인에 복구
도구를 비파괴로 돌렸더니 —

    인덱스 상태: updated | 색인 성공: 1 | 오류: 0      ← 매핑은 여전히 틀린 채

`analysis-stale` 경고 통로가 **있는데도** 울리지 않았다. 고칠 수 있었던 유일한 신호가 닫혀 있었다.

🔴 **「비었다」와 「못 읽었다」는 다르다.** 코드 정본(`build_index_body`)에는 분석기가 반드시
있으므로, 살아 있는 색인에 그것이 **없다는 사실 자체가 이상 신호**다. 읽기 실패일 때만 보류한다.
"""
from __future__ import annotations

import unittest
from typing import Any

from src.search import opensearch_sync as sync


def _wanted() -> dict[str, Any]:
    """코드 정본의 분석기 설정(형태만 맞춘 최소 대역)."""
    return sync.build_index_body()["settings"]["analysis"]


class TestAnalysisStale(unittest.TestCase):
    """세 경우를 갈라야 한다 — 비었다 · 못 읽었다 · 다르다."""

    def test_비어_있으면_어긋남으로_본다(self) -> None:
        """🔴 이 테스트가 spec 101 의 존재 이유다.

        자동 생성된 색인은 분석기가 없다. 그것을 「정상」으로 치면 아무도 알려주지 않는다.
        """
        self.assertTrue(sync._analysis_stale({}, _wanted()))

    def test_못_읽었으면_보류한다(self) -> None:
        """늑대소년 방지 — 권한·네트워크로 못 읽은 것까지 어긋남이라 하면 경보가 무의미해진다."""
        self.assertFalse(sync._analysis_stale(None, _wanted()))

    def test_다르면_어긋남이다(self) -> None:
        """종전 동작 — 회귀하지 않는다."""
        other = {"analyzer": {"nori_user": {"type": "custom", "tokenizer": "standard"}}}
        self.assertTrue(sync._analysis_stale(other, _wanted()))

    def test_같으면_어긋나지_않는다(self) -> None:
        """종전 동작 — 회귀하지 않는다."""
        self.assertFalse(sync._analysis_stale(_wanted(), _wanted()))


class _Client:
    """`indices.get_settings` 만 답하는 최소 대역."""

    def __init__(self, payload: Any, *, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises

    def indices_get_settings(self, index: str) -> Any:  # pragma: no cover - 대역 편의
        raise NotImplementedError

    @property
    def indices(self) -> Any:
        client = self

        class _Indices:
            @staticmethod
            def get_settings(index: str) -> Any:
                if client._raises:
                    raise RuntimeError("권한 없음")
                return client._payload

        return _Indices()


class TestLiveAnalysisDistinguishesFailure(unittest.TestCase):
    """읽기 실패는 ``None``, 설정이 없는 것은 ``{}`` — 부르는 쪽이 갈라 볼 수 있어야 한다."""

    def test_읽기_실패는_None(self) -> None:
        got = sync._live_analysis(_Client(None, raises=True), "assets")
        self.assertIsNone(got)

    def test_분석기가_없으면_빈_dict(self) -> None:
        """🔴 자동 생성된 색인이 바로 이 모양이다 — settings 는 읽히는데 analysis 만 없다."""
        payload = {"assets": {"settings": {"index": {"number_of_shards": "1"}}}}
        got = sync._live_analysis(_Client(payload), "assets")
        self.assertEqual(got, {})

    def test_있으면_그대로_돌려준다(self) -> None:
        analysis = {"analyzer": {"nori_user": {"type": "custom"}}}
        payload = {"assets": {"settings": {"index": {"analysis": analysis}}}}
        got = sync._live_analysis(_Client(payload), "assets")
        self.assertEqual(got, analysis)

    def test_색인_이름이_달라도_첫_항목을_쓴다(self) -> None:
        """별칭으로 조회하면 응답 키가 실제 색인 이름이다(종전 동작 · 회귀 방지)."""
        analysis = {"analyzer": {"nori_user": {"type": "custom"}}}
        payload = {"assets-000001": {"settings": {"index": {"analysis": analysis}}}}
        got = sync._live_analysis(_Client(payload), "assets")
        self.assertEqual(got, analysis)



class TestMappingStale(unittest.TestCase):
    """🔴 벡터 필드 타입이 어긋나면 **검색 전에** 알려야 한다(101 G4 · T003).

    실측(2026-09-22): 색인이 없는데 문서가 먼저 들어가 엔진이 자동 생성했고, 1536차원 벡터가
    ``float`` 으로 잡혔다. 그 색인은 **벡터 검색이 전부 죽는데** 상태 보고는 ``updated`` 였다.
    빠진 필드는 덧붙일 수 있지만 **타입 변경은 엔진이 허용하지 않는다** — 다시 만들어야 한다.
    그래서 '고칠 수 있는 것'과 성격이 다르고, 별도 상태로 알린다.
    """

    @staticmethod
    def _client(embedding_mapping: dict[str, Any]) -> Any:
        from tests.test_opensearch_sync import _FakeClient

        body = sync.build_index_body(dim=8)
        props = dict(body["mappings"]["properties"])
        props["embedding"] = embedding_mapping
        return _FakeClient(existing=True, live_props=props)

    def test_벡터가_float_이면_알린다(self) -> None:
        """자동 생성된 색인의 실제 모양이다."""
        client = self._client({"type": "float"})
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "mapping-stale")

    def test_정상_매핑은_조용하다(self) -> None:
        """회귀 — 제대로 만들어진 색인에서 헛울리면 안 된다."""
        body = sync.build_index_body(dim=8)
        from tests.test_opensearch_sync import _FakeClient

        client = _FakeClient(existing=True, live_props=body["mappings"]["properties"])
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "exists")

    def test_매핑_어긋남이_분석기_어긋남보다_먼저다(self) -> None:
        """둘 다 어긋나면 **더 심한 쪽**을 알린다 — 매핑은 재색인 없이는 절대 못 고친다."""
        body = sync.build_index_body(dim=8)
        props = dict(body["mappings"]["properties"])
        props["embedding"] = {"type": "float"}
        from tests.test_opensearch_sync import _FakeClient

        client = _FakeClient(existing=True, live_props=props, live_analysis={})
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "mapping-stale")

    def test_차원이_다르면_알린다(self) -> None:
        """같은 ``knn_vector`` 라도 차원이 다르면 못 고친다 — 1536D 는 헌법상 정본이다."""
        client = self._client({"type": "knn_vector", "dimension": 768})
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "mapping-stale")

    def test_객체_필드_하위_누락은_보강_소관이다(self) -> None:
        """🔴 과잉 판정 방지 — 하위 항목이 빠진 객체 필드는 **덧붙여 고칠 수 있다.**

        이것까지 어긋남이라 하면 정상적인 필드 보강(096)이 전부 'mapping-stale' 이 된다.
        """
        from tests.test_opensearch_sync import _FakeClient

        body = sync.build_index_body(dim=8)
        props = dict(body["mappings"]["properties"])
        props["filter_date"] = {"properties": {"created_at": {"type": "date"}}}
        client = _FakeClient(existing=True, live_props=props)
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "updated")

    def test_매핑을_못_읽으면_보류한다(self) -> None:
        """분석기와 같은 원칙 — 읽기 실패는 어긋남이 아니다."""
        from tests.test_opensearch_sync import _FakeClient

        client = _FakeClient(existing=True, mapping_unreadable=True)
        self.assertEqual(sync.ensure_index(client, "assets", dim=8), "exists")

if __name__ == "__main__":
    unittest.main()
