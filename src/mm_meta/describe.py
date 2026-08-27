"""084 멀티모달 메타 — **메타 설명**(개체 요약) 생성·문안 조립·응답 해석.

무엇을 하는 모듈인가: 메타 카드에 붙는 "이 묶음에 무엇이 들어 있는지" **한 문장**을 만든다.
카드가 집계값(주제·근거 키워드)만 보여 주면 성격을 파악하는 데 한 박자가 더 걸린다는 사용자
지적에서 나온 기능이다(spec §8-1). 예: "남극" 메타에 "설원 풍경과 펭귄 생태, 빙하 융해 위기".

    재료는 **구성 자산의 요약뿐**이다(각 150자) + 개체 이름·타입. 통계·주제·키워드는 넣지 않는다 —
    카드가 이미 그것을 따로 보여 주므로 설명이 그 숫자를 되풀 필요가 없다.

🔴 이 모듈의 첫째 계약 — **외부 지식 금지**(프롬프트에 명시).

    LLM 은 "제주도"라는 이름만 봐도 백과사전 지식으로 그럴듯한 문장을 쓸 수 있다. 그러면 우리
    코퍼스에 **없는 사실**이 카드에 실리고, 사용자가 그 설명을 의심할 때 **검증할 근거가 없다**.
    비유하면 서평이다 — 책을 읽고 쓴 서평은 본문으로 확인되지만, 제목만 보고 쓴 서평은 확인할
    방법이 없다. 그래서 문안이 "위 요약에 없는 사실을 새로 만들지 마라(외부 지식 금지)"를 못 박고,
    노출도 설명 + 집계 근거(주제·근거 키워드) **병행**이다(설명이 이상하면 근거로 즉시 대조된다).

🔴 둘째 계약 — **성공/실패 명시 구분**(``judge`` 와 같은 규율 · spec §2).

    공통 seam ``complete_json`` 은 빈 응답·JSON 파싱 실패를 **빈 dict** 로 접어 준다. 그것을
    "설명 없음"으로 읽어 저장하면 **LLM 이 잠깐 죽은 순간이 빈 설명으로 굳는다** — 재생성 대상
    판정은 저장된 값(``desc_member_count``·``desc_prompt_version``)으로 하므로, 빈 설명이라도
    한 번 저장되면 다음 배치가 그 메타를 다시 집지 않는다. 그래서 실패는 ``ok=False`` 로 돌려주고
    설명을 비운다(호출부가 실수로 저장해도 빈 값이 굳지 않는 이중 안전장치).

**길이 상한이 두 단계**인 이유(둘을 합치면 하나를 잃는다):

    - ``DESCRIPTION_TARGET_CHARS`` — **문안 규칙**(soft). LLM 에게 요구하는 길이다. 실측에는 이
      규칙을 조금 넘겼지만 문장으로는 정상인 산출물이 있었다.
    - ``DESCRIPTION_MAX_CHARS`` — **코드 상한**(hard). 폭주(재료를 통째로 되풀거나 구성원을 모두
      나열)만 막는다. 규칙 길이에서 자르면 위의 정상 산출물을 버리게 되고, 상한이 아예 없으면
      카드 한 줄이 무너진다. 초과는 **자르지 않고 실패**다 — 중간에서 끊긴 문장을 저장하면 그것이
      정상인지 폭주인지 나중에 알 수 없다(다음 배치가 다시 만드는 편이 낫다).

설명은 생성문이라 두 번 부르면 완전히 같지 않다. 그래도 헌법 3조를 충족하는 방식은 판정과 같다 —
**1회 생성 후 영속**이므로 저장된 설명의 재현은 100%다.

부수 효과 — 설명은 **묶음 품질 진단 도구**이기도 하다. 잡다한 묶음은 설명 문장 자체가 잡다해져
눈에 띈다("이탈리아 → 베네치아 풍경, 파스타의 정의, 카르보나라 요리법, 축구 경기 분석").

LLM 호출은 ``src/llm/client.py`` **단일 seam**만 경유한다(헌법 2조 · temperature=0 · ``client=``
주입으로 네트워크 없이 테스트). ``complete_json`` 은 호출 시점에 import 한다(``judge`` 와 같은
관례 — 패키지 import 만으로 무거운 의존성이 딸려오지 않게).

설계 배경·파일럿 실측 수치(실패·결정성·길이 분포·소요): `specs/084-entity-bundle` spec §8-1 표 ·
tasks T015
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# 설명 문안의 지문. **문안을 고칠 때마다 올린다** — 이 값이 ``node.canonical.desc_prompt_version``
# 으로 영속돼 "어떤 문안이 만든 설명인가"를 되짚고, 재생성 대상 선별의 축이 된다(spec §8-1).
# 🔴 판정 문안 판(``judge.PROMPT_VERSION``)과 **다른 지문**이어야 한다. 같은 값을 쓰면 판정 문안을
#    고칠 때 설명이 전량 재생성되고(불필요한 LLM 비용) 그 반대도 마찬가지다 — 두 문안은 따로 늙는다.
DESC_PROMPT_VERSION = "mm_meta.desc.v1"

# 구성원 요약을 프롬프트에 싣는 길이 상한(spec §8-1)의 **기본값**. 파일럿이 이 값으로 측정됐다 —
# 바꾸면 실측치의 기준선이 흔들리므로 문안 판을 함께 올린다(그래야 옛 설명과 구분된다).
# 재료는 조회(``persist.fetch_meta_members``)에서 이미 이 길이로 잘려 오고, 여기서 한 번 더 자르는
# 것은 이중 안전망이다(다른 경로로 조립할 때도 프롬프트 크기가 예측 가능해야 한다).
MEMBER_SUMMARY_MAX_CHARS = 150

# 문안이 요구하는 길이(soft). 카드 한 줄에 들어가는 분량이다. 이 값을 문안 문자열에 손으로 적지
# 않고 상수에서 끼워 넣는 이유: 손으로 적으면 상수와 갈리고, 갈리면 "60자 규칙인데 왜 80자가
# 오나"를 조사하게 된다.
DESCRIPTION_TARGET_CHARS = 60

# 코드가 받아 주는 최대 길이(hard). 규칙 길이의 **2배**로 둔다 —
#   ① 규칙을 조금 넘긴 정상 산출물이 실측에 있었고, 그것은 반드시 통과해야 한다(규칙 길이에서
#      자르면 알짜를 버린다).
#   ② 폭주는 이 값과 자릿수가 다르다: 구성원 요약(``MEMBER_SUMMARY_MAX_CHARS``) × N 을 되풀거나
#      목록을 나열하면 수백 자가 된다. 2배면 정상 산출물의 여유분이면서 폭주는 확실히 걸린다.
#   ③ 카드 한 줄 표시가 무너지는 지점도 이 부근이다(설명은 한 문장이라는 계약).
DESCRIPTION_MAX_CHARS = DESCRIPTION_TARGET_CHARS * 2

# 응답 최상위 키. 파일럿 문안이 한국어 키를 썼고 그 문안으로 실측치가 나왔으므로 그대로 유지한다
# (영문 키로 바꾸면 재검증이 필요하다 · ``judge.JUDGEMENT_KEY`` 와 같은 이유).
DESCRIPTION_KEY = "설명"

# 모달리티가 비었을 때 프롬프트에 적는 값. 새 말을 만들지 않고 **DB 어휘를 그대로** 쓴다 —
# 빈 STT 자산의 ``asset.modality`` 가 실제로 ``'unknown'`` 이다(2026-07 결정으로 그대로 두기로 했다).
_UNKNOWN_MODALITY = "unknown"

# 실패 사유 발췌를 잘라 담는 길이 — 리포트·로그에 실리는 값이라 응답 전문을 안고 다니지 않는다
# (요약 원문이 로그로 새는 것도 막는다 · ``judge`` 와 같은 값·이유).
_DETAIL_MAX_CHARS = 200


class DescribeFailure(StrEnum):
    """설명 생성 실패 사유 — **닫힌 어휘**(배치 리포트가 사유별로 셀 수 있게).

    전부 "저장하지 않고 다음 배치의 재대상으로 둔다"는 같은 처분을 받지만, 사유를 나눠 두면 원인을
    갈라 볼 수 있다 — ``response_shape`` 가 몰리면 LLM 서버 쪽 문제이고, ``description_too_long``
    이 몰리면 문안 문제다(파일럿 기준선이 흔들렸다는 신호).
    """

    NO_MEMBERS = "no_members"  # 설명할 재료(구성 자산 요약)가 없음 — LLM 을 부르지 않는다.
    RESPONSE_SHAPE = "response_shape"  # 비-dict·빈 dict(= seam 의 빈응답·파싱실패 폴백).
    DESCRIPTION_MISSING = "description_missing"  # 최상위에 ``설명`` 키가 없음.
    DESCRIPTION_NOT_STR = "description_not_str"  # ``설명`` 값이 문자열이 아님(숫자·목록·dict·null).
    DESCRIPTION_EMPTY = "description_empty"  # 다듬으면 빈 문자열 — 저장할 값이 없다.
    DESCRIPTION_TOO_LONG = "description_too_long"  # 코드 상한 초과(폭주) — 자르지 않고 버린다.


@dataclass(frozen=True, slots=True)
class MetaDescription:
    """메타 하나의 설명 생성 결과(불변).

    ``ok`` 한 필드로 성공/실패가 갈린다:
        - ``ok=True`` — ``description`` 은 비어 있지 않다(저장해도 되는 값).
        - ``ok=False`` — ``failure`` 에 사유, ``description`` 은 **빈 문자열**. 저장하지 않는다.
    """

    ok: bool
    description: str = ""
    failure: DescribeFailure | None = None
    # 실패 원인 발췌(사람이 읽는 한 줄). 성공이면 빈 문자열.
    detail: str = ""

    @property
    def exceeds_target(self) -> bool:
        """문안 규칙(``DESCRIPTION_TARGET_CHARS``)을 넘겼는가 — 성공이지만 리포트가 세어 두는 값.

        "규칙 초과 N건"은 파일럿에서 문안 품질의 신호였다. 실패는 설명이 없는 상태이므로
        여기서 ``False`` 다 — 길이 통계에 섞이면 리포트가 거짓이 된다.
        """
        return self.ok and len(self.description) > DESCRIPTION_TARGET_CHARS


def _failed(failure: DescribeFailure, detail: str) -> MetaDescription:
    """실패 결과를 만든다 — 설명은 항상 비운다.

    Args:
        failure: 실패 사유(닫힌 어휘).
        detail: 사람이 읽는 원인 발췌. 길면 잘라 담는다.

    Returns:
        ``ok=False`` 인 ``MetaDescription``.
    """
    return MetaDescription(ok=False, failure=failure, detail=detail[:_DETAIL_MAX_CHARS])


def _resolve_summary_limit(summary_max_chars: int) -> int:
    """구성원 요약 상한을 확정한다(잘못된 값은 fail-fast).

    ``judge._resolve_summary_limit`` 과 쌍둥이지만 따로 둔다 — 기본값(150 vs 250)과 잘못된 값일
    때의 안내 문구가 다르다(이쪽은 재료가 0 이면 설명이 통째로 환각이 된다).

    Args:
        summary_max_chars: 호출부가 준 상한. **1 미만은 예외** — 0 을 허용하면 재료 없는 프롬프트가
            조용히 나가고 LLM 이 이름만 보고 문장을 지어낸다(외부 지식 금지 규칙이 무력해진다).

    Returns:
        실제로 쓸 상한(1 이상 정수).

    Raises:
        ValueError: 1 미만이거나 정수로 읽을 수 없는 값일 때.
    """
    limit = int(summary_max_chars)
    if limit < 1:
        raise ValueError(
            f"구성원 요약 상한은 1 이상이어야 한다: {summary_max_chars!r} "
            "(0 이면 재료 없는 프롬프트가 나가고 설명이 통째로 환각이 된다)"
        )
    return limit


def _resolve_labels(name: str | None, entity_type: str | None) -> tuple[str, str]:
    """메타 이름·타입을 프롬프트에 실을 값으로 확정한다(빈 값은 fail-fast).

    재료(요약)가 없는 것은 **데이터 사정**이라 실패로 돌려주지만, 이름이 빈 것은 **호출 오류**다 —
    이름 없는 카드에는 설명을 붙일 자리가 없다(조회는 ``COALESCE(name, entity_uid)`` 로 늘 채워
    준다). 그래서 이쪽은 예외로 즉시 멈춘다(``judge`` 가 잘못된 상한에 예외를 올리는 것과 같은 결).

    Args:
        name: 메타 대표 표기. 앞뒤 공백은 다듬는다. 빈 값이면 예외.
        entity_type: 개체 타입. 빈 값이면 예외. **어휘 검사는 하지 않는다** — 저장 계층
            (``persist.ensure_entity_node``)이 등록 어휘만 허용하므로 조회로 읽어 온 값은 어휘 안이고,
            여기서 또 검사하면 어휘를 늘릴 때 고칠 곳만 늘어난다.
            ✅ 그 판단이 값을 했다: 2026-08-27 어휘를 6종(`음식` 추가)으로 늘릴 때 이 파일은
            **한 줄도 고치지 않았다**(타입 이름을 문안에 그대로 싣기 때문이다 · spec 087 T009).

    Returns:
        ``(이름, 타입)`` — 둘 다 다듬어진 문자열.

    Raises:
        ValueError: 이름이나 타입이 비었을 때.
    """
    label = str(name or "").strip()
    kind = str(entity_type or "").strip()
    if not label or not kind:
        raise ValueError(
            f"메타 이름·타입이 있어야 설명을 만든다: name={name!r} entity_type={entity_type!r}"
        )
    return label, kind


def _clean_members(
    members: Iterable[Any] | None, summary_limit: int
) -> list[tuple[str, str]]:
    """구성원 목록을 프롬프트에 실을 ``(모달리티, 요약)`` 목록으로 다듬는다(순서 보존).

    문안 조립과 재료 유무 판정이 **같은 목록**을 봐야 한다 — 한쪽만 다듬으면 "자산 N건"과 실제로
    실린 줄 수가 어긋나 LLM 에 모순된 재료를 준다. 그래서 두 곳이 이 함수를 공유한다.

    Args:
        members: ``(모달리티, 요약)`` 쌍의 목록. **요약이 비거나 ``None`` 인 구성원은 버린다** —
            요약 없는 자산(빈 STT 등)이 실제로 있고, 그 줄은 재료가 아니라 잡음이다(``"- (audio) "``
            같은 빈 줄은 LLM 을 흔든다). ``None``(목록 자체가 없음)이면 빈 목록.
        summary_limit: 요약 하나를 실을 길이 상한(1 이상 · 이미 확정된 값).

    Returns:
        다듬어진 ``(모달리티, 요약)`` 목록. 모달리티가 비면 ``unknown``(DB 어휘)으로 채운다.
    """
    cleaned: list[tuple[str, str]] = []
    for member in members or ():
        modality, summary = member
        text = str(summary or "").strip()[:summary_limit]
        if not text:
            continue  # 재료 없는 줄은 싣지 않는다(건수도 함께 줄어든다)
        cleaned.append((str(modality or "").strip() or _UNKNOWN_MODALITY, text))
    return cleaned


def build_description_prompt(
    name: str,
    entity_type: str,
    members: Iterable[Any] | None,
    *,
    summary_max_chars: int = MEMBER_SUMMARY_MAX_CHARS,
) -> str:
    """메타 설명 프롬프트를 조립한다(순수 · 같은 입력 → 같은 문안).

    문안은 **파일럿 기준선**이다(실측치가 이 문안에서 나왔다) — 고치면 그 수치와 비교할 수 없게
    되므로 ``DESC_PROMPT_VERSION`` 을 함께 올린다. 규칙 세 줄이 각자 막는 것:

        - 상투어 금지 — 초안은 2문장이었고 첫 문장이 "이 개체는 세종대왕입니다" 류 **무정보 서두**로
          채워졌다. 1문장으로 조이고 서두를 금지해 내용부터 쓰게 했다.
        - 🔴 외부 지식 금지 — 환각 억제의 핵심(모듈 docstring). 이 줄이 빠지면 코퍼스에 없는 사실이
          카드에 실리고 검증 경로가 사라진다.
        - 60자·명사형 — 카드 한 줄에 들어가고 문장체 잡음("~입니다")이 붙지 않게.

    Args:
        name: 메타 대표 표기(카드 간판). 빈 값이면 ``ValueError``.
        entity_type: 개체 타입(인물·장소·조직·작품·사건). 빈 값이면 ``ValueError``.
        members: ``(모달리티, 요약)`` 쌍의 목록. **요약이 빈 구성원은 빠지고 건수도 함께 줄어든다**
            (프롬프트의 "자산 N건"은 실제로 실린 줄 수다). 빈 목록이면 재료 없는 문안이 되므로
            부르는 쪽(``describe_meta``)이 먼저 걸러야 한다.
        summary_max_chars: 구성원 요약 하나를 실을 길이 상한(글자). 기본값이 파일럿 기준선이며
            **미주입이 곧 기존 동작**이다. 재료는 조회 단계에서 이미 잘려 오므로 이 값은 이중
            안전망이다. 1 미만이면 ``ValueError``.

    Returns:
        LLM 에 그대로 넘길 단일 프롬프트 문자열.

    Raises:
        ValueError: 이름·타입이 비었거나 ``summary_max_chars`` 가 1 미만일 때.
    """
    label, kind = _resolve_labels(name, entity_type)
    usable = _clean_members(members, _resolve_summary_limit(summary_max_chars))
    lines = [f'"{label}"({kind}) 로 묶인 자산 {len(usable)}건의 요약이다.']
    lines.extend(f"- ({modality}) {summary}" for modality, summary in usable)
    lines.extend([
        "",  # 재료와 지시를 빈 줄로 가른다(파일럿 문안 그대로)
        "이 묶음에 **무엇이 들어 있는지** 한 문장으로 요약하라. 규칙:",
        '- "이 개체는~"·"이 묶음은~" 같은 상투적 서두를 쓰지 마라. 내용부터 바로 쓴다.',
        "- 위 요약에 없는 사실을 새로 만들지 마라(외부 지식 금지).",
        f"- {DESCRIPTION_TARGET_CHARS}자 이내. 명사형으로 끝낸다.",
        f'JSON 하나로만 답하라: {{"{DESCRIPTION_KEY}": "..."}}',
    ])
    return "\n".join(lines)


def interpret_description(response: Any) -> MetaDescription:
    """LLM 응답을 설명 결과로 해석한다(순수 — LLM·DB 호출 없음).

    검사 순서에 뜻이 있다: **모양 → 키 → 값 → 길이**. 먼저 걸린 사유를 돌려주므로 리포트에서
    원인 층을 가릴 수 있다(응답이 통째로 깨진 것과 문장이 길어진 것은 다른 문제다).

    Args:
        response: ``complete_json`` 이 돌려준 응답. 계약은 ``{"설명": "…"}`` 이지만 그것을 지켰는지가
            이 함수의 판정 대상이므로 **무엇이든 올 수 있다고 가정**한다.

    Returns:
        ``MetaDescription`` — 성공이면 다듬어진 한 문장, 실패면 사유와 발췌(설명은 빈 문자열).
    """
    # ① 모양 — 비-dict 와 **빈 dict** 를 함께 막는다. 빈 dict 는 seam 이 빈 응답·파싱 실패를 접어
    #    준 모양이라(``complete_json`` docstring) 여기서 성공으로 새면 장애가 빈 설명으로 굳는다.
    if not isinstance(response, Mapping) or not response:
        return _failed(DescribeFailure.RESPONSE_SHAPE, f"{type(response).__name__}: {response!r}")
    if DESCRIPTION_KEY not in response:
        return _failed(DescribeFailure.DESCRIPTION_MISSING, f"응답 키: {sorted(map(str, response))}")

    raw = response[DESCRIPTION_KEY]
    # bool 은 int 의 하위형이라 ``isinstance(raw, str)`` 로 한 번에 걸린다(문자열이 아닌 것은 전부 여기).
    if not isinstance(raw, str):
        return _failed(DescribeFailure.DESCRIPTION_NOT_STR,
                       f"설명 형: {type(raw).__name__} = {raw!r}")

    # 카드 한 줄에 그대로 나가는 값이라 양끝 공백을 자르고 **개행·연속 공백을 한 칸으로** 눌러
    # 담는다(줄바꿈이 남으면 카드가 흐트러지고, 두 칸 공백은 표기 차이로 남는다).
    text = " ".join(raw.split())
    if not text:
        return _failed(DescribeFailure.DESCRIPTION_EMPTY, f"공백뿐: {raw!r}")
    if len(text) > DESCRIPTION_MAX_CHARS:
        # 자르지 않는다 — 중간에서 끊긴 문장을 저장하면 그것이 정상인지 폭주인지 나중에 알 수 없다.
        return _failed(DescribeFailure.DESCRIPTION_TOO_LONG,
                       f"{len(text)}자(상한 {DESCRIPTION_MAX_CHARS}): {text}")
    return MetaDescription(ok=True, description=text)


def describe_meta(
    name: str,
    entity_type: str,
    members: Iterable[Any] | None,
    *,
    client: Any | None = None,
) -> MetaDescription:
    """메타 하나의 설명을 만든다(LLM 단일 seam 경유 · 메타당 호출 1회).

    **저장은 하지 않는다** — 영속은 ``persist.upsert_meta_description`` 의 일이고, 🔴 **성공
    (``ok=True``)만 저장한다**. 실패를 저장하면 그 메타는 다음 배치에서 "설명 있음"으로 보여 영구히
    다시 만들어지지 않는다(재생성 판정이 저장된 값으로 이뤄지기 때문이다 · spec §8-1).

    응답 내용에 대해서는 예외를 올리지 않는다(깨진 JSON·빈 응답·긴 문장 → ``ok=False``). 다만
    **전송 계층 예외**(연결 실패 등)는 그대로 오른다 — 메타 단위 실패 격리는 배치의 책임이다
    (``judge.judge_asset_entities`` 와 같은 경계).

    Args:
        name: 메타 대표 표기. 빈 값이면 **LLM 을 부르기 전에** ``ValueError``.
        entity_type: 개체 타입. 빈 값이면 ``ValueError``.
        members: ``(모달리티, 요약)`` 쌍의 목록(``persist.fetch_meta_members`` 결과를 그대로 넘긴다).
            요약이 있는 구성원이 **하나도 없으면 LLM 을 부르지 않고** ``NO_MEMBERS`` 실패를 돌려준다
            — 재료 없는 호출은 비용만 들고 결과는 환각뿐이다(외부 지식 금지 규칙이 무력해진다).
        client: **테스트용 LLM 클라이언트 주입 seam** — 미주입이면 설정의 운영 온프레미스
            클라이언트를 쓴다(``src.llm.client.get_llm_client``). temperature 는 seam 기본값 0 이다
            (헌법 3조 결정 재현성 — 여기서 올리지 않는다).

    Returns:
        ``MetaDescription``. 저장 여부는 이 값의 ``ok`` 로 결정된다(영속 계층 책임).

    Raises:
        ValueError: 이름·타입이 비었을 때(**LLM 을 부르기 전에** 터진다).
    """
    # 이름 검사를 재료 검사보다 **먼저** 한다 — 호출이 잘못된 배치는 첫 메타에서 즉시 멈춰야 한다
    # (재료 없는 메타가 먼저 오면 잘못된 호출이 조용히 지나간다 · ``judge`` 와 같은 순서 규율).
    label, kind = _resolve_labels(name, entity_type)
    usable = _clean_members(members, MEMBER_SUMMARY_MAX_CHARS)
    if not usable:
        return _failed(DescribeFailure.NO_MEMBERS, f"요약 있는 구성원 0건: {name!r}")

    # 무거운 의존성(openai)을 패키지 import 시점에 끌어오지 않으려고 호출 시점에 import 한다
    # (``judge``·``src.mm_classify.judge`` 와 같은 관례).
    from src.llm.client import complete_json

    prompt = build_description_prompt(label, kind, usable)
    return interpret_description(complete_json(prompt, client=client))


__all__ = [
    "DESCRIPTION_KEY",
    "DESCRIPTION_MAX_CHARS",
    "DESCRIPTION_TARGET_CHARS",
    "DESC_PROMPT_VERSION",
    "MEMBER_SUMMARY_MAX_CHARS",
    "DescribeFailure",
    "MetaDescription",
    "build_description_prompt",
    "describe_meta",
    "interpret_description",
]
