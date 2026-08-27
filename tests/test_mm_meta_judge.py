"""084 T002 — 멀티모달 메타 **판정**(LLM seam)·프롬프트 단위 테스트(``src/mm_meta/judge.py``).

무엇을 검증하나 — 두 계약이다.

1. 🔴 **"판정 실패"와 "개체 0"을 절대 섞지 않는다**(spec §2). 공통 seam ``complete_json`` 은 빈 응답·
   파싱 실패를 빈 dict ``{}`` 로 접어 준다. 그것을 "개체를 못 찾음"으로 읽으면 LLM 이 잠깐 죽은
   순간이 **"판정 완료·개체 0"으로 영구히 굳는다** — 판정 이력(lineage `entity.judged.v1`)이
   기록돼 다음 배치가 그 자산을 다시 집지 않기 때문이다(spec §6 대상 선별).
   비유하면 설문 회수다: "응답 안 옴"(다시 보내야 함)과 "해당 없음이라고 답함"(집계 대상)은 다른
   사건인데, 둘을 같은 칸에 적으면 무엇을 다시 물어야 할지 알 수 없게 된다.
2. **키워드 단위 관용**(spec §2): 응답에서 어떤 키워드가 빠졌거나 타입이 어휘 밖이면 **그 키워드만**
   판정 없음으로 흡수한다(예외 전파 금지). 자산 전체를 실패로 돌리지 않는다 — 개체 추출은 "빠지면
   묶음이 작아질 뿐 검색은 무손실"인 더하기 축이라, 부분 성공이 쓸모 있다(ADR 08-20 맥락 3).

문안 기준선은 사전 검증의 재현 스크립트(`fixtures/entity_bundle/full_asset_entity_judge.py`)이며,
A/B 통과분(검증 §6-③)의 규칙 ⓐ무대·소속 수식은 추출 ⓑ상대 지시어·막연 범주는 null ⓒ동음이의는
문맥으로 판별 을 얹었다. ⚠️ 지정·자격 규칙('정식 종목' 류)은 프롬프트에 **없다** — LLM 이 그 경계에서
비일관해 결정적 스톱패턴(``rules.STOP_PATTERNS``)으로 이관했다(spec §3).

모의 client 주입으로 네트워크 0(테스트 가이드 §2).
"""

from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

from src.mm_meta.judge import (
    JUDGEMENT_KEY,
    PROMPT_VERSION,
    PROMPT_VERSION_WITHOUT_TYPE_DEFS,
    SUMMARY_MAX_CHARS,
    EntityJudgement,
    JudgeFailure,
    build_entity_prompt,
    interpret_response,
    judge_asset_entities,
    prompt_version_for,
)
from src.mm_meta.rules import ENTITY_TYPE_DEFS, ENTITY_TYPE_ORDER, EntityTypeDef, apply_rules


def _response(judgement: dict[str, object]) -> dict[str, object]:
    """계약 모양의 응답을 만든다: ``{"판정": {키워드: {"entity":…, "type":…}}}``.

    Args:
        judgement: 키워드별 판정 dict.

    Returns:
        ``complete_json`` 이 돌려줄 모양의 dict.
    """
    return {JUDGEMENT_KEY: judgement}


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


class TestPromptShape(unittest.TestCase):
    """프롬프트 조립(순수) — 같은 입력이면 같은 문안(결정성 · 헌법 3조)."""

    def test_버전_상수가_있다(self) -> None:
        # 문안의 지문 — ``reason`` 스탬프의 ``pv=`` 로 영속돼 "어떤 문안이 만든 판정인가"를 되짚는다.
        self.assertTrue(PROMPT_VERSION)
        self.assertIn("mm_meta", PROMPT_VERSION)

    def test_요약과_키워드가_실린다(self) -> None:
        prompt = build_entity_prompt("가나다 더미 요약", ["가키워드", "나키워드"])
        self.assertIn("가나다 더미 요약", prompt)
        self.assertIn("가키워드", prompt)
        self.assertIn("나키워드", prompt)

    def test_요약은_250자로_자른다(self) -> None:
        # spec §2 입력 계약(요약 앞 250자). 길수록 프롬프트가 부풀어 지연·결정성이 나빠진다.
        self.assertEqual(SUMMARY_MAX_CHARS, 250)
        prompt = build_entity_prompt("가" * 400, ["가키워드"])
        self.assertIn("가" * SUMMARY_MAX_CHARS, prompt)
        self.assertNotIn("가" * (SUMMARY_MAX_CHARS + 1), prompt)

    def test_요약이_없어도_None이_글자로_새지_않는다(self) -> None:
        # 요약 없는 자산(빈 STT 등)이 실제로 있다 — 그때 "None" 이 프롬프트에 박히면 안 된다.
        prompt = build_entity_prompt(None, ["가키워드"])
        self.assertNotIn("None", prompt)

    def test_키워드는_한글_그대로_JSON_배열로_들어간다(self) -> None:
        # ``\\uXXXX`` 로 이스케이프되면 LLM 이 읽을 재료가 나빠진다(ensure_ascii=False).
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        self.assertIn('["가키워드"]', prompt)

    def test_키워드_순서를_보존한다(self) -> None:
        prompt = build_entity_prompt("더미 요약", ["나키워드", "가키워드"])
        self.assertLess(prompt.index("나키워드"), prompt.index("가키워드"))

    def test_타입_어휘_5종이_고정_순서로_실린다(self) -> None:
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        positions = [prompt.index(entity_type) for entity_type in ENTITY_TYPE_ORDER]
        self.assertEqual(positions, sorted(positions))

    def test_A_B_통과_규칙_3종이_문안에_있다(self) -> None:
        # 검증 §6-③ 에서 일관 작동이 입증된 규칙만 프롬프트에 둔다.
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        self.assertIn("지시 대상", prompt)  # ⓐ 무대·소재지·소속·주체 수식은 추출
        for word in ("국내", "해외", "우리나라", "전국"):  # ⓑ 상대 지시어는 null
            with self.subTest(word=word):
                self.assertIn(word, prompt)
        self.assertIn("동음이의", prompt)  # ⓒ 문맥으로 판별

    def test_지정_자격_규칙은_문안에_없다(self) -> None:
        # 🔴 LLM 이 이 경계에서 비일관했다(골프 제거/역도 잔류·김수녕 과잉 제거) → 결정적
        #    스톱패턴으로 이관. 프롬프트에 되돌리면 그 흔들림이 함께 돌아온다(spec §3).
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        for word in ("정식 종목", "세계문화유산", "인류무형문화유산"):
            with self.subTest(word=word):
                self.assertNotIn(word, prompt)

    def test_출력_계약이_문안에_있다(self) -> None:
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        self.assertIn(JUDGEMENT_KEY, prompt)
        self.assertIn("entity", prompt)
        self.assertIn("type", prompt)

    def test_같은_입력이면_같은_문안이다(self) -> None:
        first = build_entity_prompt("더미 요약", ["가키워드", "나키워드"])
        second = build_entity_prompt("더미 요약", ["가키워드", "나키워드"])
        self.assertEqual(first, second)


class TestInterpretSuccess(unittest.TestCase):
    """정상 응답 해석 — 키워드별 개체·타입을 판정 목록으로."""

    def test_개체_하나(self) -> None:
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": "제주도", "type": "장소"}})
        )
        self.assertTrue(result.ok)
        self.assertIsNone(result.failure)
        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.entities[0].name, "제주도")
        self.assertEqual(result.entities[0].entity_type, "장소")
        self.assertEqual(result.entities[0].keyword, "가키워드")
        self.assertFalse(result.is_empty)

    def test_키워드_순서대로_돌려준다(self) -> None:
        # 결정성 — 응답 dict 순서가 흔들려도 **입력 키워드 순서**로 정렬해야 대표 키워드 선택
        # (rules.apply_rules 의 첫 판정 우선)이 재현된다.
        keywords = ["가키워드", "나키워드"]
        response = _response({
            "나키워드": {"entity": "경복궁", "type": "장소"},
            "가키워드": {"entity": "제주도", "type": "장소"},
        })
        result = interpret_response(keywords, response)
        self.assertEqual([entity.keyword for entity in result.entities], keywords)

    def test_개체_0도_성공이다(self) -> None:
        # 🔴 실패와 구분되는 지점 — 이력을 남기고 엣지는 만들지 않는다(spec §2).
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": None, "type": None}})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.entities, ())
        self.assertTrue(result.is_empty)

    def test_여러_키워드가_같은_개체를_가리켜도_그대로_돌려준다(self) -> None:
        # 접기는 규칙(rules)의 일이다 — 판정은 본 대로 돌려준다(층 분리).
        keywords = ["가키워드", "나키워드"]
        result = interpret_response(keywords, _response({
            "가키워드": {"entity": "제주도", "type": "장소"},
            "나키워드": {"entity": "제주도", "type": "장소"},
        }))
        self.assertEqual(len(result.entities), 2)
        self.assertEqual(len(apply_rules(result.entities)), 1)

    def test_표기_앞뒤_공백은_다듬는다(self) -> None:
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": " 제주도 ", "type": "장소"}})
        )
        self.assertEqual(result.entities[0].name, "제주도")

    def test_응답_키의_공백_차이를_흡수한다(self) -> None:
        # LLM 이 키워드를 되풀어 쓸 때 공백이 흔들리는 일이 있다 — 표기 키로 대조해 붙인다.
        result = interpret_response(
            ["가 키워드"], _response({"가키워드": {"entity": "제주도", "type": "장소"}})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.entities[0].keyword, "가 키워드")  # 원문 키워드를 보존한다

    def test_판정은_규칙을_적용하지_않는다(self) -> None:
        # 층 분리 확인: 규칙 제외는 rules 의 일이다. judge 가 미리 걸러 버리면 "LLM 이 무엇을
        # 말했나"와 "규칙이 무엇을 떨궜나"를 구분할 수 없다(diff 리포트가 사유를 못 센다).
        # 🔴 예시를 `북극` 으로 바꿨다(spec 087 T005) — `대한민국` 은 규칙이 아니라 **정의문**
        #    소관이 됐다. 층 분리 자체는 그대로이므로 규칙에 남은 항목으로 확인한다.
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": "북극", "type": "장소"}})
        )
        self.assertEqual(result.entities[0].name, "북극")
        self.assertEqual(apply_rules(result.entities), ())


class TestInterpretPerKeywordTolerance(unittest.TestCase):
    """키워드 단위 흡수 — **그 키워드만** 판정 없음(자산 전체 실패가 아니다 · spec §2)."""

    def test_응답에서_빠진_키워드는_판정_없음(self) -> None:
        result = interpret_response(
            ["가키워드", "나키워드"],
            _response({"가키워드": {"entity": "제주도", "type": "장소"}}),
        )
        self.assertTrue(result.ok)
        self.assertEqual([entity.keyword for entity in result.entities], ["가키워드"])

    def test_타입_어휘_밖은_그_키워드만_버린다(self) -> None:
        result = interpret_response(
            ["가키워드", "나키워드"],
            _response({
                "가키워드": {"entity": "제주도", "type": "지명"},  # 어휘 밖
                "나키워드": {"entity": "경복궁", "type": "장소"},
            }),
        )
        self.assertTrue(result.ok)
        self.assertEqual([entity.name for entity in result.entities], ["경복궁"])

    def test_개체는_있고_타입이_없으면_버린다(self) -> None:
        # 저장 유니크 키가 (entity_type, entity_uid) 다 — 타입 없는 개체는 저장할 자리가 없다.
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": "제주도", "type": None}})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.entities, ())

    def test_개체_표기가_이상하면_버린다(self) -> None:
        for bad in ("", "   ", None, 3, ["제주도"], {"name": "제주도"}):
            with self.subTest(bad=bad):
                result = interpret_response(
                    ["가키워드"], _response({"가키워드": {"entity": bad, "type": "장소"}})
                )
                self.assertTrue(result.ok)
                self.assertEqual(result.entities, ())

    def test_키워드_판정이_dict가_아니면_버린다(self) -> None:
        for bad in ("제주도", ["제주도"], 3, None):
            with self.subTest(bad=bad):
                result = interpret_response(["가키워드"], _response({"가키워드": bad}))
                self.assertTrue(result.ok)
                self.assertEqual(result.entities, ())

    def test_응답에_없는_키워드를_지어내지_않는다(self) -> None:
        # LLM 이 입력에 없던 키워드를 덧붙여도 그것으로 엣지를 만들지 않는다(입력이 계약이다).
        result = interpret_response(
            ["가키워드"],
            _response({
                "가키워드": {"entity": "제주도", "type": "장소"},
                "없던키워드": {"entity": "경복궁", "type": "장소"},
            }),
        )
        self.assertEqual([entity.keyword for entity in result.entities], ["가키워드"])


class TestInterpretFailure(unittest.TestCase):
    """🔴 망가진 응답은 전부 **실패** — "개체 0"으로 뭉개지 않는다(spec §2)."""

    def test_빈_dict는_실패다(self) -> None:
        # ``complete_json`` 이 빈 응답·파싱 실패를 접어 주는 모양이 바로 이것이다.
        result = interpret_response(["가키워드"], {})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)
        self.assertEqual(result.entities, ())

    def test_dict가_아니면_실패다(self) -> None:
        for bad in ([], "판정", None, 3, [{"판정": {}}]):
            with self.subTest(bad=bad):
                result = interpret_response(["가키워드"], bad)
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_판정_키가_없으면_실패다(self) -> None:
        result = interpret_response(["가키워드"], {"결과": {}})
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.JUDGEMENT_MISSING)

    def test_판정_값이_매핑이_아니면_실패다(self) -> None:
        for bad in ([], "제주도", 3, None):
            with self.subTest(bad=bad):
                result = interpret_response(["가키워드"], _response(bad))  # type: ignore[arg-type]
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.JUDGEMENT_NOT_MAPPING)

    def test_판정이_빈_매핑이면_실패다(self) -> None:
        # 프롬프트는 "각 키워드에 대해" 답하라고 지시한다 — 빈 판정은 지시를 못 따른 응답이며,
        # 개체 0(=키워드마다 null)과 구분해야 한다.
        result = interpret_response(["가키워드"], _response({}))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.JUDGEMENT_EMPTY)

    def test_입력_키워드와_하나도_겹치지_않으면_실패다(self) -> None:
        # 다른 대상을 말하는 응답이다 — "개체 0"으로 굳히면 그 자산은 영구히 재판정되지 않는다.
        result = interpret_response(
            ["가키워드"], _response({"없던키워드": {"entity": "제주도", "type": "장소"}})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.KEYWORDS_UNMATCHED)

    def test_키워드가_없으면_실패다(self) -> None:
        # 판정할 재료가 없으면 "판정한 것"이 아니다 — 이력을 남기지 않아야 키워드가 생기는 날
        # 자연히 재대상이 된다(배치 대상 선별은 키워드 보유 자산만 고르므로 실전 경로는 아니다).
        for keywords in ([], None, ["  "], [None]):
            with self.subTest(keywords=keywords):
                result = interpret_response(keywords, _response({}))
                self.assertFalse(result.ok)
                self.assertEqual(result.failure, JudgeFailure.NO_KEYWORDS)

    def test_실패에는_원인_발췌가_남는다(self) -> None:
        self.assertIn("결과", interpret_response(["가키워드"], {"결과": {}}).detail)

    def test_실패는_판정을_비워_돌려준다(self) -> None:
        # 호출부가 실수로 ``entities`` 만 보고 저장해도 엣지가 생기지 않는다(이중 안전장치).
        for response in ({}, {"결과": {}}, _response({})):
            with self.subTest(response=response):
                self.assertEqual(interpret_response(["가키워드"], response).entities, ())


class TestSummaryLimitInjection(unittest.TestCase):
    """G3 방어 ① — 요약 상한은 **주입 파라미터**여야 한다(설정 ``MM_META_JUDGE_SUMMARY_CHARS``).

    왜 필요한가: 설정 키는 이미 있는데 소비처가 없었다. 그러면 운영에서 값을 180 이나 400 으로
    바꿔도 **조용히 아무 일도 일어나지 않는다** — "설정을 바꿨는데 왜 그대로인가"를 다시 조사하게
    되는 종류의 결함이다(설정과 코드가 말이 갈리는 것). 배치가 설정값을 여기로 흘려 넣을 수 있어야
    비로소 그 키가 사실이 된다.
    """

    def test_기본값은_문안_상한과_같다(self) -> None:
        # 주입하지 않으면 기존 동작 그대로(합격선 기준선 보존).
        prompt = build_entity_prompt("가" * 400, ["가키워드"])
        self.assertIn("가" * SUMMARY_MAX_CHARS, prompt)
        self.assertNotIn("가" * (SUMMARY_MAX_CHARS + 1), prompt)

    def test_주입한_상한이_문안에_반영된다(self) -> None:
        prompt = build_entity_prompt("가" * 400, ["가키워드"], summary_max_chars=100)
        self.assertIn("가" * 100, prompt)
        self.assertNotIn("가" * 101, prompt)

    def test_판정_경로로도_상한이_흘러간다(self) -> None:
        client = _client_returning('{"판정": {}}')
        judge_asset_entities("가" * 400, ["가키워드"], client=client, summary_max_chars=80)
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("가" * 80, sent)
        self.assertNotIn("가" * 81, sent)

    def test_설정_기본값을_그대로_주입할_수_있다(self) -> None:
        # 설정 기본(250)을 주입하면 미주입과 **같은 문안**이어야 한다(드리프트 0).
        self.assertEqual(build_entity_prompt("가" * 400, ["가키워드"], summary_max_chars=250),
                         build_entity_prompt("가" * 400, ["가키워드"]))

    def test_0이하는_거부한다(self) -> None:
        # 0 이면 요약 없는 프롬프트가 조용히 나가고 판정 품질이 무성하게 떨어진다 → fail-fast.
        for bad in (0, -1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    build_entity_prompt("더미 요약", ["가키워드"], summary_max_chars=bad)
        with self.assertRaises(ValueError):
            judge_asset_entities("더미 요약", ["가키워드"],
                                 client=_client_returning('{"판정": {}}'), summary_max_chars=0)


class TestPromptTypeDefinitions(unittest.TestCase):
    """F05 — 타입 **정의문**을 프롬프트에 싣는 통로(spec §10 · 2026-08-25 파일럿 확정).

    왜 필요한가: 타입 5종이 이름뿐이라 경계를 LLM 상식에 맡겼고, 같은 개체가 자산마다 다른 타입으로
    갈렸다(실측 7건 — 조선=사건/조직 · 국립중앙박물관=장소/조직 …). 정의문을 붙이자 흔들림 6종이
    전부 통일됐다(18자산 재판정). 그 정의문이 **등록 행에서 와서 문안에 실리는 길**을 여기서 못 박는다.

    🔴 **미지정이면 현행 문안 그대로**여야 한다(하위호환) — 파이프 배치 워커가 지금 이 함수를
    ``type_defs`` 없이 부르고 있으므로, 기본값이 문안을 바꾸면 그쪽 판정이 예고 없이 달라진다.
    """

    def _defs(self) -> tuple[EntityTypeDef, ...]:
        """테스트용 정의문 2종(값 자체는 프리셋과 무관하게 조립만 본다).

        Returns:
            정의문 튜플.
        """
        return (
            EntityTypeDef(code="person", name="인물", definition="가정의", exclusion="가경계"),
            EntityTypeDef(code="place", name="장소", definition="나정의", exclusion="나경계"),
        )

    def test_미지정이면_현행_문안_그대로다(self) -> None:
        # 하위호환의 핵심 — 기본 호출은 정의문 블록이 없는 옛 문안이다.
        prompt = build_entity_prompt("더미 요약", ["가키워드"])
        self.assertNotIn("타입 정의", prompt)
        self.assertNotIn("아닌 것", prompt)

    def test_빈_목록도_미지정과_같은_문안이다(self) -> None:
        # 등록 행이 없어 폴백조차 비었을 때 "정의 없는 정의문 블록"이 나가면 안 된다.
        self.assertEqual(
            build_entity_prompt("더미 요약", ["가키워드"], type_defs=()),
            build_entity_prompt("더미 요약", ["가키워드"]),
        )

    def test_정의문을_주면_이름_정의_경계가_한_줄로_실린다(self) -> None:
        prompt = build_entity_prompt("더미 요약", ["가키워드"], type_defs=self._defs())
        self.assertIn("타입 정의(이 뜻으로만 판정한다):", prompt)
        self.assertIn('- "인물": 가정의. 아닌 것: 가경계', prompt)
        self.assertIn('- "장소": 나정의. 아닌 것: 나경계', prompt)

    def test_정의문_블록은_타입_목록_줄_뒤에_온다(self) -> None:
        # 순서에 뜻이 있다 — 먼저 어휘를 보여 주고 그 뜻을 이어 붙인다(파일럿 문안 구조).
        prompt = build_entity_prompt("더미 요약", ["가키워드"], type_defs=self._defs())
        self.assertLess(prompt.index('- "type"'), prompt.index("타입 정의"))

    def test_출력_계약_줄은_두_갈래_모두_맨_끝이다(self) -> None:
        # 출력 계약이 중간에 끼면 LLM 이 그 뒤 문장을 계약의 일부로 읽는다.
        for defs in (None, self._defs()):
            with self.subTest(defs=bool(defs)):
                prompt = build_entity_prompt("더미 요약", ["가키워드"], type_defs=defs)
                self.assertTrue(prompt.splitlines()[-1].startswith("JSON 하나로만:"))

    def test_정의문_순서가_그대로_문안_순서다(self) -> None:
        # 등록 행이 정본이므로 나열 순서도 행의 순서다(같은 행이면 같은 문안 · 결정성).
        reversed_defs = tuple(reversed(self._defs()))
        prompt = build_entity_prompt("더미 요약", ["가키워드"], type_defs=reversed_defs)
        self.assertLess(prompt.index('"장소"'), prompt.index('"인물"'))

    def test_같은_정의문이면_같은_문안이다(self) -> None:
        first = build_entity_prompt("더미 요약", ["가키워드"], type_defs=self._defs())
        second = build_entity_prompt("더미 요약", ["가키워드"], type_defs=self._defs())
        self.assertEqual(first, second)

    def test_판정_경로로도_정의문이_흘러간다(self) -> None:
        client = _client_returning('{"판정": {}}')
        judge_asset_entities("더미 요약", ["가키워드"], client=client, type_defs=self._defs())
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn('- "인물": 가정의. 아닌 것: 가경계', sent)

    def test_판정_경로도_미지정이면_옛_문안이다(self) -> None:
        client = _client_returning('{"판정": {}}')
        judge_asset_entities("더미 요약", ["가키워드"], client=client)
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("타입 정의", sent)

    def test_확정_프리셋을_실으면_왕조명_지시가_문안에_있다(self) -> None:
        # 🔴 표기 부작용 방지 문구가 실제로 LLM 까지 간다는 확인(값 봉인은 rules 테스트).
        prompt = build_entity_prompt("더미 요약", ["가키워드"], type_defs=ENTITY_TYPE_DEFS)
        self.assertIn("왕조명 그대로", prompt)
        self.assertIn("국립중앙박물관", prompt)


class TestPromptVersionBump(unittest.TestCase):
    """🔴 문안이 바뀌면 판정이 낡는다 — ``PROMPT_VERSION`` 상승이 **재판정 방아쇠**다(spec 구현확정 3).

    배치는 ``reason`` 스탬프의 ``pv=`` 를 현행 값과 **동일성 비교**해 재판정 대상을 고른다. 정의문을
    실을 수 있게 된 지금 문안 판을 올리지 않으면, 옛 문안으로 만든 판정이 "최신"으로 남아 영영
    다시 판정되지 않는다.
    """

    def test_현행_문안_판은_v2다(self) -> None:
        self.assertEqual(PROMPT_VERSION, "mm_meta.v2")

    def test_옛_판_상수가_남아있다(self) -> None:
        # 정의문 없이 나간 문안의 판 — 하위호환 경로가 자기 판을 정직하게 찍을 수 있어야 한다.
        self.assertEqual(PROMPT_VERSION_WITHOUT_TYPE_DEFS, "mm_meta.v1")

    def test_정의문_유무로_찍을_판이_갈린다(self) -> None:
        self.assertEqual(prompt_version_for(ENTITY_TYPE_DEFS), PROMPT_VERSION)
        self.assertEqual(prompt_version_for(None), PROMPT_VERSION_WITHOUT_TYPE_DEFS)
        self.assertEqual(prompt_version_for(()), PROMPT_VERSION_WITHOUT_TYPE_DEFS)


class TestJudgeThroughSeam(unittest.TestCase):
    """``judge_asset_entities`` — LLM 단일 seam 경유(헌법 2조)·모의 client 로 네트워크 0."""

    def test_모의_client_성공(self) -> None:
        payload = json.dumps(
            {JUDGEMENT_KEY: {"가키워드": {"entity": "제주도", "type": "장소"}}},
            ensure_ascii=False,
        )
        result = judge_asset_entities("더미 요약", ["가키워드"], client=_client_returning(payload))
        self.assertTrue(result.ok)
        self.assertEqual(result.entities[0].name, "제주도")

    def test_temperature가_0이다(self) -> None:
        # 결정 재현성(헌법 3조·2조) — 판정 경로에서 seam 기본값 0 을 확인한다.
        client = _client_returning(json.dumps({JUDGEMENT_KEY: {}}, ensure_ascii=False))
        judge_asset_entities("더미 요약", ["가키워드"], client=client)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0)

    def test_프롬프트에_요약과_키워드가_실려_나간다(self) -> None:
        client = _client_returning('{"판정": {}}')
        judge_asset_entities("가나다 더미 요약", ["가키워드"], client=client)
        sent = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("가나다 더미 요약", sent)
        self.assertIn("가키워드", sent)

    def test_깨진_JSON은_실패로_반환한다(self) -> None:
        # 예외를 던지지 않는다 — 자산 하나 때문에 배치가 죽으면 안 된다(실패 격리).
        result = judge_asset_entities("더미 요약", ["가키워드"], client=_client_returning("JSON 아님"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_빈_응답도_실패다(self) -> None:
        result = judge_asset_entities("더미 요약", ["가키워드"], client=_client_returning(""))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.RESPONSE_SHAPE)

    def test_키워드가_없으면_LLM을_부르지_않는다(self) -> None:
        # 재료가 없는 호출은 비용만 든다 — 그리고 결과는 실패(이력 미기록)여야 한다.
        client = _client_returning('{"판정": {}}')
        result = judge_asset_entities("더미 요약", [], client=client)
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, JudgeFailure.NO_KEYWORDS)
        client.chat.completions.create.assert_not_called()

    def test_요약이_없어도_판정을_시도한다(self) -> None:
        payload = json.dumps(
            {JUDGEMENT_KEY: {"가키워드": {"entity": None, "type": None}}}, ensure_ascii=False
        )
        result = judge_asset_entities(None, ["가키워드"], client=_client_returning(payload))
        self.assertTrue(result.ok)
        self.assertTrue(result.is_empty)

    def test_같은_응답이면_같은_판정이다(self) -> None:
        payload = json.dumps(
            {JUDGEMENT_KEY: {"가키워드": {"entity": "제주도", "type": "장소"}}}, ensure_ascii=False
        )
        first = judge_asset_entities("더미 요약", ["가키워드"], client=_client_returning(payload))
        second = judge_asset_entities("더미 요약", ["가키워드"], client=_client_returning(payload))
        self.assertEqual(first, second)


class TestJudgementShape(unittest.TestCase):
    """반환 객체의 모양 — 성공/실패가 **한 필드로** 갈린다."""

    def test_불변객체다(self) -> None:
        result = interpret_response(
            ["가키워드"], _response({"가키워드": {"entity": "제주도", "type": "장소"}})
        )
        self.assertIsInstance(result, EntityJudgement)
        with self.assertRaises(FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_성공이면_failure가_None이고_실패면_값이_있다(self) -> None:
        self.assertIsNone(interpret_response(["가키워드"], _response({"가키워드": {}})).failure)
        self.assertIsNotNone(interpret_response(["가키워드"], {}).failure)

    def test_실패는_개체_0과_구분된다(self) -> None:
        # 🔴 이 테스트가 spec §2 의 핵심을 지킨다 — 둘의 ``ok`` 가 달라야 한다.
        empty = interpret_response(["가키워드"], _response({"가키워드": {"entity": None}}))
        failed = interpret_response(["가키워드"], {})
        self.assertTrue(empty.ok)
        self.assertTrue(empty.is_empty)
        self.assertFalse(failed.ok)
        self.assertFalse(failed.is_empty)  # 실패는 "개체 0" 이 아니다

    def test_실패_사유_어휘가_닫혀있다(self) -> None:
        # 배치 diff 리포트가 실패를 사유별로 셀 수 있어야 한다(spec §6).
        self.assertEqual(
            {member.value for member in JudgeFailure},
            {
                "no_keywords",
                "response_shape",
                "judgement_missing",
                "judgement_not_mapping",
                "judgement_empty",
                "keywords_unmatched",
            },
        )


if __name__ == "__main__":
    unittest.main()
