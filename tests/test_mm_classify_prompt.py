"""085 T103 — 분류 스킬 **프롬프트 조립** 단위 테스트(``src/mm_classify/prompt.py``).

무엇을 검증하나: 스킬 설정(라벨명·정의문·경계)이 **빠짐없이·같은 순서로** 프롬프트에 실리는지,
그리고 선택 정책(multi/single)에 따라 **출력 지시가 갈라지는지**를 못 박는다.

왜 중요한가(파일럿 발견 1 — "정의문이 곧 분류"): 판정은 정의문 문구에 충실하다. 정의문이나 경계
문장이 한 줄이라도 빠지면 그 라벨은 이름만 남고, LLM 이 자기 상식으로 채운다. 즉 **프롬프트에
실리지 않은 설정은 존재하지 않는 설정**이다.

또 하나의 계약: **스킬 1개 = 호출 1개**(085 plan §Global Constraints). 여러 스킬을 한 프롬프트에
섞으면 라벨 격리가 깨지고 버전 분리도 불가능해진다 — 그래서 함수는 스킬 하나만 받는다(시그니처로 봉인).

DB·LLM·네트워크 불필요한 순수 단위 테스트다.
"""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from src.mm_classify.model import load_skill
from src.mm_classify.prompt import (
    PROMPT_VERSION,
    SUMMARY_MAX_CHARS,
    build_classification_prompt,
)


def _skill(selection: str = "multi") -> Any:
    """더미 스킬(2라벨)을 만든다 — spec §1 구조·값은 더미.

    Args:
        selection: 선택 정책(``"multi"`` 또는 ``"single"``) — 출력 지시 분기를 검증하려고 바꾼다.

    Returns:
        검증을 통과한 ``ClassificationSkill``.
    """
    return load_skill(
        {
            "skill": "샘플 분류",
            "version": 1,
            "policy": {"selection": selection, "unassigned": "해당없음", "max_labels": 30},
            "labels": [
                {
                    "code": "alpha",
                    "name": "가라벨",
                    "definition": "가에 해당하는 내용이 중심인 자산",
                    "not": "나에 해당하는 내용",
                },
                {
                    "code": "beta",
                    "name": "나라벨",
                    "definition": "나에 해당하는 내용이 중심인 자산",
                    "not": "가에 해당하는 내용",
                },
            ],
        }
    )


class TestPromptVersion(unittest.TestCase):
    """``PROMPT_VERSION`` 은 문안의 지문이다 — 문안을 고치면 이 값도 올려야 한다."""

    def test_값이_고정돼있다(self) -> None:
        # 이 단언이 깨지면 "문안을 고쳤다"는 뜻이다 — 버전을 올리고 이 기대값도 함께 바꾼다.
        # 문안이 조용히 바뀌면 과거 판정과 현재 판정을 같은 근거로 착각하게 된다
        # (헌법 3조 — 저장된 사실의 재현).
        self.assertEqual(PROMPT_VERSION, "mm_classify.v1")

    def test_요약_상한이_spec_250자(self) -> None:
        # spec §4 — 입력은 "요약 앞 250자 + 키워드".
        self.assertEqual(SUMMARY_MAX_CHARS, 250)


class TestSingleSkillPerCall(unittest.TestCase):
    """스킬 1개 = 호출 1개(혼합 금지)를 시그니처로 봉인한다."""

    def test_스킬을_하나만_받는다(self) -> None:
        params = inspect.signature(build_classification_prompt).parameters
        # ``skills``(복수)를 받는 순간 한 프롬프트에 여러 스킬이 섞일 수 있다 — 그 문을 닫는다.
        self.assertEqual(set(params), {"skill", "summary", "keywords"})
        self.assertNotIn("skills", params)


class TestLabelBlock(unittest.TestCase):
    """라벨 나열 — 이름·정의문·경계가 전부, 설정 순서대로."""

    def test_모든_라벨의_이름_정의_경계가_실린다(self) -> None:
        text = build_classification_prompt(_skill(), summary="요약 본문", keywords=["가키워드"])
        for name, definition, exclusion in (
            ("가라벨", "가에 해당하는 내용이 중심인 자산", "나에 해당하는 내용"),
            ("나라벨", "나에 해당하는 내용이 중심인 자산", "가에 해당하는 내용"),
        ):
            with self.subTest(name=name):
                self.assertIn(name, text)
                self.assertIn(definition, text)
                self.assertIn(exclusion, text)
        # 파일럿 문안의 경계 표기("아닌 것: …")를 유지한다.
        self.assertIn("아닌 것", text)

    def test_라벨_순서는_설정_순서다(self) -> None:
        text = build_classification_prompt(_skill(), summary="요약", keywords=[])
        self.assertLess(text.index("가라벨"), text.index("나라벨"))

    def test_스킬명이_실린다(self) -> None:
        # 어떤 분류표로 보는지가 판정 맥락이다(파일럿 문안 "분류표(스킴: …)").
        self.assertIn("샘플 분류", build_classification_prompt(_skill(), summary="요약", keywords=[]))

    def test_미부여_라벨과_그_정의가_실린다(self) -> None:
        # 미부여 선택지를 주지 않으면 무관한 자산에도 억지로 라벨이 붙는다(파일럿 발견 2).
        text = build_classification_prompt(_skill(), summary="요약", keywords=[])
        self.assertIn("해당없음", text)
        self.assertIn("어디에도 해당하지 않음", text)

    def test_어휘를_그대로_쓰라고_지시한다(self) -> None:
        # judge 는 어휘 밖 원소를 **판정 실패**로 돌린다(spec §4) — 실패는 재대상 비용이라
        # 프롬프트에서 먼저 막는다(지시 + 검증 이중 장치).
        text = build_classification_prompt(_skill(), summary="요약", keywords=[])
        self.assertIn("그대로", text)


class TestSelectionBranch(unittest.TestCase):
    """multi/single 출력 지시 분기(spec §1 — v1 에서 둘 다 구현)."""

    def test_multi는_모두_고르라고_지시한다(self) -> None:
        text = build_classification_prompt(_skill("multi"), summary="요약", keywords=[])
        self.assertIn("모두", text)
        self.assertNotIn("하나만", text)

    def test_single은_하나만_고르라고_지시한다(self) -> None:
        text = build_classification_prompt(_skill("single"), summary="요약", keywords=[])
        self.assertIn("하나", text)
        self.assertNotIn("모두", text)

    def test_두_정책_모두_labels_배열로_답하게_한다(self) -> None:
        # spec §4 — 출력 계약은 ``{"labels": [...]}`` 하나다(single 도 원소 1개 배열).
        for selection in ("multi", "single"):
            with self.subTest(selection=selection):
                text = build_classification_prompt(_skill(selection), summary="요약", keywords=[])
                self.assertIn('"labels"', text)

    def test_두_정책의_문안이_다르다(self) -> None:
        self.assertNotEqual(
            build_classification_prompt(_skill("multi"), summary="요약", keywords=[]),
            build_classification_prompt(_skill("single"), summary="요약", keywords=[]),
        )


class TestAssetInputBlock(unittest.TestCase):
    """자산 입력 — 요약 앞 250자 + 키워드(spec §4)."""

    def test_요약을_250자로_자른다(self) -> None:
        summary = "가" * 240 + "나" * 40  # 280자 — 250자 경계를 넘긴다
        text = build_classification_prompt(_skill(), summary=summary, keywords=[])
        self.assertIn(summary[:SUMMARY_MAX_CHARS], text)
        self.assertNotIn(summary, text)

    def test_키워드가_한국어_그대로_실린다(self) -> None:
        # ensure_ascii=False — \uXXXX 로 이스케이프되면 LLM 이 읽을 재료가 나빠진다.
        text = build_classification_prompt(_skill(), summary="요약", keywords=["가키워드", "나키워드"])
        self.assertIn("가키워드", text)
        self.assertNotIn("\\u", text)

    def test_키워드_비문자열은_문자열로_바꿔_싣는다(self) -> None:
        # 요약기 산출물이 숫자·None 을 섞어 줄 수 있다 — 조립이 죽지 않아야 한다.
        text = build_classification_prompt(_skill(), summary="요약", keywords=[3, None, "가키워드"])
        self.assertIn("가키워드", text)
        self.assertIn("3", text)

    def test_요약이_없어도_죽지_않고_None을_노출하지_않는다(self) -> None:
        # DB 요약이 NULL 인 자산이 있다(빈 STT 등) — 프롬프트에 "None" 이 글자로 새면 안 된다.
        text = build_classification_prompt(_skill(), summary=None, keywords=None)
        self.assertNotIn("None", text)

    def test_키워드가_비면_빈_배열로_표기한다(self) -> None:
        self.assertIn("[]", build_classification_prompt(_skill(), summary="요약", keywords=[]))


class TestDeterminism(unittest.TestCase):
    """같은 입력이면 같은 프롬프트(헌법 3조 — 결정 재현성의 전제)."""

    def test_두번_조립해도_같은_문자열(self) -> None:
        args: dict[str, Any] = {"summary": "요약 본문", "keywords": ["가키워드", "나키워드"]}
        self.assertEqual(
            build_classification_prompt(_skill(), **args),
            build_classification_prompt(_skill(), **args),
        )

    def test_키워드_순서는_보존한다(self) -> None:
        # 정렬하지 않는다 — 요약기가 준 순서가 곧 중요도 순서라는 관례(관계 프롬프트와 동형).
        text = build_classification_prompt(_skill(), summary="요약", keywords=["나키워드", "가키워드"])
        self.assertLess(text.index("나키워드"), text.index("가키워드"))


if __name__ == "__main__":
    unittest.main()
