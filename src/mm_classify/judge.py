"""085 분류 스킬 — **판정**(LLM 단일 seam 경유) + 응답 해석(순수).

무엇을 하는 모듈인가: 스킬 하나와 자산 재료(요약·키워드)를 온프레미스 LLM 에 보내 "이 자산은 이
분류표에서 어떤 라벨인가"를 받고, 그 응답이 **계약을 지켰는지 코드가 검사**해 판정 결과를 돌려준다.
LLM 은 라벨을 고르기만 하고, 정책 집행(어휘·개수)은 전부 여기 있다(085 plan §Architecture).

🔴 이 모듈의 핵심 계약 — **"판정 실패"와 "해당없음"을 절대 섞지 않는다**(spec §4 · 084 §2 동형).

    공통 seam ``complete_json`` 은 빈 응답·JSON 파싱 실패를 **빈 dict ``{}``** 로 접어 돌려준다
    (호출부가 ``.get()`` 으로 안전하게 접근하도록 모양을 통일한 것). 그 편의를 그대로 "라벨 없음 =
    해당없음"으로 읽으면, **LLM 이 잠깐 죽은 순간이 "판정 완료·해당없음"으로 영구히 굳는다** —
    판정 이력은 행의 존재로 표현되므로(spec §2) 한 번 기록되면 다음 배치가 그 자산을 다시 집지
    않는다. 그래서 이 모듈은 성공/실패를 ``SkillJudgement.ok`` 로 **명시 구분**해 돌려주고,
    실패는 라벨을 비워 준다(호출부가 실수로 저장해도 행이 생기지 않는 이중 안전장치).

    비유하면 시험 채점이다 — "빈칸으로 제출"(실패·재시험 대상)과 "해당 없음이라고 답함"(정답 후보)
    은 다른 사건이다. 둘을 같은 칸에 적으면 무엇을 다시 물어야 할지 알 수 없게 된다.

🔴 두 번째 계약 — **조용한 필터링 금지**. 어휘 밖 라벨이 하나라도 섞이면 그 원소만 버리지 않고
    판정 전체를 실패로 돌린다. 어휘 밖 값은 프롬프트·설정이 잘못됐다는 신호이고(파일럿 발견 1 —
    정의문이 곧 분류), 몰래 걸러 버리면 신호가 사라진 채 절반짜리 판정이 저장된다.

LLM 호출은 ``src/llm/client.py`` **단일 seam**만 경유한다(헌법 2조 · temperature=0 · ``client=``
주입으로 네트워크 없이 테스트). ``complete_json`` 은 호출 시점에 import 한다 — 이 패키지를
불러오는 것만으로 무거운 의존성(openai)이 딸려오지 않게 하는 관례다(``src.relations`` 와 같다).

설계 배경: `specs/085-classification-skill` §4(판정 계약) · §2(행 = 판정 이력)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.mm_classify.model import UNASSIGNED_LABEL_CODE, ClassificationSkill
from src.mm_classify.prompt import build_classification_prompt

# 실패 사유 발췌를 잘라 담는 길이 — 리포트·로그에 실리는 값이라 응답 전문을 그대로 안고 다니지
# 않는다(프롬프트 원문·요약이 로그로 새는 것도 막는다).
_DETAIL_MAX_CHARS = 200


class JudgeFailure(StrEnum):
    """판정 실패 사유 — **닫힌 어휘**(배치 diff 리포트가 사유별로 셀 수 있게).

    전부 "행을 남기지 않고 다음 배치의 재대상으로 둔다"는 같은 처분을 받지만, 사유를 나눠 두면
    운영에서 원인을 갈라 볼 수 있다 — 예를 들어 ``out_of_vocab`` 이 몰리면 설정·문안 문제이고,
    ``response_shape`` 가 몰리면 LLM 서버 쪽 문제다.
    """

    RESPONSE_SHAPE = "response_shape"  # 비-dict·빈 dict(= seam 의 빈응답·파싱실패 폴백).
    LABELS_MISSING = "labels_missing"  # ``labels`` 키 자체가 없음(옛 단수 계약 응답 등).
    LABELS_NOT_LIST = "labels_not_list"  # ``labels`` 가 배열이 아님(문자열 하나 등).
    LABELS_EMPTY = "labels_empty"  # 빈 배열 — 미부여는 ``["해당없음"]`` 으로 명시해야 한다.
    ELEMENT_NOT_TEXT = "element_not_text"  # 원소가 문자열이 아니거나 공백뿐.
    OUT_OF_VOCAB = "out_of_vocab"  # 어휘 밖 라벨이 하나라도 포함(조용한 필터링 금지).
    SINGLE_POLICY = "single_policy"  # single 스킬인데 라벨이 둘 이상.
    UNASSIGNED_MIXED = "unassigned_mixed"  # 미부여와 다른 라벨이 함께 옴(모순 응답).


@dataclass(frozen=True, slots=True)
class SkillJudgement:
    """자산 하나 × 스킬 하나의 판정 결과(불변).

    ``ok`` 한 필드로 성공/실패가 갈린다:
        - ``ok=True`` — 라벨 1..N개(또는 미부여 단독). 영속 대상이다(행 = 판정 이력).
        - ``ok=False`` — ``failure`` 에 사유, ``label_*`` 는 **빈 값**. 행을 남기지 않는다(재대상).
    """

    ok: bool
    # 성공 시 라벨 표시명·저장 코드(같은 순서·같은 길이). **설정에 적힌 라벨 순서로 정규화**한다 —
    # LLM 이 순서를 흔들어도 같은 판정이면 같은 결과가 나오게(결정성 · 헌법 3조).
    label_names: tuple[str, ...] = ()
    label_codes: tuple[str, ...] = ()
    failure: JudgeFailure | None = None
    # 실패 원인 발췌(사람이 읽는 한 줄). 성공이면 빈 문자열.
    detail: str = ""

    @property
    def is_unassigned(self) -> bool:
        """성공이면서 **미부여 단독**인가(= "이 분류표에는 해당 없음"으로 판정된 자산인가)."""
        return self.ok and self.label_codes == (UNASSIGNED_LABEL_CODE,)


def _failed(failure: JudgeFailure, detail: str) -> SkillJudgement:
    """실패 판정을 만든다 — 라벨은 항상 비운다.

    Args:
        failure: 실패 사유(닫힌 어휘).
        detail: 사람이 읽는 원인 발췌. 길면 잘라 담는다.

    Returns:
        ``ok=False`` 인 ``SkillJudgement``.
    """
    return SkillJudgement(ok=False, failure=failure, detail=detail[:_DETAIL_MAX_CHARS])


def _unique_texts(raw: Sequence[Any]) -> list[str] | None:
    """응답 원소들을 문자열로 확인하고 **첫 등장 순서를 지킨 채 중복만** 없앤다.

    같은 라벨을 두 번 적은 응답은 모순이 아니라 잉여이므로 중복 제거로 흡수한다(관계 파싱에서도
    중복 제거는 정상 경로다 · ``schema.parse_llm_edges`` 관례). 반면 문자열이 아닌 원소는 흡수하지
    않는다 — 무엇을 고른 것인지 알 수 없는 응답이다.

    Args:
        raw: ``labels`` 배열의 원소들.

    Returns:
        strip 된 문자열 목록(중복 제거·순서 보존). 원소 중 하나라도 문자열이 아니거나 공백뿐이면
        ``None``(= 호출부가 ``ELEMENT_NOT_TEXT`` 실패로 처리한다).
    """
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None
        text = item.strip()
        if text not in out:
            out.append(text)
    return out


def interpret_response(skill: ClassificationSkill, response: Any) -> SkillJudgement:
    """LLM 응답을 판정 결과로 해석한다(순수 — LLM·DB 호출 없음).

    검사 순서에 뜻이 있다: **모양 → 어휘 → 정책**. 모양이 깨진 응답에서 어휘를 따질 수 없고,
    어휘 밖 값이 섞인 응답에서 개수 정책을 따지는 것은 의미가 없다(먼저 걸린 사유를 돌려준다).

    Args:
        skill: 검증된 스킬. 허용 어휘(``vocabulary``)·미부여 라벨명·선택 정책·라벨 순서를 읽는다.
        response: ``complete_json`` 이 돌려준 응답. **무엇이든 올 수 있다고 가정**한다 — 계약은
            ``{"labels": ["라벨명", …]}`` 이지만 그것을 지켰는지가 바로 이 함수의 판정 대상이다.

    Returns:
        ``SkillJudgement``. 성공이면 라벨(설정 순서로 정규화)·실패면 사유와 발췌.
    """
    # ① 모양 — 비-dict 와 **빈 dict** 를 함께 막는다. 빈 dict 는 seam 이 빈 응답·파싱 실패를 접어
    #    준 모양이라(``complete_json`` docstring) 여기서 성공으로 새면 장애가 판정으로 굳는다.
    if not isinstance(response, Mapping) or not response:
        return _failed(JudgeFailure.RESPONSE_SHAPE, f"{type(response).__name__}: {response!r}")
    if "labels" not in response:
        return _failed(JudgeFailure.LABELS_MISSING, f"응답 키: {sorted(map(str, response))}")
    raw = response["labels"]
    # 문자열 하나(``"가라벨"``)를 배열로 승격하지 않는다 — 승격하면 글자 단위 순회 같은 사고가
    # 조용히 성공으로 통과할 여지가 생긴다.
    if not isinstance(raw, (list, tuple)):
        return _failed(JudgeFailure.LABELS_NOT_LIST, f"labels 형: {type(raw).__name__} = {raw!r}")
    if not raw:
        return _failed(JudgeFailure.LABELS_EMPTY, "labels 가 빈 배열 — 미부여는 명시해야 한다")
    names = _unique_texts(raw)
    if names is None:
        return _failed(JudgeFailure.ELEMENT_NOT_TEXT, f"labels 원소: {raw!r}")

    # ② 어휘 — 하나라도 밖이면 전체 실패(조용한 필터링 금지 · spec §4).
    outside = [name for name in names if name not in skill.vocabulary]
    if outside:
        return _failed(JudgeFailure.OUT_OF_VOCAB, f"어휘 밖 라벨: {outside}")

    # ③ 정책 — 미부여는 단독이어야 하고, single 스킬은 하나여야 한다.
    unassigned = skill.policy.unassigned
    if unassigned in names and len(names) > 1:
        # "어디에도 해당 안 됨"과 "이 라벨에 해당함"은 동시에 참일 수 없다. 한쪽을 골라 저장하면
        # 추측이 데이터로 굳으므로 실패로 돌린다.
        return _failed(JudgeFailure.UNASSIGNED_MIXED, f"미부여와 라벨이 함께 옴: {names}")
    if not skill.is_multi and len(names) > 1:
        return _failed(JudgeFailure.SINGLE_POLICY, f"single 스킬에 라벨 {len(names)}개: {names}")

    # ④ 성공 — 설정 순서로 정규화한다(LLM 이 준 순서를 그대로 쓰면 같은 판정이 다르게 보인다).
    if names == [unassigned]:
        ordered: tuple[str, ...] = (unassigned,)
    else:
        ordered = tuple(name for name in skill.label_names if name in names)
    code_by_name = skill.code_by_name
    return SkillJudgement(
        ok=True,
        label_names=ordered,
        label_codes=tuple(code_by_name[name] for name in ordered),
    )


def judge_asset_labels(
    skill: ClassificationSkill,
    summary: str | None,
    keywords: Sequence[Any] | None,
    *,
    client: Any | None = None,
) -> SkillJudgement:
    """자산 하나를 스킬 하나로 판정한다(LLM 단일 seam 경유).

    스킬이 여럿이면 **스킬마다 이 함수를 따로** 부른다 — 한 프롬프트에 섞지 않는다(격리·버전 분리 ·
    085 plan §Global Constraints).

    예외를 올리지 않는다. LLM 이 이상한 답을 주거나 응답이 비어도 ``ok=False`` 인 판정으로
    돌려준다 — 자산 하나의 실패가 배치 전체를 죽이면 안 되고(실패 격리), 실패는 행을 남기지 않아
    다음 배치가 자연히 재시도한다(spec §2 — 행 부재 = 미판정/실패).

    Args:
        skill: 검증된 스킬(``load_skill`` 결과).
        summary: 자산 요약. 앞 250자만 프롬프트에 실린다. ``None`` 도 받는다(요약이 없는 자산이
            실제로 있다 — 그때는 키워드만으로 판단하게 된다).
        keywords: 자산 키워드 목록. ``None`` 이면 빈 배열로 표기한다.
        client: **테스트용 LLM 클라이언트 주입 seam** — 미주입이면 설정의 운영 온프레미스
            클라이언트를 쓴다(``src.llm.client.get_llm_client``). temperature 는 seam 기본값 0 이다
            (헌법 3조 결정 재현성 — 여기서 올리지 않는다).

    Returns:
        ``SkillJudgement`` — 성공이면 라벨, 실패면 사유. 판정 이력을 남길지 말지는 이 값의
        ``ok`` 로 결정된다(영속 계층 책임).
    """
    # 무거운 의존성(openai)을 패키지 import 시점에 끌어오지 않으려고 호출 시점에 import 한다
    # (``src.relations.llm_propose`` 와 같은 관례).
    from src.llm.client import complete_json

    prompt = build_classification_prompt(skill, summary=summary, keywords=keywords)
    return interpret_response(skill, complete_json(prompt, client=client))


__all__ = [
    "JudgeFailure",
    "SkillJudgement",
    "interpret_response",
    "judge_asset_labels",
]
