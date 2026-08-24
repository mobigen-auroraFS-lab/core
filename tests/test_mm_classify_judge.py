"""085 T104 — 분류 스킬 **판정**(LLM seam) 단위 테스트(``src/mm_classify/judge.py``).

무엇을 검증하나 — 이 모듈의 핵심 계약은 **"실패"와 "해당없음"을 절대 섞지 않는 것**이다.

왜 그게 중요한가(spec §4 · 084 §2 동형 규율): LLM 이 잠깐 죽어 빈 응답을 주면 공통 seam
``complete_json`` 은 그것을 빈 dict ``{}`` 로 접어 돌려준다. 그 순간을 "라벨 없음 = 해당없음"으로
읽으면, 일시 장애가 **"판정 완료·해당없음"으로 영구히 굳는다**(행이 기록돼 재판정 대상에서 빠진다).
그래서 판정 함수는 성공/실패를 명시 구분해 돌려주고, 실패는 행을 남기지 않아 다음 배치가 다시 집는다.

또 하나 — **조용한 필터링 금지**: 어휘 밖 라벨이 하나라도 섞인 응답은 그 원소만 버리지 않고 **판정
전체를 실패**로 돌린다. 어휘 밖 값이 나오는 것은 프롬프트·설정이 잘못됐다는 신호이고, 몰래 걸러
버리면 그 신호가 사라진 채 절반짜리 판정이 저장된다.

모의 client 로 네트워크 0(테스트 가이드 §2 — ``complete_json`` 은 ``client=`` 주입 seam).
"""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import MagicMock

from src.mm_classify.judge import (
    JudgeFailure,
    SkillJudgement,
    interpret_response,
    judge_asset_labels,
)
from src.mm_classify.model import UNASSIGNED_LABEL_CODE, load_skill


def _skill(selection: str = "multi") -> Any:
    """더미 스킬(3라벨) — spec §1 구조·값은 더미(실 데이터 문구를 코드 레포에 넣지 않는다).

    Args:
        selection: 선택 정책(``"multi"``·``"single"``).

    Returns:
        검증을 통과한 ``ClassificationSkill``.
    """
    return load_skill(
        {
            "skill": "샘플 분류",
            "skill_code": "sample_skill",  # 필수 키(구현확정 G1) — 판정 계약에는 관여하지 않는다.
            "version": 2,
            "policy": {"selection": selection, "unassigned": "해당없음", "max_labels": 30},
            "labels": [
                {
                    "code": "alpha",
                    "name": "가라벨",
                    "definition": "가에 해당하는 내용이 중심인 자산",
                    "not": "나·다에 해당하는 내용",
                },
                {
                    "code": "beta",
                    "name": "나라벨",
                    "definition": "나에 해당하는 내용이 중심인 자산",
                    "not": "가·다에 해당하는 내용",
                },
                {
                    "code": "gamma",
                    "name": "다라벨",
                    "definition": "다에 해당하는 내용이 중심인 자산",
                    "not": "가·나에 해당하는 내용",
                },
            ],
        }
    )


def _client_returning(content: str) -> MagicMock:
    """OpenAI 호환 응답을 흉내내는 가짜 클라이언트(테스트 가이드 §2 골격).

    Args:
        content: LLM 이 돌려줄 원문 문자열(JSON 문자열 또는 깨진 문자열).

    Returns:
        ``chat.completions.create`` 가 그 원문을 담은 응답을 주는 MagicMock.
    """
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock()]
    client.chat.completions.create.return_value.choices[0].message.content = content
    return client


class TestSuccessMulti(unittest.TestCase):
    """multi 성공 경로 — 자산 하나가 라벨 1..N 개를 가질 수 있다(spec §1)."""

    def test_라벨_하나(self) -> None:
        result = interpret_response(_skill(), {"labels": ["나라벨"]})
        self.assertTrue(result.ok)
        self.assertIsNone(result.failure)
        self.assertEqual(result.label_names, ("나라벨",))
        self.assertEqual(result.label_codes, ("beta",))
        self.assertFalse(result.is_unassigned)

    def test_라벨_둘_multi_실측_유형(self) -> None:
        # multi 파일럿에서 실제로 나온 유형: 한 자산이 두 측면을 동시에 담고 있어 라벨 둘이
        # 정보 보존이 되는 경우("유래 설명 + 조리 과정" 같은 겹침). 값은 더미로 재현한다.
        result = interpret_response(_skill(), {"labels": ["가라벨", "다라벨"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_names, ("가라벨", "다라벨"))
        self.assertEqual(result.label_codes, ("alpha", "gamma"))

    def test_라벨_전부(self) -> None:
        result = interpret_response(_skill(), {"labels": ["다라벨", "가라벨", "나라벨"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("alpha", "beta", "gamma"))

    def test_응답_순서와_무관하게_설정_순서로_정규화한다(self) -> None:
        # 결정성(헌법 3조) — LLM 이 순서를 흔들어도 같은 판정이면 같은 결과가 나와야 한다
        # (저장 행은 순서가 없지만, 리포트·테스트·diff 는 순서에 의존한다).
        a = interpret_response(_skill(), {"labels": ["다라벨", "가라벨"]})
        b = interpret_response(_skill(), {"labels": ["가라벨", "다라벨"]})
        self.assertEqual(a, b)

    def test_같은_라벨_반복은_한번으로_센다(self) -> None:
        # 같은 라벨을 두 번 적은 것은 모순이 아니라 잉여다 — 중복만 없애고 성공으로 본다
        # (관계 파싱에서도 중복 제거는 정상 경로 · schema.parse_llm_edges 관례).
        result = interpret_response(_skill(), {"labels": ["가라벨", "가라벨"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("alpha",))

    def test_앞뒤_공백은_잘라서_맞춘다(self) -> None:
        result = interpret_response(_skill(), {"labels": [" 나라벨 "]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("beta",))


class TestSuccessUnassigned(unittest.TestCase):
    """해당없음 — **성공**이며 행으로 기록된다(실패와 구분 · spec §2)."""

    def test_미부여_단독은_성공(self) -> None:
        result = interpret_response(_skill(), {"labels": ["해당없음"]})
        self.assertTrue(result.ok)
        self.assertTrue(result.is_unassigned)
        self.assertEqual(result.label_names, ("해당없음",))
        self.assertEqual(result.label_codes, (UNASSIGNED_LABEL_CODE,))

    def test_미부여가_다른_라벨과_섞이면_실패(self) -> None:
        # "어디에도 해당 안 됨"과 "이 라벨에 해당함"은 동시에 참일 수 없다 — 모순 응답이므로
        # 한쪽을 골라 저장하지 않고 판정 실패로 돌린다(추측 금지).
        result = interpret_response(_skill(), {"labels": ["가라벨", "해당없음"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.UNASSIGNED_MIXED)
        self.assertEqual(result.label_codes, ())


class TestSinglePolicy(unittest.TestCase):
    """single 스킬 — 코드가 원소 1개를 강제한다(프롬프트 지시 + 검증 이중 장치)."""

    def test_하나면_성공(self) -> None:
        result = interpret_response(_skill("single"), {"labels": ["가라벨"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("alpha",))

    def test_둘이면_실패(self) -> None:
        result = interpret_response(_skill("single"), {"labels": ["가라벨", "나라벨"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.SINGLE_POLICY)

    def test_multi에서는_둘도_성공(self) -> None:
        # 같은 응답이 정책에 따라 갈린다 — 정책 집행이 코드에 있다는 증거.
        self.assertTrue(interpret_response(_skill("multi"), {"labels": ["가라벨", "나라벨"]}).ok)

    def test_중복_제거_후_판정한다(self) -> None:
        # ["가라벨","가라벨"] 은 사실상 하나다 — 원소 수만 세어 실패로 돌리면 억울하다.
        result = interpret_response(_skill("single"), {"labels": ["가라벨", "가라벨"]})
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("alpha",))


class TestFailureShapes(unittest.TestCase):
    """망가진 응답 — 전부 **실패**이며 "해당없음"으로 뭉개지 않는다(spec §4)."""

    def test_빈_dict는_실패(self) -> None:
        # ``complete_json`` 이 빈 응답·파싱 실패를 접어 주는 모양이 바로 이것이다.
        # 이것을 "라벨 없음"으로 읽으면 일시 장애가 판정 완료로 굳는다.
        result = interpret_response(_skill(), {})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_dict가_아니면_실패(self) -> None:
        for bad in ([], "해당없음", None, 3, ["가라벨"]):
            with self.subTest(bad=bad):
                result = interpret_response(_skill(), bad)
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_labels_키_없음은_실패(self) -> None:
        # 파일럿 문안의 옛 계약(``{"label": …}`` 단수)으로 답한 경우가 여기 걸린다.
        result = interpret_response(_skill(), {"label": "가라벨"})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.LABELS_MISSING)

    def test_labels가_배열이_아니면_실패(self) -> None:
        # 문자열 하나를 주면 글자 단위로 순회돼 쓰레기 라벨이 생긴다 — 승격하지 않고 실패로.
        for bad in ("가라벨", {"name": "가라벨"}, 3, None):
            with self.subTest(bad=bad):
                result = interpret_response(_skill(), {"labels": bad})
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.LABELS_NOT_LIST)

    def test_빈_배열은_실패(self) -> None:
        # 미부여는 ``["해당없음"]`` 으로 **명시**해야 한다 — 빈 배열은 지시를 못 따른 응답이다.
        result = interpret_response(_skill(), {"labels": []})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.LABELS_EMPTY)

    def test_원소가_문자열이_아니면_실패(self) -> None:
        for bad in ([3], [None], [{"name": "가라벨"}], ["가라벨", 3], ["  "]):
            with self.subTest(bad=bad):
                result = interpret_response(_skill(), {"labels": bad})
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.ELEMENT_NOT_TEXT)


class TestFailureOutOfVocabulary(unittest.TestCase):
    """어휘 밖 원소가 **하나라도** 있으면 판정 전체 실패(조용한 필터링 금지)."""

    def test_어휘_밖_단독_실패(self) -> None:
        result = interpret_response(_skill(), {"labels": ["없는라벨"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.OUT_OF_VOCAB)

    def test_어휘_안과_섞여도_전체_실패(self) -> None:
        # 🔴 여기서 "가라벨만 저장"으로 타협하면 설정·프롬프트 결함 신호가 사라진다.
        result = interpret_response(_skill(), {"labels": ["가라벨", "없는라벨"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.OUT_OF_VOCAB)
        self.assertEqual(result.label_codes, ())
        self.assertEqual(result.label_names, ())

    def test_라벨코드로_답하면_실패(self) -> None:
        # 어휘는 **표시명**이다(프롬프트가 표시명을 나열한다). 코드로 답하는 것은 지시 위반이며
        # 코드↔이름을 임의로 보정하면 이름 개정 후 판정이 조용히 달라진다.
        result = interpret_response(_skill(), {"labels": ["alpha"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.OUT_OF_VOCAB)

    def test_설명이_덧붙은_라벨도_실패(self) -> None:
        result = interpret_response(_skill(), {"labels": ["가라벨(확실함)"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.OUT_OF_VOCAB)

    def test_실패에도_원인_발췌를_남긴다(self) -> None:
        # 운영에서 "왜 실패했나"를 되짚을 수 있어야 한다(리포트·로그용 · 값은 잘라서 담는다).
        result = interpret_response(_skill(), {"labels": ["없는라벨"]})
        self.assertIn("없는라벨", result.detail)

    def test_실패는_라벨을_비워_돌려준다(self) -> None:
        # 호출부가 실수로 ``label_codes`` 만 보고 저장하더라도 빈 값이라 행이 생기지 않는다
        # (실패 = 행 미기록 · spec §2 · 이중 안전장치).
        for response in ({}, {"labels": []}, {"labels": ["없는라벨"]}):
            with self.subTest(response=response):
                result = interpret_response(_skill(), response)
                self.assertEqual(result.label_codes, ())
                self.assertEqual(result.label_names, ())


class TestJudgeThroughSeam(unittest.TestCase):
    """``judge_asset_labels`` — LLM 단일 seam 경유(헌법 2조)·모의 client 로 네트워크 0."""

    def test_모의_client_성공(self) -> None:
        client = _client_returning(json.dumps({"labels": ["나라벨"]}, ensure_ascii=False))
        result = judge_asset_labels(_skill(), "더미 요약", ["가키워드"], client=client)
        self.assertTrue(result.ok)
        self.assertEqual(result.label_codes, ("beta",))

    def test_temperature가_0이다(self) -> None:
        # 결정 재현성(헌법 3조·2조) — seam 기본값이 0 임을 판정 경로에서 확인한다.
        client = _client_returning('{"labels": ["해당없음"]}')
        judge_asset_labels(_skill(), "더미 요약", [], client=client)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0)

    def test_프롬프트에_스킬_라벨이_실려_나간다(self) -> None:
        client = _client_returning('{"labels": ["가라벨"]}')
        judge_asset_labels(_skill(), "더미 요약", ["가키워드"], client=client)
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("가라벨", sent)
        self.assertIn("가키워드", sent)
        self.assertIn("더미 요약", sent)

    def test_깨진_JSON은_실패로_반환한다(self) -> None:
        # seam 이 ``{}`` 로 접어 주는 경로 — 예외를 던지지 않고 **실패로 표시**해 돌려준다
        # (배치가 자산 하나 때문에 죽지 않도록. 실패 격리).
        result = judge_asset_labels(_skill(), "더미 요약", [], client=_client_returning("JSON 아님"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_빈_응답도_실패다(self) -> None:
        result = judge_asset_labels(_skill(), "더미 요약", [], client=_client_returning(""))
        self.assertFalse(result.ok)

    def test_요약_None도_호출된다(self) -> None:
        # 요약이 없는 자산(빈 STT 등)에서도 판정 자체는 시도된다(키워드만으로 판단).
        client = _client_returning('{"labels": ["해당없음"]}')
        result = judge_asset_labels(_skill(), None, None, client=client)
        self.assertTrue(result.ok)
        self.assertTrue(result.is_unassigned)

    def test_같은_응답이면_같은_판정(self) -> None:
        # 결정성 — 같은 입력·같은 응답이면 같은 결과(헌법 3조).
        payload = '{"labels": ["가라벨", "다라벨"]}'
        first = judge_asset_labels(_skill(), "더미 요약", [], client=_client_returning(payload))
        second = judge_asset_labels(_skill(), "더미 요약", [], client=_client_returning(payload))
        self.assertEqual(first, second)


class TestJudgementShape(unittest.TestCase):
    """반환 객체의 모양 — 성공/실패가 **한 필드로** 판별 가능해야 한다."""

    def test_불변객체다(self) -> None:
        result = interpret_response(_skill(), {"labels": ["가라벨"]})
        self.assertIsInstance(result, SkillJudgement)
        with self.assertRaises(FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_성공이면_failure가_None이고_실패면_값이_있다(self) -> None:
        self.assertIsNone(interpret_response(_skill(), {"labels": ["가라벨"]}).failure)
        self.assertIsNotNone(interpret_response(_skill(), {}).failure)

    def test_실패_사유_어휘가_닫혀있다(self) -> None:
        # 리포트가 실패를 사유별로 셀 수 있어야 한다(T110 diff 리포트 — 실패 건수).
        self.assertEqual(
            {member.value for member in JudgeFailure},
            {
                "response_shape",
                "labels_missing",
                "labels_not_list",
                "labels_empty",
                "element_not_text",
                "out_of_vocab",
                "single_policy",
                "unassigned_mixed",
            },
        )


if __name__ == "__main__":
    unittest.main()
