"""084 T015 — 멀티모달 메타 **설명 생성**(``src/mm_meta/describe.py``) 단위 테스트.

무엇을 검증하나 — 메타 카드에 붙는 "이 묶음에 무엇이 들어 있는지" 한 문장(spec §8-1)이다.
검증 축 셋:

1. 🔴 **환각 억제 문안이 문안에 남아 있다.** 재료는 **구성 자산 요약뿐**이고 프롬프트가 "외부 지식
   금지"를 명시한다. 이 한 줄이 빠지면 LLM 이 백과사전 지식으로 문장을 쓰고, 그러면 코퍼스에 없는
   사실이 카드에 실려 **검증할 방법이 사라진다**(설명이 이상해도 근거를 댈 수 없다). 문안 규칙
   (상투어 금지·60자·명사형)도 파일럿 기준선이라 함께 못 박는다.
2. **성공/실패 명시 구분**(``judge`` 와 같은 규율). ``complete_json`` 은 빈 응답·파싱 실패를 빈
   dict 로 접어 주므로, 그것을 "설명 없음"으로 읽으면 **일시 장애가 빈 설명으로 굳는다**. 실패는
   저장하지 않아야 다음 배치가 다시 만든다(재생성 대상 판정은 저장된 값으로 하기 때문이다).
3. **길이 상한 두 단계**. 60자는 **문안 규칙**(soft — 파일럿 최대 67자가 정상 통과했다)이고,
   ``DESCRIPTION_MAX_CHARS`` 는 **코드 상한**(hard — 폭주 차단)이다. 하나로 합치면 파일럿 기준선을
   깨거나(60 에서 자르면 정상 산출물을 버린다) 폭주를 놓친다(상한이 없으면 카드가 무너진다).

파일럿 실측(2026-08-24 · 116 묶음): 실패 0 · 결정성 95.7% · 평균 38자 · 최대 67자 · 상투어 0건.

모의 client 주입으로 네트워크 0(테스트 가이드 §2). ⚠️ 여기 쓰인 요약·이름은 전부 더미다.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

from src.mm_meta.describe import (
    DESC_PROMPT_VERSION,
    DESCRIPTION_KEY,
    DESCRIPTION_MAX_CHARS,
    DESCRIPTION_TARGET_CHARS,
    MEMBER_SUMMARY_MAX_CHARS,
    DescribeFailure,
    MetaDescription,
    build_description_prompt,
    describe_meta,
    interpret_description,
)
from src.mm_meta.judge import PROMPT_VERSION

# 더미 재료 — (모달리티, 요약) 쌍이 설명의 유일한 입력이다.
_MEMBERS = [("image", "가 오름 전경 사진"), ("text", "가 지역 기후와 장마 특징")]


def _response(description: object) -> dict[str, object]:
    """계약 모양의 응답을 만든다: ``{"설명": "…"}``.

    Args:
        description: ``설명`` 값. 계약 위반 값(숫자·dict 등)도 그대로 실어 실패 경로를 검증한다.

    Returns:
        ``complete_json`` 이 돌려줄 모양의 dict.
    """
    return {DESCRIPTION_KEY: description}


def _client_returning(content: str) -> MagicMock:
    """OpenAI 호환 응답을 흉내내는 가짜 클라이언트(테스트 가이드 §2 골격).

    Args:
        content: LLM 이 돌려줄 원문 문자열(JSON 문자열 또는 깨진 문자열).

    Returns:
        ``chat.completions.create`` 가 그 원문을 주는 MagicMock.
    """
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock()]
    client.chat.completions.create.return_value.choices[0].message.content = content
    return client


class TestDescriptionPromptShape(unittest.TestCase):
    """문안 조립(순수) — 같은 입력이면 같은 문안(결정성 · 헌법 3조)."""

    def test_버전_상수가_있다(self) -> None:
        # 이 값이 ``canonical.desc_prompt_version`` 으로 영속돼 재생성 대상 판정의 축이 된다.
        self.assertTrue(DESC_PROMPT_VERSION)
        self.assertIn("mm_meta", DESC_PROMPT_VERSION)

    def test_판정_문안_버전과_다른_지문이다(self) -> None:
        # 🔴 같은 값이면 판정 문안을 고칠 때 설명이 전량 재생성되고(비용) 그 반대도 마찬가지다 —
        #    두 문안은 따로 늙는다.
        self.assertNotEqual(DESC_PROMPT_VERSION, PROMPT_VERSION)

    def test_문안_스냅샷(self) -> None:
        # 파일럿 문안이 **기준선**이다(실패 0·상투어 0 의 근거). 문안을 고치면 이 단정이 먼저 깨지고,
        # 그때는 DESC_PROMPT_VERSION 도 함께 올려야 한다(저장된 설명이 어느 문안 산출인지 남는다).
        expected = "\n".join([
            '"가개체"(장소) 로 묶인 자산 2건의 요약이다.',
            "- (image) 가 오름 전경 사진",
            "- (text) 가 지역 기후와 장마 특징",
            "",
            "이 묶음에 **무엇이 들어 있는지** 한 문장으로 요약하라. 규칙:",
            '- "이 개체는~"·"이 묶음은~" 같은 상투적 서두를 쓰지 마라. 내용부터 바로 쓴다.',
            "- 위 요약에 없는 사실을 새로 만들지 마라(외부 지식 금지).",
            "- 60자 이내. 명사형으로 끝낸다.",
            'JSON 하나로만 답하라: {"설명": "..."}',
        ])
        self.assertEqual(build_description_prompt("가개체", "장소", _MEMBERS), expected)

    def test_외부_지식_금지가_문안에_있다(self) -> None:
        # 🔴 환각 억제의 핵심 — 빼면 코퍼스에 없는 사실이 카드에 실리고 검증 경로가 사라진다.
        prompt = build_description_prompt("가개체", "장소", _MEMBERS)
        self.assertIn("외부 지식 금지", prompt)
        self.assertIn("위 요약에 없는 사실", prompt)

    def test_상투어_금지_규칙이_문안에_있다(self) -> None:
        # 초안은 2문장이었고 첫 문장이 "이 개체는 …입니다" 류 무정보 서두가 됐다(spec §8-1) →
        # 1문장 + 상투어 금지로 조였다. 파일럿 상투어 잔존 0건이 이 규칙의 실측 근거다.
        prompt = build_description_prompt("가개체", "장소", _MEMBERS)
        self.assertIn("이 개체는~", prompt)
        self.assertIn("이 묶음은~", prompt)
        self.assertIn("상투적 서두", prompt)

    def test_길이_규칙이_상수와_연동된다(self) -> None:
        # 문안의 숫자를 손으로 적으면 상수와 갈린다 — 갈리면 "60자 규칙인데 왜 80자가 오나"가 된다.
        self.assertEqual(DESCRIPTION_TARGET_CHARS, 60)
        self.assertIn(f"{DESCRIPTION_TARGET_CHARS}자 이내", build_description_prompt(
            "가개체", "장소", _MEMBERS))

    def test_명사형_종결_규칙이_있다(self) -> None:
        self.assertIn("명사형", build_description_prompt("가개체", "장소", _MEMBERS))

    def test_한_문장을_요구한다(self) -> None:
        self.assertIn("한 문장", build_description_prompt("가개체", "장소", _MEMBERS))

    def test_출력_계약이_문안에_있다(self) -> None:
        prompt = build_description_prompt("가개체", "장소", _MEMBERS)
        self.assertIn(DESCRIPTION_KEY, prompt)
        self.assertIn("JSON 하나로만", prompt)

    def test_이름과_타입이_실린다(self) -> None:
        prompt = build_description_prompt("가개체", "인물", _MEMBERS)
        self.assertIn("가개체", prompt)
        self.assertIn("인물", prompt)

    def test_구성원_순서를_보존한다(self) -> None:
        # 재료 순서는 조회(asset_id 순)가 정한다 — 여기서 다시 섞으면 결정성이 깨진다.
        prompt = build_description_prompt("가개체", "장소", _MEMBERS)
        self.assertLess(prompt.index("가 오름 전경 사진"), prompt.index("가 지역 기후"))

    def test_자산_건수가_재료_줄_수와_같다(self) -> None:
        # 🔴 건수와 줄 수가 어긋나면 LLM 에 모순된 재료를 주는 것이다(N 은 실제 실린 줄 수여야 한다).
        prompt = build_description_prompt("가개체", "장소", _MEMBERS * 3)
        self.assertIn("자산 6건의 요약이다", prompt)

    def test_요약이_없는_구성원은_싣지_않고_건수도_줄어든다(self) -> None:
        members = [("image", "가 오름 전경 사진"), ("audio", None), ("text", "   ")]
        prompt = build_description_prompt("가개체", "장소", members)
        self.assertIn("자산 1건의 요약이다", prompt)
        self.assertNotIn("(audio)", prompt)
        self.assertNotIn("None", prompt)  # None 이 글자로 새면 안 된다

    def test_모달리티가_없으면_unknown으로_적는다(self) -> None:
        # 빈 STT 자산의 modality 가 실제로 'unknown' 이다(DB 어휘를 그대로 쓴다 — 새 말을 만들지 않는다).
        prompt = build_description_prompt("가개체", "장소", [(None, "가 요약")])
        self.assertIn("- (unknown) 가 요약", prompt)

    def test_요약은_150자로_자른다(self) -> None:
        # 재료가 길수록 프롬프트가 부풀어 지연·결정성이 나빠진다(spec §8-1 재료 = 각 150자).
        self.assertEqual(MEMBER_SUMMARY_MAX_CHARS, 150)
        prompt = build_description_prompt("가개체", "장소", [("text", "가" * 400)])
        self.assertIn("가" * MEMBER_SUMMARY_MAX_CHARS, prompt)
        self.assertNotIn("가" * (MEMBER_SUMMARY_MAX_CHARS + 1), prompt)

    def test_주입한_상한이_문안에_반영된다(self) -> None:
        prompt = build_description_prompt("가개체", "장소", [("text", "가" * 400)],
                                          summary_max_chars=50)
        self.assertIn("가" * 50, prompt)
        self.assertNotIn("가" * 51, prompt)

    def test_기본값을_그대로_주입하면_같은_문안이다(self) -> None:
        self.assertEqual(
            build_description_prompt("가개체", "장소", _MEMBERS,
                                     summary_max_chars=MEMBER_SUMMARY_MAX_CHARS),
            build_description_prompt("가개체", "장소", _MEMBERS),
        )

    def test_상한_0이하는_거부한다(self) -> None:
        # 0 이면 재료 없는 프롬프트가 조용히 나가고 설명이 통째로 환각이 된다 → fail-fast.
        for bad in (0, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    build_description_prompt("가개체", "장소", _MEMBERS, summary_max_chars=bad)

    def test_이름이나_타입이_비면_거부한다(self) -> None:
        # 이름 없는 카드에 설명을 붙일 이유가 없다 — 재료 문제가 아니라 호출 오류이므로 예외다.
        for name, entity_type in (("", "장소"), ("   ", "장소"), ("가개체", ""), ("가개체", "  ")):
            with self.subTest(name=name, entity_type=entity_type):
                with self.assertRaises(ValueError):
                    build_description_prompt(name, entity_type, _MEMBERS)

    def test_이름과_요약의_앞뒤_공백은_다듬는다(self) -> None:
        prompt = build_description_prompt("  가개체  ", " 장소 ", [("  image  ", "  가 요약  ")])
        self.assertIn('"가개체"(장소) 로 묶인', prompt)
        self.assertIn("- (image) 가 요약", prompt)

    def test_같은_입력이면_같은_문안이다(self) -> None:
        self.assertEqual(build_description_prompt("가개체", "장소", _MEMBERS),
                         build_description_prompt("가개체", "장소", _MEMBERS))


class TestInterpretSuccess(unittest.TestCase):
    """정상 응답 해석 — 한 문장을 설명으로."""

    def test_설명_한_문장(self) -> None:
        result = interpret_description(_response("가 지역의 오름 전경과 장마 기후 자료"))
        self.assertTrue(result.ok)
        self.assertIsNone(result.failure)
        self.assertEqual(result.description, "가 지역의 오름 전경과 장마 기후 자료")

    def test_앞뒤_공백과_줄바꿈은_다듬는다(self) -> None:
        # 카드 한 줄에 그대로 나가는 값이다 — 개행이 남으면 화면이 흐트러진다.
        result = interpret_description(_response("  가 지역 자료\n"))
        self.assertEqual(result.description, "가 지역 자료")

    def test_60자_초과도_통과하되_표시된다(self) -> None:
        # 파일럿 최대 67자(60자 초과 1건)가 정상 산출물이었다 — 버리지 않고 배치가 셀 수 있게 알린다.
        text = "가" * 67
        result = interpret_description(_response(text))
        self.assertTrue(result.ok)
        self.assertEqual(result.description, text)
        self.assertTrue(result.exceeds_target)

    def test_60자_이내면_초과_표시가_없다(self) -> None:
        result = interpret_description(_response("가" * DESCRIPTION_TARGET_CHARS))
        self.assertTrue(result.ok)
        self.assertFalse(result.exceeds_target)

    def test_코드_상한_경계는_통과한다(self) -> None:
        result = interpret_description(_response("가" * DESCRIPTION_MAX_CHARS))
        self.assertTrue(result.ok)


class TestInterpretFailure(unittest.TestCase):
    """🔴 망가진 응답은 전부 **실패** — "설명 없음"으로 뭉개지 않는다(저장하면 굳는다)."""

    def test_빈_dict는_실패다(self) -> None:
        # ``complete_json`` 이 빈 응답·파싱 실패를 접어 주는 모양이 바로 이것이다.
        result = interpret_description({})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.RESPONSE_SHAPE)
        self.assertEqual(result.description, "")

    def test_dict가_아니면_실패다(self) -> None:
        for bad in ([], "설명", None, 3, [{"설명": "가"}]):
            with self.subTest(bad=bad):
                result = interpret_description(bad)
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, DescribeFailure.RESPONSE_SHAPE)

    def test_설명_키가_없으면_실패다(self) -> None:
        result = interpret_description({"요약": "가 지역 자료"})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.DESCRIPTION_MISSING)

    def test_설명이_문자열이_아니면_실패다(self) -> None:
        for bad in (3, ["가"], {"text": "가"}, None, True):
            with self.subTest(bad=bad):
                result = interpret_description(_response(bad))
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, DescribeFailure.DESCRIPTION_NOT_STR)

    def test_설명이_공백이면_실패다(self) -> None:
        for bad in ("", "   ", "\n"):
            with self.subTest(bad=bad):
                result = interpret_description(_response(bad))
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, DescribeFailure.DESCRIPTION_EMPTY)

    def test_코드_상한을_넘으면_실패다(self) -> None:
        # 폭주(재료를 통째로 되풀거나 목록을 나열) 차단 — 잘라 저장하면 문장이 중간에서 끊긴 채
        # 카드에 실리고, 그것이 정상 산출물인지 폭주인지 나중에 알 수 없다.
        result = interpret_description(_response("가" * (DESCRIPTION_MAX_CHARS + 1)))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.DESCRIPTION_TOO_LONG)
        self.assertEqual(result.description, "")

    def test_상한은_파일럿_최대치보다_넉넉하다(self) -> None:
        # 파일럿 최대 67자를 막으면 정상 산출물을 버린다 — 상한은 규칙(60자)의 여유분이어야 한다.
        self.assertGreater(DESCRIPTION_MAX_CHARS, 67)

    def test_실패에는_원인_발췌가_남는다(self) -> None:
        self.assertIn("요약", interpret_description({"요약": "가"}).detail)

    def test_실패는_설명을_비워_돌려준다(self) -> None:
        # 호출부가 실수로 ``description`` 만 보고 저장해도 빈 값이 굳지 않는다(이중 안전장치).
        for response in ({}, {"요약": "가"}, _response("  "), _response(3)):
            with self.subTest(response=response):
                self.assertEqual(interpret_description(response).description, "")


class TestDescribeThroughSeam(unittest.TestCase):
    """``describe_meta`` — LLM 단일 seam 경유(헌법 2조)·모의 client 로 네트워크 0."""

    def test_모의_client_성공(self) -> None:
        payload = json.dumps({DESCRIPTION_KEY: "가 지역 오름 전경과 기후 자료"}, ensure_ascii=False)
        result = describe_meta("가개체", "장소", _MEMBERS, client=_client_returning(payload))
        self.assertTrue(result.ok)
        self.assertEqual(result.description, "가 지역 오름 전경과 기후 자료")

    def test_temperature가_0이다(self) -> None:
        # 결정 재현성(헌법 3조·2조). 생성문이라 완전 일치는 아니지만(파일럿 95.7%) 흔들림을 최소로.
        client = _client_returning(json.dumps({DESCRIPTION_KEY: "가 요약"}, ensure_ascii=False))
        describe_meta("가개체", "장소", _MEMBERS, client=client)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0)

    def test_프롬프트에_재료가_실려_나간다(self) -> None:
        client = _client_returning('{"설명": "가 요약"}')
        describe_meta("가개체", "장소", _MEMBERS, client=client)
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("가개체", sent)
        self.assertIn("가 오름 전경 사진", sent)
        self.assertIn("외부 지식 금지", sent)

    def test_깨진_JSON은_실패로_반환한다(self) -> None:
        # 예외를 던지지 않는다 — 메타 하나 때문에 배치가 죽으면 안 된다(실패 격리).
        result = describe_meta("가개체", "장소", _MEMBERS, client=_client_returning("JSON 아님"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.RESPONSE_SHAPE)

    def test_빈_응답도_실패다(self) -> None:
        result = describe_meta("가개체", "장소", _MEMBERS, client=_client_returning(""))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.RESPONSE_SHAPE)

    def test_재료가_없으면_LLM을_부르지_않는다(self) -> None:
        # 재료 없는 호출은 비용만 들고, 결과는 환각뿐이다.
        client = _client_returning('{"설명": "가 요약"}')
        result = describe_meta("가개체", "장소", [], client=client)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.NO_MEMBERS)
        client.chat.completions.create.assert_not_called()

    def test_요약이_전부_비면_재료_없음이다(self) -> None:
        client = _client_returning('{"설명": "가 요약"}')
        result = describe_meta("가개체", "장소", [("image", None), ("text", "  ")], client=client)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, DescribeFailure.NO_MEMBERS)
        client.chat.completions.create.assert_not_called()

    def test_이름이_비면_LLM을_부르기_전에_터진다(self) -> None:
        client = _client_returning('{"설명": "가 요약"}')
        with self.assertRaises(ValueError):
            describe_meta("", "장소", _MEMBERS, client=client)
        client.chat.completions.create.assert_not_called()

    def test_같은_응답이면_같은_결과다(self) -> None:
        payload = json.dumps({DESCRIPTION_KEY: "가 요약"}, ensure_ascii=False)
        first = describe_meta("가개체", "장소", _MEMBERS, client=_client_returning(payload))
        second = describe_meta("가개체", "장소", _MEMBERS, client=_client_returning(payload))
        self.assertEqual(first, second)


class TestDescriptionShape(unittest.TestCase):
    """반환 객체의 모양 — 성공/실패가 **한 필드로** 갈린다(``judge`` 와 같은 규율)."""

    def test_불변객체다(self) -> None:
        result = interpret_description(_response("가 요약"))
        self.assertIsInstance(result, MetaDescription)
        with self.assertRaises(FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_성공이면_failure가_None이고_실패면_값이_있다(self) -> None:
        self.assertIsNone(interpret_description(_response("가 요약")).failure)
        self.assertIsNotNone(interpret_description({}).failure)

    def test_실패는_초과_표시를_켜지_않는다(self) -> None:
        # 실패는 설명이 없는 상태다 — 길이 통계에 섞이면 리포트가 거짓이 된다.
        self.assertFalse(interpret_description({}).exceeds_target)

    def test_실패_사유_어휘가_닫혀있다(self) -> None:
        # 배치 리포트가 실패를 사유별로 셀 수 있어야 한다(judge 의 닫힌 어휘와 같은 이유).
        self.assertEqual(
            {member.value for member in DescribeFailure},
            {
                "no_members",
                "response_shape",
                "description_missing",
                "description_not_str",
                "description_empty",
                "description_too_long",
            },
        )


if __name__ == "__main__":
    unittest.main()
