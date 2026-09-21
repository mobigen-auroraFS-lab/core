"""개체 집합 판정의 게이트를 **절대 하한**으로 바꾼다(2026-09-21 재보정).

무엇이 문제였나: 종전 기준은 ``top − 배경 ≥ 0.15`` 라는 **상대 격차**였다. 배경은 후보 20개 중
하위 절반 평균인데, 색인이 커지면 그 20개가 빽빽해져 배경이 올라가고 격차가 줄어든다. 즉
**자료가 늘수록 같은 질의가 조용히 막힌다.** 임계 0.15 는 개체가 82개이던 시절 값이고, 822개가
된 지금 개념 질의 통과율은 **3%** 다(실측 · 질의 102개).

무엇으로 바꾸나: ``top ≥ 0.44`` 라는 **절대 하한**. 코사인 값 자체는 색인이 몇 개든 변하지 않아
크기에 흔들리지 않는다 — 파일 검색이 하한 0.60 으로 재보정 없이 도는 것과 같은 구조다.

근거(실측 2026-09-21 · 개념 102 · 음성 217 = 난수 60 + 자료 밖 157):
  · 0.15 상대 격차 → 통과  3% · 음성 차단 100%
  · 0.44 절대 하한 → 통과 81% · 음성 차단  84%
0.44 는 통과율과 차단율의 합이 가장 큰 구간이다. 음성에 **자료 밖 질의**(「주식 투자 방법」처럼
말은 되지만 대상이 없는 것)를 넣은 것이 핵심이다 — 난수만으로는 어떤 기준이든 다 막아낸다.

🔴 순위 경로(``search_entities_hybrid``)는 **건드리지 않는다.** 아무도 부르지 않는 되돌림 경로라
이번 측정 대상이 아니었고, 재지 않은 경로의 임계를 바꾸는 것은 근거 없는 변경이다.
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from src.config import search_constants
from src.search import entity_search_os


class _Client:
    """kNN 본문에만 답하는 최소 대역(낱말 갈래는 빈 결과)."""

    def __init__(self, knn_hits: list[dict[str, Any]]) -> None:
        self._knn = knn_hits

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "knn" in json.dumps(body, ensure_ascii=False):
            return {"hits": {"total": {"value": len(self._knn), "relation": "eq"},
                             "hits": self._knn}}
        return {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}


def _knn(uid: str, cosine: float) -> dict[str, Any]:
    """코사인을 lucene knn ``_score``(=(1+cos)/2)로 되돌린 hit."""
    return {"_id": f"장소/{uid}", "_score": (1.0 + cosine) / 2.0,
            "_source": {"entity_type": "장소", "entity_uid": uid}}


# 🔴 넷 다 서로 붙어 있어 **상대 격차는 거의 0** 이다(0.50~0.47). 종전 기준이면 무조건 차단인데,
#    1등이 하한을 넘으므로 새 기준에서는 통과해야 한다 — 이 표본이 이번 변경의 핵심 증거다.
_FLAT_HIGH = [_knn("경주시", 0.50), _knn("안동시", 0.49), _knn("부여군", 0.48), _knn("전주시", 0.47)]
# 같은 모양인데 통째로 낮다 — 1등이 하한 아래라 막혀야 한다.
_FLAT_LOW = [_knn("경주시", 0.40), _knn("안동시", 0.39), _knn("부여군", 0.38), _knn("전주시", 0.37)]


class TestGateUsesAbsoluteFloor(unittest.TestCase):
    def test_평평해도_1등이_하한을_넘으면_통과한다(self) -> None:
        """🔴 종전 기준(상대 격차 0.15)이면 격차 0.015 라 차단됐다 — 그것이 결함이었다."""
        got = entity_search_os.semantic_entity_keys(
            _Client(_FLAT_HIGH), "idx", query_vector=[0.1] * 4)
        self.assertTrue(got.gate_passed)
        self.assertIn(("장소", "경주시"), got.keys)

    def test_1등이_하한_아래면_막힌다(self) -> None:
        got = entity_search_os.semantic_entity_keys(
            _Client(_FLAT_LOW), "idx", query_vector=[0.1] * 4)
        self.assertFalse(got.gate_passed)
        self.assertEqual(got.keys, frozenset())

    def test_하한은_호출부가_바꿀_수_있다(self) -> None:
        """운영에서 값을 조정할 수 있어야 한다 — 코드에 박으면 재배포 없이는 못 고친다."""
        got = entity_search_os.semantic_entity_keys(
            _Client(_FLAT_LOW), "idx", query_vector=[0.1] * 4, gate_floor=0.30)
        self.assertTrue(got.gate_passed)

    def test_상대_격차_상수는_남아_있지_않다(self) -> None:
        """0 으로 둔 값을 남기면 같은 값을 두 번 재는 코드가 다시 생긴다(실제로 겪었다)."""
        self.assertFalse(hasattr(search_constants, "ENTITY_SET_GATE_EPS_DEFAULT"))

    def test_하한_기본값은_실측에서_고른_값이다(self) -> None:
        self.assertEqual(search_constants.ENTITY_SET_GATE_FLOOR_DEFAULT, 0.44)

    def test_순위_경로_기본값은_건드리지_않는다(self) -> None:
        """재지 않은 경로의 임계를 바꾸면 근거 없는 변경이 된다."""
        self.assertEqual(search_constants.ENTITY_SEMANTIC_GATE_EPS_DEFAULT, 0.15)


class TestGateEdgeCases(unittest.TestCase):
    """경계·이상값 — 판정이 바뀐 자리라 여기서 조용히 새기 쉽다."""

    def test_같은_입력이면_같은_판정이다(self) -> None:
        """헌법 3조 — 결정 재현성. 하한 비교는 순수 계산이라 회차가 달라도 같아야 한다."""
        runs = [entity_search_os.semantic_entity_keys(
            _Client(_FLAT_HIGH), "idx", query_vector=[0.1] * 4) for _ in range(3)]
        self.assertEqual({r.gate_passed for r in runs}, {True})
        self.assertEqual({r.keys for r in runs}, {runs[0].keys})

    def test_후보가_0건이면_막힌다(self) -> None:
        """"막혔다"와 "후보가 없다"는 다른 사건이지만, 결과는 둘 다 빈 집합이어야 한다."""
        got = entity_search_os.semantic_entity_keys(
            _Client([]), "idx", query_vector=[0.1] * 4)
        self.assertFalse(got.gate_passed)
        self.assertEqual(got.sample_size, 0)

    def test_후보가_하나여도_하한으로_판정한다(self) -> None:
        """표본이 1개면 배경을 만들 수 없다(``gate_signal`` 이 baseline 0). 종전 상대 기준이면
        격차가 top 그대로라 거의 늘 통과했는데, 하한 기준에서는 그 우연이 사라진다."""
        self.assertTrue(entity_search_os.semantic_entity_keys(
            _Client([_knn("경주시", 0.50)]), "idx", query_vector=[0.1] * 4).gate_passed)
        self.assertFalse(entity_search_os.semantic_entity_keys(
            _Client([_knn("경주시", 0.40)]), "idx", query_vector=[0.1] * 4).gate_passed)

    def test_음수_코사인은_막힌다(self) -> None:
        """뜻이 반대인 쪽으로 가까운 것은 결과가 아니다 — 하한이 양수라 자동으로 걸러진다."""
        got = entity_search_os.semantic_entity_keys(
            _Client([_knn("경주시", -0.20), _knn("안동시", -0.30)]),
            "idx", query_vector=[0.1] * 4)
        self.assertFalse(got.gate_passed)

    def test_하한을_0으로_주면_전부_통과한다(self) -> None:
        """게이트를 끄는 길이 있어야 진단이 된다 — "게이트 탓인가"를 가르는 유일한 방법이다."""
        got = entity_search_os.semantic_entity_keys(
            _Client(_FLAT_LOW), "idx", query_vector=[0.1] * 4, gate_floor=0.0)
        self.assertTrue(got.gate_passed)

    def test_막히면_낱말_갈래는_그대로_남는다(self) -> None:
        """🔴 게이트는 ② 갈래에만 건다 — ① 을 함께 막으면 재현율이 통째로 무너진다."""
        class _Both:
            def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
                if "knn" in json.dumps(body, ensure_ascii=False):
                    return {"hits": {"total": {"value": len(_FLAT_LOW), "relation": "eq"},
                                     "hits": _FLAT_LOW}}
                hit = {"_id": "작품/훈민정음", "_score": 1.0,
                       "_source": {"entity_type": "작품", "entity_uid": "훈민정음"}}
                return {"hits": {"total": {"value": 1, "relation": "eq"}, "hits": [hit]}}

        got = entity_search_os.match_entity_keys(
            _Both(), "idx", query="한글", query_vector=[0.1] * 4)
        self.assertEqual(got.keys, {("작품", "훈민정음")})
        self.assertFalse(got.semantic_gate_passed)
        self.assertEqual(got.text_keys, {("작품", "훈민정음")})


class TestPerItemFloor(unittest.TestCase):
    """하한을 **후보 하나하나에도** 건다(2026-09-21 후속).

    종전에는 1등이 하한을 넘으면 후보 20개를 **전부** 내보냈다. 20등이 아무리 멀어도 나갔다.
    실측(개념 102 · 자료밖 157 · 난수 60): 자료 밖 질의가 평균 1.78건, 난수가 평균 7.00건을
    물어왔다. 개별 하한을 걸면 각각 **0.23건 · 0.93건** 으로 줄고, 놓치는 것은 **전혀 늘지
    않는다**(개념 0건 비율 19% 그대로). 잘려 나가는 것이 「바닷가 경치」의 해인사·팔만대장경·
    첨성대처럼 실제로 무관한 것들이라 그렇다.

    🔴 게이트 하한과 **같은 값**을 쓴다. 뜻이 다르다 — 게이트는 "이 질의를 믿을 만한가"(1등 기준),
    개별 하한은 "이 후보가 결과로 나갈 만한가"(각자 기준)다. 값이 같으니 결과적으로
    "하한을 넘는 것만 나간다"가 되고, 게이트 통과는 "적어도 하나는 남는다"와 같은 말이 된다.
    """

    def test_하한_아래_후보는_결과에서_빠진다(self) -> None:
        mixed = [_knn("경주시", 0.50), _knn("안동시", 0.46),
                 _knn("부여군", 0.41), _knn("전주시", 0.30)]
        got = entity_search_os.semantic_entity_keys(
            _Client(mixed), "idx", query_vector=[0.1] * 4)
        self.assertTrue(got.gate_passed)
        self.assertEqual(got.keys, {("장소", "경주시"), ("장소", "안동시")})

    def test_표본_수는_자른_뒤가_아니라_받은_그대로다(self) -> None:
        """``sample_size`` 는 진단값이다 — 자른 뒤 수를 넣으면 "후보가 적었나"를 알 수 없다."""
        mixed = [_knn("경주시", 0.50), _knn("부여군", 0.30)]
        got = entity_search_os.semantic_entity_keys(
            _Client(mixed), "idx", query_vector=[0.1] * 4)
        self.assertEqual(got.sample_size, 2)
        self.assertEqual(len(got.keys), 1)

    def test_1등만_넘으면_1등만_남는다(self) -> None:
        """게이트는 통과하되 결과가 하나뿐인 경우 — 종전에는 넷이 다 나갔다."""
        got = entity_search_os.semantic_entity_keys(
            _Client([_knn("경주시", 0.50), _knn("안동시", 0.20),
                     _knn("부여군", 0.10), _knn("전주시", 0.05)]),
            "idx", query_vector=[0.1] * 4)
        self.assertEqual(got.keys, {("장소", "경주시")})

    def test_하한을_0으로_주면_종전처럼_전부_나간다(self) -> None:
        """진단 경로 — 게이트와 개별 하한을 한꺼번에 끄면 옛 동작을 재현할 수 있다."""
        got = entity_search_os.semantic_entity_keys(
            _Client(_FLAT_LOW), "idx", query_vector=[0.1] * 4, gate_floor=0.0)
        self.assertEqual(len(got.keys), 4)

    def test_통과_여부는_남은_후보에서_유도된다(self) -> None:
        """🔴 1등을 따로 검사하지 않는다 — 1등이 최대값이라 "하나라도 남았나"와 같은 말이다.

        같은 값으로 두 번 재던 것을 하나로 접었다(2026-09-21). 결과는 동일하다.
        """
        for cos in (0.44, 0.50, 0.90):
            got = entity_search_os.semantic_entity_keys(
                _Client([_knn("경주시", cos), _knn("안동시", 0.01)]),
                "idx", query_vector=[0.1] * 4)
            self.assertTrue(got.gate_passed)
            self.assertGreaterEqual(len(got.keys), 1)


if __name__ == "__main__":
    unittest.main()
