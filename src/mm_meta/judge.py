"""084 멀티모달 메타 — **개체 판정**(LLM 단일 seam 경유) + 프롬프트 조립·응답 해석(순수).

무엇을 하는 모듈인가: 자산의 **요약 앞 250자 + 키워드 목록**을 온프레미스 LLM 에 보내 "이 키워드가
어떤 고유 개체를 가리키는가"를 받고, 그 응답이 계약을 지켰는지 **코드가 검사**해 판정을 돌려준다.
LLM 은 개체와 타입을 고르기만 하고, 목록·패턴 같은 결정 규칙은 ``rules`` 가 따로 집행한다.

왜 요약을 함께 보내나(사전 검증 §1 실측): 키워드만 일괄 판정하면 ①빈도 문턱이 꼬리 키워드를 버려
묶음이 실제보다 작아지고 ②동음이의('파리' 곤충/도시)를 원리적으로 가를 수 없다. 자산별로 요약
문맥을 함께 주면 둘 다 해소된다(수치는 검증 보고서).

🔴 이 모듈의 핵심 계약 — **"판정 실패"와 "개체 0"을 절대 섞지 않는다**(spec §2).

    공통 seam ``complete_json`` 은 빈 응답·JSON 파싱 실패를 **빈 dict ``{}``** 로 접어 돌려준다
    (호출부가 ``.get()`` 으로 안전하게 접근하도록 모양을 통일한 것). 그 편의를 "개체를 못 찾음"으로
    읽으면 **LLM 이 잠깐 죽은 순간이 "판정 완료·개체 0"으로 영구히 굳는다** — 성공은 판정 이력
    (lineage ``entity.judged.v1``)을 남기고, 이력이 있는 자산은 다음 배치가 다시 집지 않기 때문이다
    (spec §6 대상 선별). 그래서 성공/실패를 ``EntityJudgement.ok`` 로 **명시 구분**하고, 실패는
    판정을 비워 준다(호출부가 실수로 저장해도 엣지가 생기지 않는 이중 안전장치).

    비유하면 설문 회수다 — "응답이 안 옴"(다시 보내야 함)과 "해당 없다고 답함"(집계 대상)은 다른
    사건이다. 둘을 같은 칸에 적으면 무엇을 다시 물어야 할지 알 수 없게 된다.

**키워드 단위는 관용한다**(spec §2): 응답에서 키워드가 빠졌거나 타입이 어휘 밖이면 **그 키워드만**
판정 없음으로 흡수하고 나머지는 살린다. 085 분류가 어휘 밖 라벨 하나로 전체를 실패시키는 것과
반대인데, 이유가 있다 — 분류는 "이 자산의 라벨"이라 절반짜리가 곧 오분류지만, 개체 추출은 더하기
축이라 빠지면 묶음이 작아질 뿐 검색은 무손실이다(ADR 08-20 맥락 3 · "묶인 것은 확실하다" 원칙).

**타입 정의문(F05 · 2026-08-25)**: ``type_defs=`` 를 주면 타입 어휘 줄 뒤에 "이 뜻으로만 판정한다"
블록이 붙는다 — 이름만 주면 경계를 LLM 상식이 정해 같은 개체가 자산마다 다른 타입으로 갈렸고
(조선=사건/조직 · 국립중앙박물관=장소/조직 등), 정의문을 붙이자 그 흔들림이 거의 전부 통일됐다
(수치는 spec §10). 정의문의 정본은 **등록 행**이며(``persist.fetch_meta_type_vocab``), 이 모듈은 받은
값을 문안으로 옮기기만 한다(순수 · DB 를 모른다). **미지정이면 기존 문안 그대로**다 — 하위호환.

문안의 기준선은 사전 검증 재현 스크립트(`fixtures/entity_bundle/full_asset_entity_judge.py`)이며,
A/B 통과분(검증 §6-③)의 규칙을 얹었다: ⓐ개체가 무대·소재지·소속·주체 **수식**이면 추출("제주
장마"→제주도) ⓑ상대 지시어('국내'·'해외'·'우리나라'·'전국')와 막연한 범주("한국 음식")는 null
ⓒ동음이의는 요약 문맥으로 판별. ⚠️ **지정·자격 규칙('정식 종목'·'세계문화유산' 류)은 넣지 않는다**
— LLM 이 그 경계에서 비일관해(골프 제거/역도 잔류·금메달리스트 과잉 제거) 결정적 스톱패턴
(``rules.STOP_PATTERNS``)으로 이관했다. 프롬프트로 되돌리면 그 흔들림이 함께 돌아온다.

LLM 호출은 ``src/llm/client.py`` **단일 seam**만 경유한다(헌법 2조 · temperature=0 · ``client=``
주입으로 네트워크 없이 테스트). ``complete_json`` 은 호출 시점에 import 한다 — 이 패키지를 불러오는
것만으로 무거운 의존성(openai)이 딸려오지 않게 하는 관례다(``src.mm_classify`` 와 같다).

설계 배경: `specs/084-entity-bundle` spec §2(판정 계약) · `docs/개체묶음_사전검증_20260820.md` §6-③
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.domain.text_norm import normalize_text_key
from src.mm_meta.rules import ENTITY_TYPE_ORDER, ENTITY_TYPES, EntityTypeDef, ExtractedEntity

# 프롬프트 문안의 지문. **문안을 고칠 때마다 올린다** — 이 값이 ``reason`` 스탬프의 ``pv=`` 로
# 영속돼 "어떤 문안이 만든 판정인가"를 되짚는 단서가 된다(헌법 3조 · spec §4).
# 값에 패키지명을 넣은 이유: 리포트·로그의 한 칸짜리 문자열로 나갔을 때도 무엇의 버전인지 읽히게
# 하려는 것이다(``mm_classify.v1``·``relations.proposed.v1`` 표기 관례와 같은 결).
# ⚠️ 규칙 판(``rules.RULE_VERSION``)은 정수인데 이쪽은 문자열이다 — 백필 대상 선별이 규칙 판은
#    "이력 < 현행"으로 **순서 비교**하고 문안 판은 **동일성 비교**만 하기 때문이다(spec §6).
#
# 🔴 **v1 → v2 (2026-08-25 · F05)**: 문안에 **타입 정의문**을 실을 수 있게 됐다(spec §10). 이 상수를
#    올리는 것이 곧 **재판정 방아쇠**다 — 배치는 스탬프의 ``pv=`` 가 현행 값과 다른 자산을 다시
#    판정 대상으로 고르므로(구현 확정 3), 올리지 않으면 옛 문안으로 만든 판정이 "최신"으로 남아
#    영영 갱신되지 않는다. 실제 재판정(전량·LLM)은 사람이 실행한다.
PROMPT_VERSION = "mm_meta.v2"

# 정의문 **없이** 나간 문안의 판(v1 · 하위호환 경로). 왜 남겨 두나: ``type_defs`` 를 주지 않은 호출은
# 문안이 v1 그대로인데 스탬프만 v2 로 찍히면 "정의문이 실린 판정"과 구분할 수 없게 된다. 배치가
# ``prompt_version_for`` 로 자기 판을 정직하게 고를 수 있도록 값을 남긴다(스탬프는 사후 조사의
# 유일한 단서다 — 거짓이면 재판정 범위를 잘못 잡는다).
PROMPT_VERSION_WITHOUT_TYPE_DEFS = "mm_meta.v1"

# 요약을 프롬프트에 싣는 길이 상한(spec §2 입력 계약)의 **기본값**. 사전 검증이 이 값으로 측정됐다 —
# 바꾸면 합격선 수치(묶음 120~140 등)의 기준선이 흔들리므로 문안 판을 함께 올린다.
# 운영에서 다른 값을 쓰려면 설정 ``MM_META_JUDGE_SUMMARY_CHARS`` 를 배치가
# ``build_entity_prompt(..., summary_max_chars=)``·``judge_asset_entities(..., summary_max_chars=)``
# 로 **주입**한다(라이브러리는 설정을 직접 읽지 않는다 — 주입 seam 관례). 주입이 없으면 이 값이다.
SUMMARY_MAX_CHARS = 250

# 응답 최상위 키. 검증 스크립트 문안이 한국어 키를 썼고 그 문안으로 실측치가 나왔으므로 그대로
# 유지한다(문안 기준선 보존 — 영문 키로 바꾸면 재검증이 필요하다).
JUDGEMENT_KEY = "판정"

# 실패 사유 발췌를 잘라 담는 길이 — 리포트·로그에 실리는 값이라 응답 전문을 안고 다니지 않는다
# (요약·프롬프트 원문이 로그로 새는 것도 막는다).
_DETAIL_MAX_CHARS = 200


class JudgeFailure(StrEnum):
    """판정 실패 사유 — **닫힌 어휘**(배치 diff 리포트가 사유별로 셀 수 있게).

    전부 "판정 이력을 남기지 않고 다음 배치의 재대상으로 둔다"는 같은 처분을 받지만, 사유를 나눠
    두면 원인을 갈라 볼 수 있다 — ``response_shape`` 가 몰리면 LLM 서버 쪽 문제이고,
    ``keywords_unmatched`` 가 몰리면 문안 문제다.
    """

    NO_KEYWORDS = "no_keywords"  # 판정할 재료(키워드)가 없음 — LLM 을 부르지 않는다.
    RESPONSE_SHAPE = "response_shape"  # 비-dict·빈 dict(= seam 의 빈응답·파싱실패 폴백).
    JUDGEMENT_MISSING = "judgement_missing"  # 최상위에 ``판정`` 키가 없음.
    JUDGEMENT_NOT_MAPPING = "judgement_not_mapping"  # ``판정`` 값이 dict 가 아님.
    JUDGEMENT_EMPTY = "judgement_empty"  # ``판정`` 이 빈 dict — 키워드마다 답하라는 지시 불이행.
    KEYWORDS_UNMATCHED = "keywords_unmatched"  # 입력 키워드와 겹치는 항목이 하나도 없음.


@dataclass(frozen=True, slots=True)
class EntityJudgement:
    """자산 하나의 개체 판정 결과(불변).

    ``ok`` 한 필드로 성공/실패가 갈린다:
        - ``ok=True`` — ``entities`` 는 0..N 건. **0 건도 성공**이다(이력은 남기고 엣지는 없다).
        - ``ok=False`` — ``failure`` 에 사유, ``entities`` 는 **빈 값**. 이력을 남기지 않는다(재대상).
    """

    ok: bool
    # 성공 시 판정 목록. **입력 키워드 순서**로 담는다 — 규칙(``apply_rules``)이 같은 개체를 접을 때
    # 첫 판정의 키워드를 대표로 남기므로, 순서가 흔들리면 ``reason`` 의 ``kw=`` 가 달라진다(결정성).
    entities: tuple[ExtractedEntity, ...] = ()
    failure: JudgeFailure | None = None
    # 실패 원인 발췌(사람이 읽는 한 줄). 성공이면 빈 문자열.
    detail: str = ""

    @property
    def is_empty(self) -> bool:
        """성공이면서 개체 0 인가(= "이 자산에서 개체를 찾지 못했다"고 판정된 것인가).

        실패는 여기서 ``False`` 다 — 실패는 "개체 0"이 아니라 "판정하지 못함"이다.
        """
        return self.ok and not self.entities


def _failed(failure: JudgeFailure, detail: str) -> EntityJudgement:
    """실패 판정을 만든다 — 판정 목록은 항상 비운다.

    Args:
        failure: 실패 사유(닫힌 어휘).
        detail: 사람이 읽는 원인 발췌. 길면 잘라 담는다.

    Returns:
        ``ok=False`` 인 ``EntityJudgement``.
    """
    return EntityJudgement(ok=False, failure=failure, detail=detail[:_DETAIL_MAX_CHARS])


def _clean_keywords(keywords: Sequence[Any] | None) -> list[str]:
    """키워드 목록을 판정에 쓸 문자열 목록으로 다듬는다(순서 보존·중복 제거).

    프롬프트 조립과 응답 해석이 **같은 목록**을 봐야 한다 — 한쪽만 다듬으면 "보낸 키워드"와
    "찾는 키워드"가 어긋나 애먼 실패가 난다. 그래서 두 곳이 이 함수를 공유한다.

    Args:
        keywords: 자산 키워드 목록. 문자열이 아닌 값(숫자 등)은 문자열로 바꾸고, ``None``·공백뿐인
            값은 버린다 — 요약기 산출물에 섞여 들어와도 ``"None"`` 같은 쓰레기 토큰이 프롬프트에
            새지 않게 한다. ``None``(목록 자체가 없음)이면 빈 목록.

    Returns:
        다듬어진 키워드 목록(입력 순서 보존 — 요약기가 준 순서가 중요도 순서라는 관례).
    """
    cleaned: list[str] = []
    for raw in keywords or []:
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _resolve_summary_limit(summary_max_chars: int | None) -> int:
    """요약 상한을 확정한다(미주입이면 문안 기본값 · 잘못된 값은 fail-fast).

    Args:
        summary_max_chars: 호출부가 준 상한. ``None`` 이면 ``SUMMARY_MAX_CHARS``(=문안 기준선).
            **1 미만은 예외** — 0 을 허용하면 요약 없는 프롬프트가 조용히 나가고 판정 품질이
            뚝 떨어지는데, 그 원인은 로그만 봐선 보이지 않는다.

    Returns:
        실제로 쓸 상한(1 이상 정수).

    Raises:
        ValueError: 1 미만이거나 정수로 읽을 수 없는 값일 때.
    """
    if summary_max_chars is None:
        return SUMMARY_MAX_CHARS
    limit = int(summary_max_chars)
    if limit < 1:
        raise ValueError(
            f"요약 상한은 1 이상이어야 한다: {summary_max_chars!r} "
            "(설정 MM_META_JUDGE_SUMMARY_CHARS 확인 — 0 은 요약 없는 판정이 된다)"
        )
    return limit


def prompt_version_for(type_defs: Sequence[EntityTypeDef] | None) -> str:
    """이 호출이 실제로 만든 문안의 **판**을 고른다(순수).

    스탬프(``reason`` 의 ``pv=``)는 사후 조사의 유일한 단서다 — "정의문을 실은 판정"과 "이름만 준
    판정"이 같은 값으로 찍히면, 정의문 효과를 확인할 수도 재판정 범위를 잡을 수도 없다. 배치는
    판정에 넘긴 것과 **같은 값**을 이 함수에 넣어 스탬프를 정한다.

    Args:
        type_defs: 프롬프트에 실은 타입 정의문. ``None``·빈 목록이면 정의문 없이 나간 것이다.

    Returns:
        ``PROMPT_VERSION``(정의문 실림) 또는 ``PROMPT_VERSION_WITHOUT_TYPE_DEFS``(안 실림).
    """
    return PROMPT_VERSION if tuple(type_defs or ()) else PROMPT_VERSION_WITHOUT_TYPE_DEFS


def _type_lines(type_defs: Sequence[EntityTypeDef] | None) -> list[str]:
    """타입 어휘 줄(+정의문 블록)을 만든다.

    정의문이 없으면 **옛 문안 그대로 한 줄**이다(하위호환). 있으면 그 아래에 타입마다 한 줄씩
    ``- "이름": 정의. 아닌 것: 경계`` 를 붙인다 — 2026-08-25 파일럿에서 흔들림 6종을 통일시킨
    구조 그대로다(spec §10).

    나열 순서는 **정의문이 있으면 그 순서**다: 등록 행이 정본이므로 행에 적힌 순서가 곧 제시
    순서이고, 어휘 줄과 정의 블록이 같은 순서로 나란히 놓여야 사람이 대조하기 쉽다. 정의문이 없으면
    코드 상수 순서(``ENTITY_TYPE_ORDER``)를 쓴다(기존 문안 보존).

    Args:
        type_defs: 실을 타입 정의문 목록. ``None``·빈 목록이면 정의 블록을 만들지 않는다.

    Returns:
        프롬프트에 그대로 넣을 줄 목록(첫 줄이 타입 어휘 줄).
    """
    defs = tuple(type_defs or ())
    names = tuple(d.name for d in defs) if defs else ENTITY_TYPE_ORDER
    types = "|".join(f'"{name}"' for name in names)
    lines = [f'- "type": entity 가 있으면 {types} 중 하나, 없으면 null.']
    if defs:
        # "이 뜻으로만" 이라고 못 박는 이유: 정의문을 참고 사항으로 읽으면 LLM 이 자기 상식을
        # 우선해 경계 개체(조선·시청 청사)에서 다시 갈린다(파일럿 v1 실측).
        lines.append("타입 정의(이 뜻으로만 판정한다):")
        lines += [f'- "{d.name}": {d.definition}. 아닌 것: {d.exclusion}' for d in defs]
    return lines


def build_entity_prompt(
    summary: str | None,
    keywords: Sequence[Any] | None,
    *,
    summary_max_chars: int | None = None,
    type_defs: Sequence[EntityTypeDef] | None = None,
) -> str:
    """자산 하나의 개체 판정 프롬프트를 조립한다(순수 · 같은 입력 → 같은 문안).

    Args:
        summary: 자산 요약. 앞 ``summary_max_chars`` 자만 싣는다. ``None``·빈 값도 받는다 —
            요약이 없는 자산(빈 STT 등)이 실제로 있고, 그때 ``"None"`` 이 글자로 새면 안 된다.
        keywords: 자산 키워드 목록(``_clean_keywords`` 규칙으로 다듬어 싣는다).
        summary_max_chars: 요약 상한(글자). ``None``(기본)이면 ``SUMMARY_MAX_CHARS``(250) —
            **미주입이 곧 기존 동작**이다. 배치는 설정 ``MM_META_JUDGE_SUMMARY_CHARS`` 를 여기로
            흘려 넣는다(그 키가 사실이 되는 유일한 통로다). ⚠️ 기본값을 벗어난 상한으로 만든
            판정은 사전 검증의 기준선(합격선 수치)과 비교할 수 없다 — 바꾸려면 ``PROMPT_VERSION``
            도 함께 올려 "다른 문안"임을 스탬프에 남긴다.
        type_defs: 타입 **정의문** 목록(``rules.EntityTypeDef``). ``None``(기본)이면 **현행 문안
            그대로** — 타입 이름만 나열한다(하위호환 · 지금 이 함수를 부르는 배치가 인자를 주지
            않으므로 기본값이 문안을 바꾸면 그쪽 판정이 예고 없이 달라진다). 값을 주면 타입 어휘 줄
            뒤에 "이 뜻으로만 판정한다" 블록이 붙는다. 정본은 등록 행(``mm_skill``·
            ``skill_code='mm_meta_type'``)이며 ``persist.fetch_meta_type_vocab`` 이 읽어 준다
            (행이 없으면 코드 프리셋 ``rules.ENTITY_TYPE_DEFS`` 로 폴백).

    Returns:
        LLM 에 그대로 넘길 단일 프롬프트 문자열.

    Raises:
        ValueError: ``summary_max_chars`` 가 1 미만일 때.
    """
    # 요약은 앞뒤 공백을 정리한 뒤 자른다(공백만으로 250자 창이 밀리지 않게).
    summary_text = (summary or "").strip()[:_resolve_summary_limit(summary_max_chars)]
    keywords_json = json.dumps(_clean_keywords(keywords), ensure_ascii=False)
    type_names = tuple(d.name for d in (type_defs or ())) or ENTITY_TYPE_ORDER
    return "\n".join([
        f'자산 요약: "{summary_text}"',
        f"키워드 목록: {keywords_json}",
        "각 키워드에 대해 요약 문맥을 참고해 판정하라.",
        # ⓐ 지시 대상 규칙 — 무대·소재지·소속·주체 수식은 그 개체를 뽑는다(검증 §6-③ 무손실 회귀).
        f'- "entity": 키워드가 특정 고유 개체({"/".join(type_names)})를 가리키거나,'
        " 그 개체가 무대·소재지·소속·주체로 **지시 대상**이 될 때만 그 개체의 가장 널리 쓰이는"
        " 표준 표기 하나. 아니면 null.",
        '  예: "제주 장마"→"제주도", "경복궁 야간개장"→"경복궁", "검은색 배경"→null, "요리"→null.',
        # ⓑ 상대 지시어·막연한 범주 — 실측에서 잡동사니 묶음의 주된 원료였다.
        '  상대 지시어("국내"·"해외"·"우리나라"·"전국")와 막연한 범주("한국 음식"·"전통 문화")는'
        " 개체가 아니다 → null.",
        # ⓒ 동음이의 — 자산별 판정(문맥 포함)의 존재 이유.
        '  동음이의어는 요약 문맥으로 가른다(요약이 곤충 이야기인데 "파리"를 도시로 판정하지 말 것).',
        # 타입 어휘 줄 + (정의문을 줬으면) "이 뜻으로만 판정한다" 블록.
        *_type_lines(type_defs),
        # 출력 계약은 검증 스크립트 문안 그대로다(말줄임도 ASCII 세 점 — 기준선 보존).
        # 🔴 **항상 마지막 줄**이다 — 뒤에 문장이 붙으면 LLM 이 그것을 계약의 일부로 읽는다.
        f'JSON 하나로만: {{"{JUDGEMENT_KEY}": {{"키워드": {{"entity":..., "type":...}}, ...}}}}',
    ])


def _entity_from_entry(keyword: str, entry: Any) -> ExtractedEntity | None:
    """키워드 하나의 판정 항목을 ``ExtractedEntity`` 로 바꾼다(못 바꾸면 ``None``).

    "못 바꾸면 판정 없음"은 **의도된 관용**이다(spec §2) — 개체 추출은 더하기 축이라 한 키워드가
    빠지는 것이 자산 전체를 버리는 것보다 낫다. 반대로 타입이 어휘 밖인 값을 임의 보정하지는
    않는다(저장 유니크 키가 ``(entity_type, entity_uid)`` 라 추측이 데이터로 굳는다).

    Args:
        keyword: 원문 키워드(판정의 출처 — 그대로 보존해 ``reason`` 의 ``kw=`` 가 된다).
        entry: 응답의 키워드별 판정 값. **무엇이든 올 수 있다고 가정**한다.

    Returns:
        판정 한 건. 개체가 없다고 답했거나(정상) 모양·어휘가 어긋나면 ``None``.
    """
    if not isinstance(entry, Mapping):
        return None
    name = entry.get("entity")
    entity_type = entry.get("type")
    # 개체 없음(null)은 정상 응답이다 — 실패가 아니라 "이 키워드는 개체가 아니다".
    if not isinstance(name, str) or not name.strip():
        return None
    if entity_type not in ENTITY_TYPES:
        return None
    return ExtractedEntity(keyword=keyword, name=name.strip(), entity_type=entity_type)


def interpret_response(keywords: Sequence[Any] | None, response: Any) -> EntityJudgement:
    """LLM 응답을 판정 결과로 해석한다(순수 — LLM·DB 호출 없음).

    검사 순서에 뜻이 있다: **재료 → 모양 → 대응**. 재료(키워드)가 없으면 응답을 볼 이유가 없고,
    모양이 깨진 응답에서 키워드 대응을 따질 수 없다(먼저 걸린 사유를 돌려준다).

    키워드 대조는 원문 일치를 먼저 보고, 없으면 **표기 키**(공백·전각·대소문자 흡수)로 맞춘다 —
    LLM 이 키워드를 되풀어 쓸 때 공백이 흔들리는 일이 있다. 돌려주는 ``keyword`` 는 항상 **입력
    원문**이다(``reason`` 의 근거는 저장된 키워드와 글자까지 같아야 한다).

    Args:
        keywords: 판정에 넣은 키워드 목록(프롬프트에 실은 것과 **같은 값**이어야 한다).
        response: ``complete_json`` 이 돌려준 응답. 계약은
            ``{"판정": {키워드: {"entity":…, "type":…}}}`` 이지만 그것을 지켰는지가 이 함수의 판정
            대상이므로 **무엇이든 올 수 있다고 가정**한다.

    Returns:
        ``EntityJudgement`` — 성공이면 판정 목록(0건 포함), 실패면 사유와 발췌.
    """
    cleaned = _clean_keywords(keywords)
    if not cleaned:
        # 재료가 없으면 "판정한 것"이 아니다 — 이력을 남기지 않아야 키워드가 생기는 날 재대상이 된다.
        return _failed(JudgeFailure.NO_KEYWORDS, f"키워드 없음: {keywords!r}")

    # ① 모양 — 비-dict 와 **빈 dict** 를 함께 막는다. 빈 dict 는 seam 이 빈 응답·파싱 실패를 접어
    #    준 모양이라(``complete_json`` docstring) 여기서 성공으로 새면 장애가 판정으로 굳는다.
    if not isinstance(response, Mapping) or not response:
        return _failed(JudgeFailure.RESPONSE_SHAPE, f"{type(response).__name__}: {response!r}")
    if JUDGEMENT_KEY not in response:
        return _failed(JudgeFailure.JUDGEMENT_MISSING, f"응답 키: {sorted(map(str, response))}")
    judged = response[JUDGEMENT_KEY]
    if not isinstance(judged, Mapping):
        return _failed(
            JudgeFailure.JUDGEMENT_NOT_MAPPING, f"판정 형: {type(judged).__name__} = {judged!r}"
        )
    if not judged:
        return _failed(JudgeFailure.JUDGEMENT_EMPTY, "판정이 빈 dict — 키워드마다 답해야 한다")

    # ② 대응 — 응답 키를 표기 키로도 찾을 수 있게 색인해 둔다(첫 등장 우선 · 결정적).
    by_key: dict[str, Any] = {}
    for raw_key, value in judged.items():
        by_key.setdefault(normalize_text_key(str(raw_key)), value)

    entities: list[ExtractedEntity] = []
    matched = 0
    for keyword in cleaned:  # 입력 키워드 순서 = 결과 순서(결정성)
        if keyword in judged:
            entry = judged[keyword]
        else:
            key = normalize_text_key(keyword)
            if key not in by_key:
                continue  # 응답에서 빠진 키워드는 그 키워드만 판정 없음(spec §2)
            entry = by_key[key]
        matched += 1
        entity = _entity_from_entry(keyword, entry)
        if entity is not None:
            entities.append(entity)

    if matched == 0:
        # 응답이 다른 대상을 말하고 있다 — "개체 0"으로 굳히면 그 자산은 영구히 재판정되지 않는다.
        return _failed(
            JudgeFailure.KEYWORDS_UNMATCHED, f"응답 키워드: {sorted(map(str, judged))[:10]}"
        )
    return EntityJudgement(ok=True, entities=tuple(entities))


def judge_asset_entities(
    summary: str | None,
    keywords: Sequence[Any] | None,
    *,
    client: Any | None = None,
    summary_max_chars: int | None = None,
    type_defs: Sequence[EntityTypeDef] | None = None,
) -> EntityJudgement:
    """자산 하나의 개체를 판정한다(LLM 단일 seam 경유 · 자산당 호출 1회).

    **결정 규칙은 여기서 적용하지 않는다** — 광역 제외·스톱패턴·접미 병합은 ``rules.apply_rules``
    가 판정 뒤에 따로 돌린다(층 분리). 그래야 "LLM 이 무엇을 말했나"와 "규칙이 무엇을 떨궜나"를
    배치 리포트에서 갈라 볼 수 있고, 규칙 판(``rv``)과 문안 판(``pv``)을 따로 스탬프할 수 있다.

    응답 내용에 대해서는 예외를 올리지 않는다(깨진 JSON·빈 응답·어휘 밖 값 → ``ok=False`` 또는
    해당 키워드 판정 없음). 다만 **전송 계층 예외**(연결 실패 등)는 그대로 오른다 — 자산 단위
    실패 격리는 배치의 책임이다(``src.mm_classify.judge`` 와 같은 경계).

    Args:
        summary: 자산 요약(앞 ``summary_max_chars`` 자만 싣는다). ``None`` 도 받는다.
        keywords: 자산 키워드 목록. **비어 있으면 LLM 을 부르지 않고** ``NO_KEYWORDS`` 실패를
            돌려준다(재료 없는 호출은 비용만 들고, 이력을 남기면 키워드가 생겨도 재판정되지 않는다).
        client: **테스트용 LLM 클라이언트 주입 seam** — 미주입이면 설정의 운영 온프레미스
            클라이언트를 쓴다(``src.llm.client.get_llm_client``). temperature 는 seam 기본값 0 이다
            (헌법 3조 결정 재현성 — 여기서 올리지 않는다).
        summary_max_chars: 요약 상한(글자). ``None``(기본)이면 문안 기본값 250 = 기존 동작.
            배치가 설정 ``MM_META_JUDGE_SUMMARY_CHARS`` 를 넘기는 자리다(라이브러리가 설정을 직접
            읽지 않는 관례 — 주입은 호출부 책임). 1 미만이면 ``ValueError``.
        type_defs: 타입 정의문 목록. ``None``(기본)이면 정의문 없이 **기존 문안 그대로** 나간다
            (하위호환). 배치는 ``persist.fetch_meta_type_vocab(conn)`` 로 등록 행을 읽어 여기로
            넘기고, 스탬프는 같은 값을 ``prompt_version_for`` 에 넣어 정한다 — 그래야 "정의문을 실은
            판정"과 아닌 것이 ``pv`` 로 갈린다(설정만 바꾸고 소비처가 없어 조용히 무동작이 되는
            ``MM_META_JUDGE_SUMMARY_CHARS`` 류 결함의 재발 방지).

    Returns:
        ``EntityJudgement``. 판정 이력을 남길지 말지는 이 값의 ``ok`` 로 결정된다(영속 계층 책임).

    Raises:
        ValueError: ``summary_max_chars`` 가 1 미만일 때(**LLM 을 부르기 전에** 터진다).
    """
    # 상한 검사를 **키워드 검사보다 먼저** 한다 — 설정이 잘못된 배치는 첫 자산에서 즉시 멈춰야
    # 한다(키워드가 없는 자산이 먼저 오면 잘못된 설정이 조용히 지나간다).
    limit = _resolve_summary_limit(summary_max_chars)
    cleaned = _clean_keywords(keywords)
    if not cleaned:
        return _failed(JudgeFailure.NO_KEYWORDS, f"키워드 없음: {keywords!r}")

    # 무거운 의존성(openai)을 패키지 import 시점에 끌어오지 않으려고 호출 시점에 import 한다
    # (``src.mm_classify.judge``·``src.relations.llm_propose`` 와 같은 관례).
    from src.llm.client import complete_json

    prompt = build_entity_prompt(summary, cleaned, summary_max_chars=limit, type_defs=type_defs)
    return interpret_response(cleaned, complete_json(prompt, client=client))


__all__ = [
    "JUDGEMENT_KEY",
    "PROMPT_VERSION",
    "PROMPT_VERSION_WITHOUT_TYPE_DEFS",
    "SUMMARY_MAX_CHARS",
    "EntityJudgement",
    "JudgeFailure",
    "build_entity_prompt",
    "interpret_response",
    "judge_asset_entities",
    "prompt_version_for",
]
