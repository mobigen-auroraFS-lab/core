"""085 **분류 스킬**(mm_classify) — 사용자 정의 분류셋으로 자산을 분류하는 순수 로직 패키지.

무엇을 하는 패키지인가: 사람이 선언한 분류 체계(= **스킬**, 예: ``{레시피, 식문화, 맛집·외식}``)를
받아 자산마다 라벨을 정한다. 주제 축(고정 표준 분류)이 못 주는 **배포·사용자별 맞춤 분류**를
새 패싯 축으로 제공하는 것이 목적이다(085 spec §목적).

핵심 원칙 — **스킬 = 데이터, 엔진 = 고정**(085 plan §Global Constraints)
    스킬에서 실행 로직(코드)을 받지 않는다. 설정은 두 갈래로만 소비된다:
      1. **프롬프트 반쪽** — 라벨명·정의문·경계 문장이 LLM 프롬프트로 조립된다(``prompt``).
      2. **집행 반쪽** — 선택 정책(multi/single)·어휘 검증·버전은 **순수 코드가 결정적으로 집행**
         한다(``model``·``judge``). LLM 에게 정책을 맡기지 않는다.

흐름(요약)
    1. ``load_skill``: 설정 dict → 검증된 ``ClassificationSkill``(fail-fast · ``model``).
    2. ``build_classification_prompt``: 스킬 **하나**를 프롬프트 한 개로 조립(``prompt``).
       ⚠️ 스킬을 여러 개 섞지 않는다 — 스킬마다 별도 호출(격리·버전 분리).
    3. ``judge_asset_labels``: LLM 단일 seam 경유 판정 → **성공/실패를 명시 구분**해 반환(``judge``).
       실패를 "해당없음"으로 뭉개지 않는 것이 이 패키지의 핵심 계약이다(spec §4).
    4. (후속 T105) ``persist``: 판정 결과를 ``asset_mm_skill_label`` 에 영속 — 행 존재 = 판정 이력.

의존성
    ``model``·``prompt``·``judge`` 는 표준 라이브러리만 쓰는 순수 모듈이다. LLM 클라이언트
    (``openai``)는 ``judge`` 가 **호출 시점에** import 하므로 이 패키지를 불러오는 것만으로
    무거운 의존성이 딸려오지 않는다(``src.relations`` 지연 로드 관례와 같다).

설계 배경: `specs/085-classification-skill`(spec §1~§4 · plan §파일 배치)
"""

from __future__ import annotations

from src.mm_classify.judge import (
    JudgeFailure,
    SkillJudgement,
    interpret_response,
    judge_asset_labels,
)
from src.mm_classify.model import (
    DEFAULT_MAX_LABELS,
    SELECTION_MODES,
    UNASSIGNED_LABEL_CODE,
    ClassificationSkill,
    SkillConfigError,
    SkillLabel,
    SkillPolicy,
    load_skill,
)
from src.mm_classify.prompt import PROMPT_VERSION, build_classification_prompt

__all__ = [
    "DEFAULT_MAX_LABELS",
    "PROMPT_VERSION",
    "SELECTION_MODES",
    "UNASSIGNED_LABEL_CODE",
    "ClassificationSkill",
    "JudgeFailure",
    "SkillConfigError",
    "SkillJudgement",
    "SkillLabel",
    "SkillPolicy",
    "build_classification_prompt",
    "interpret_response",
    "judge_asset_labels",
    "load_skill",
]
