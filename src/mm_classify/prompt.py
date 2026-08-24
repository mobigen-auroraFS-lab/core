"""085 분류 스킬 — **프롬프트 조립**(순수) + ``PROMPT_VERSION``.

무엇을 하는 모듈인가: 검증된 스킬 설정(``model.ClassificationSkill``)과 자산 재료(요약·키워드)를
LLM 에 보낼 **문자열 하나**로 엮는다. 설정의 "프롬프트 반쪽"을 담당한다(집행 반쪽은 ``model``·
``judge`` — 085 plan §Architecture).

문안의 기준선은 **파일럿 스크립트**(`fixtures/scheme_pilot/pilot_scheme_085.py` 의 ``build_prompt``)다.
정확도 ~95~97%·결정성 100% 를 낸 그 구조 — ①분류표 소개 + 선택 지시 ②라벨마다 "이름: 정의문.
아닌 것: 경계." ③미부여 라벨 정의 ④자산 요약·키워드 ⑤JSON 출력 지시 — 를 유지하고, 하드코딩된
스킴 대신 **설정 객체에서 조립**하도록 일반화했다.

파일럿 문안에서 의도적으로 달라진 점 3가지(모두 spec §4 요구):
    1. 출력 계약이 ``{"label": "…"}``(단일) → ``{"labels": [ … ]}``(배열) 로 바뀌었다.
       single 정책도 원소 1개 배열로 답한다 — 저장·검증 경로를 하나로 유지하려는 선택이다.
    2. multi/single **분기**가 생겼다(파일럿은 single 고정). multi 는 "해당하는 라벨을 모두",
       single 은 "가장 맞는 라벨 하나".
    3. 미부여 정의의 괄호 설명을 스킴 특정 문구("음식과 무관하거나…")에서 **일반 문구**로 바꿨고,
       "라벨명을 목록 표기 그대로 쓰라"는 한 줄을 추가했다. 어휘 밖 응답은 judge 가 **판정 실패**로
       돌리므로(spec §4 — 조용한 필터링 금지) 실패를 프롬프트에서 먼저 줄이는 편이 싸다.

⚠️ **문안을 고치면 ``PROMPT_VERSION`` 을 올린다.** 이 상수는 문안의 지문이며 "어떤 문안이 만든
판정인가"를 되짚는 단서다 — 문안만 바꾸고 버전을 그대로 두면 과거·현재 판정이 뒤섞여 재현이
불가능해진다(헌법 3조). 백필 재선별의 기준으로 **영속되는 값은 스킬 버전**
(``asset_mm_skill_label.skill_version`` · spec §4)이며, 문안 버전을 어디에 남길지는 영속 계층
(T105)의 결정이다(v302 ``decided_by`` 는 ``VARCHAR(20)``·기본 ``'llm'`` 이라 담기엔 좁다).

여기 있는 것은 **순수 함수**다 — LLM·DB 를 부르지 않고, 같은 입력이면 언제나 같은 문자열이 나온다.

설계 배경: `specs/085-classification-skill` §4(판정 계약) · 파일럿 `docs/분류스킬_파일럿_20260824.md`
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from src.mm_classify.model import ClassificationSkill

# 프롬프트 문안의 지문. 문안을 고칠 때마다 올린다.
# 값에 패키지명을 넣은 이유: 리포트·로그·이력의 한 칸짜리 문자열로 나갔을 때도 무엇의 버전인지
# 읽히게 하려는 것이다(관계의 ``relations.proposed.v1`` 표기 관례와 같은 결).
PROMPT_VERSION = "mm_classify.v1"

# 자산 요약을 프롬프트에 싣는 길이 상한(spec §4 — "요약 앞 250자 + 키워드").
# 왜 자르나: 요약 뒷부분은 세부 나열이라 분류 판단에 기여가 적고, 길수록 프롬프트가 부풀어
# 지연·비용이 늘고 결정성이 나빠진다. 파일럿도 220자 절단으로 ~95~97% 정확도를 냈다.
SUMMARY_MAX_CHARS = 250


def _label_lines(skill: ClassificationSkill) -> list[str]:
    """라벨 나열 블록 — 라벨마다 "이름: 정의문. 아닌 것: 경계." 한 줄 + 미부여 한 줄.

    순서는 **설정 순서 그대로**다(정렬하지 않는다) — 등록한 사람이 적은 순서가 곧 제시 순서이고,
    같은 설정이면 같은 문안이 나와야 한다(결정성).

    Args:
        skill: 검증된 스킬. 라벨의 ``definition``·``exclusion`` 과 정책의 ``unassigned`` 를 읽는다.

    Returns:
        프롬프트에 그대로 넣을 줄 목록(마지막 줄이 미부여 라벨 정의).
    """
    lines = [
        f"- {label.name}: {label.definition}. 아닌 것: {label.exclusion}."
        for label in skill.labels
    ]
    # 미부여 선택지를 반드시 준다 — 없으면 무관한 자산에도 억지 배정이 일어난다(파일럿 발견 2:
    # 미부여가 정직하게 작동해 라벨 커버리지 갭이 드러났다). 괄호 설명은 스킴에 의존하지 않는
    # 일반 문구다(파일럿은 "음식과 무관하거나…"처럼 스킴 특정 문구였다).
    lines.append(
        f"- {skill.policy.unassigned}: 위 어디에도 해당하지 않음"
        "(이 분류표의 주제와 무관하거나, 그 주제가 스쳐 가는 소재일 뿐인 경우)."
    )
    return lines


def _selection_instruction(skill: ClassificationSkill) -> str:
    """첫 줄 — 무엇을 몇 개 고르라는 지시(multi/single 분기).

    Args:
        skill: 검증된 스킬. ``policy.selection`` 과 표시명(``name``)을 읽는다.

    Returns:
        지시 한 줄.
    """
    head = f"다음 분류표(스킬: {skill.name})에서 이 자산에"
    if skill.is_multi:
        # multi 기본(spec §1) — "유래+조리법" 자산이 두 라벨을 갖는 것이 정보 보존이다.
        return f"{head} 해당하는 라벨을 **모두** 골라라."
    return f"{head} 가장 맞는 라벨 **하나**를 골라라."


def _output_instruction(skill: ClassificationSkill) -> str:
    """마지막 줄 — JSON 출력 계약(정책별 개수 지시 포함).

    두 정책 모두 ``{"labels": [...]}`` **배열 하나**로 답하게 한다. single 을 배열로 받는 이유:
    저장·검증 경로를 한 모양으로 유지하려는 것이다(원소 1개 강제는 코드가 집행 — spec §4).

    Args:
        skill: 검증된 스킬. ``policy.selection``·``policy.unassigned`` 를 읽는다.

    Returns:
        출력 지시 한 줄.
    """
    unassigned = skill.policy.unassigned
    if skill.is_multi:
        return (
            'JSON 하나로만 답하라: {"labels": ["라벨명", "라벨명"]} — 해당하는 라벨을 빠짐없이 '
            f'넣고, 위 어디에도 해당하지 않으면 ["{unassigned}"] 만 넣는다.'
        )
    return (
        'JSON 하나로만 답하라: {"labels": ["라벨명"]} — 라벨명은 정확히 하나만 넣는다'
        f'(위 어디에도 해당하지 않으면 ["{unassigned}"]).'
    )


def _keywords_json(keywords: Sequence[Any] | None) -> str:
    """키워드 목록을 프롬프트에 실을 JSON 배열 문자열로 만든다.

    한국어가 ``\\uXXXX`` 로 이스케이프되면 LLM 이 읽을 재료가 나빠지므로 ``ensure_ascii=False``.
    **입력 순서를 보존**한다(정렬하지 않는다 — 요약기가 준 순서가 중요도 순서라는 관례).

    Args:
        keywords: 키워드 목록. 문자열이 아닌 값(숫자 등)은 문자열로 바꾸고, ``None``·공백뿐인
            값은 버린다 — 요약기 산출물에 섞여 들어와도 ``"None"`` 같은 쓰레기 토큰이 프롬프트에
            새지 않게 한다. ``None``(목록 자체가 없음)이면 빈 배열.

    Returns:
        ``["가키워드", "나키워드"]`` 형태의 JSON 문자열(없으면 ``[]``).
    """
    cleaned = [
        text
        for raw in (keywords or [])
        if raw is not None and (text := str(raw).strip())
    ]
    return json.dumps(cleaned, ensure_ascii=False)


def build_classification_prompt(
    skill: ClassificationSkill,
    *,
    summary: str | None,
    keywords: Sequence[Any] | None,
) -> str:
    """스킬 **하나**와 자산 재료로 판정 프롬프트 한 개를 조립한다(순수).

    ⚠️ 스킬을 여러 개 받지 않는다 — 스킬마다 별도 호출이 계약이다(격리·버전 분리 · 085 plan
    §Global Constraints). 한 프롬프트에 두 분류표를 섞으면 라벨이 서로 오염되고, 응답 하나에
    두 스킬의 판정이 엉켜 버전 스탬프를 나눠 붙일 수 없다.

    Args:
        skill: 검증을 통과한 스킬(``load_skill`` 결과). 라벨·정의문·경계·미부여 라벨명·선택 정책을
            여기서 읽는다.
        summary: 자산 요약. 앞 ``SUMMARY_MAX_CHARS`` 자만 싣는다. ``None``·빈 값도 받는다 —
            요약이 없는 자산(빈 STT 등)이 실제로 있고, 그때 ``"None"`` 이 글자로 새면 안 된다.
        keywords: 자산 키워드 목록. 순서 보존·비문자열은 문자열화·``None``/공백 값은 제외.
            ``None`` 이면 빈 배열로 표기한다(키가 없다는 사실도 판단 재료다).

    Returns:
        LLM 에 그대로 넘길 단일 프롬프트 문자열.
    """
    # 요약은 앞뒤 공백을 정리한 뒤 자른다(공백만으로 250자 창이 밀리지 않게).
    summary_text = (summary or "").strip()[:SUMMARY_MAX_CHARS]
    lines = [
        _selection_instruction(skill),
        *_label_lines(skill),
        # 어휘 고정 지시 — judge 는 어휘 밖 원소가 하나라도 있으면 **판정 실패**로 돌린다
        # (조용한 필터링 금지 · spec §4). 실패는 재대상 비용이라 지시로 먼저 줄인다.
        "라벨명은 위 목록에 적힌 표기를 **그대로** 쓴다(새 이름·설명·번역 금지).",
        f'자산 요약: "{summary_text}"',
        f"키워드: {_keywords_json(keywords)}",
        _output_instruction(skill),
    ]
    return "\n".join(lines)


__all__ = [
    "PROMPT_VERSION",
    "SUMMARY_MAX_CHARS",
    "build_classification_prompt",
]
