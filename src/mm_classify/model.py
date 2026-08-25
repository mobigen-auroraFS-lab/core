"""085 분류 스킬 — **설정 객체** 파싱·검증(순수·fail-fast).

무엇을 하는 모듈인가: 사람이 손으로 쓴 분류 체계(= **스킬**)를 코드가 다룰 수 있는 객체로 바꾸고,
그 과정에서 **쓸 수 없는 설정을 즉시 거부**한다. 스킬은 "이 배포에서는 자산을 이런 렌즈로 나눠 봐라"는
선언이다 — 예를 들어 ``{레시피, 식문화, 맛집·외식}`` 세 라벨을 등록하면 배치가 그 눈으로 자산을 훑는다.

비유하면 **분류 서식 접수 창구**다. 칸이 비었거나(정의문 누락) 같은 이름이 두 번 적혀 있거나
(라벨명 중복) 장수 제한을 넘겼으면(라벨 수 상한) 그 자리에서 반려한다. 접수(= DB 등록)된 뒤에는
그 설정이 정본이 되어 분류 결과가 쌓이므로, 뒤늦게 고치면 이미 쌓인 판정과 어긋난다.

왜 이렇게 깐깐한가(085 spec §왜 · 파일럿 발견 1): 파일럿에서 **유일하게 의미 있던 오배정이
정의문 문구 탓**이었다 — 정의문에 "영양 지식"이라고 적어 두자 판정이 그 문구에 충실하게 따라갔다.
즉 **정의문이 곧 분류기**다. 그래서 라벨명만 적은 설정은 등록 자체를 막고(``definition``·``not``
필수), 나머지 정책은 LLM 이 아니라 **코드가 결정적으로 집행**한다(085 plan §Architecture — 설정의
두 갈래 소비: 프롬프트 반쪽 / 집행 반쪽).

여기 있는 것은 전부 **순수 함수·불변 객체**다 — 파일 I/O·DB·LLM 을 부르지 않는다(파일 읽기는 등록
CLI 몫). 같은 설정이면 언제나 같은 스킬 객체가 나온다(헌법 3조 결정 재현성).

설계 배경: `specs/085-classification-skill` §1(설정 객체)·§3(등록 게이트)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# 라벨 수 상한 기본값(spec §1) — 프롬프트 비대 가드다. 라벨이 수백 개면 정의문까지 실려 프롬프트가
# 부풀고, 판정 품질·비용·결정성이 함께 나빠진다. 설정에서 ``policy.max_labels`` 로 조정한다.
DEFAULT_MAX_LABELS = 30

# 선택 정책 어휘. ``multi`` = 해당하는 라벨을 **모두**(자산 하나가 1..N 라벨) · ``single`` = 딱 하나.
# 순서가 곧 표기 순서다(오류 메시지·문서에서 multi 를 먼저 보여준다 — 기본값이므로).
SELECTION_MODES = ("multi", "single")

# 미부여(= "해당없음") 판정을 **행으로 남길 때** 쓰는 예약 label_code(spec §2 — 해당없음이면
# unassigned 라벨 단독 1행). 표시 이름은 스킬마다 다르지만(``policy.unassigned``) 저장 코드는
# 배포 전체에서 하나로 고정한다 — 스킬별로 다르면 "미부여 비율" 집계가 스킬마다 갈라진다.
UNASSIGNED_LABEL_CODE = "unassigned"

# 084 F05 — **타입 어휘 저장용 예약 skill_code**(spec §10). 개체 타입 5종(인물·장소·조직·작품·사건)의
# 정의문을 담는 행이며, ``mm_skill`` 테이블을 **저장소로만** 빌려 쓴다(마이그레이션 0).
MM_META_TYPE_SKILL_CODE = "mm_meta_type"

# 🔴 **분류 배치가 집으면 안 되는 skill_code 집합**(엔진 격리 · spec §10 표).
#   왜 필요한가: 085 배치는 ``mm_skill`` 의 active 행을 전부 집어 **자산마다 LLM 판정**을 돈다.
#   그런데 위 어휘 행은 분류 대상이 **자산이 아니라 개체**이고, 판정도 별도 배치가 아니라 개체 추출과
#   같은 호출에서 난다(문맥이 있어야 동음이의를 가르므로). 방어가 없으면 어휘를 등록하는 순간 전
#   자산이 "인물/장소/…"로 분류되기 시작한다 — 쓰이지도 않는 판정에 LLM 비용이 들고 패싯 축이 오염된다.
#   비유하면 창고를 같이 쓰는 것과 같은 기계를 쓰는 것의 차이다. 선반(테이블)은 나눠 써도, 남의 물건을
#   내 컨베이어에 올리면 안 된다.
#   같은 결의 선례: 관계 카탈로그의 ``PROMPT_EXCLUDED_KIND_CODES``(``src/relations/schema.py``) —
#   ``mm_member`` 종류가 active 라는 이유로 LLM 관계 프롬프트에 실리던 문제를 같은 방식으로 막았다.
#   기준을 한 집합으로 모아 두는 이유는 사본이 생기면 언젠가 한쪽만 고쳐지기 때문이다.
NON_CLASSIFY_SKILL_CODES: frozenset[str] = frozenset({MM_META_TYPE_SKILL_CODE})

# 코드 문법 — 소문자 스네이크. 문자 집합은 관계 ``kind_code``(``src/relations/schema.py``)와 같은
# 규칙을 쓰고, 길이 상한 100 자는 그 컬럼(``relation_kind.kind_code VARCHAR(100)``) 관례를 따른다.
# 코드는 DB PK·OpenSearch 패싯 키(``"스킬코드/라벨코드"``)·API 파라미터로 그대로 나가므로,
# 공백·대문자·한글을 허용하면 URL 인코딩과 키 파싱이 지저분해진다.
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")

# 최상위·정책·라벨에서 **허용하는 키 전부**. 모르는 키는 거부한다 — ``labelss``·``max_label`` 같은
# 오타를 조용히 무시하면 "라벨 없는 스킬"이나 "상한이 기본값으로 되돌아간 스킬"이 등록된다.
_SKILL_KEYS = frozenset({"skill", "skill_code", "version", "policy", "labels"})
_POLICY_KEYS = frozenset({"selection", "unassigned", "max_labels"})
_LABEL_KEYS = frozenset({"code", "name", "definition", "not"})


class SkillConfigError(ValueError):
    """분류 스킬 설정이 쓸 수 없는 상태일 때(필수 필드 누락·문법 위반·중복·상한 초과).

    등록 CLI 가 ``--preview``/``--apply`` 전에 이 예외로 **즉시 중단**한다(spec §3 fail-fast).
    ``ValueError`` 하위형이라 호출부가 일반 값 오류로 잡아도 동작한다(``ExtMetaValidationError`` 선례).
    """


@dataclass(frozen=True, slots=True)
class SkillLabel:
    """스킬의 라벨 하나 — 코드·표시명·정의문·경계.

    ``definition`` 과 ``exclusion`` 은 **프롬프트로 그대로 나간다**(085 prompt). 즉 이 두 문장이
    분류 기준 자체이며, 문구를 고치면 분류 결과가 바뀐다(그래서 개정 = 새 버전 + 백필 · spec §3).
    """

    code: str
    name: str
    definition: str
    # JSON 키 ``not`` 은 파이썬 예약어라 필드명을 ``exclusion``(제외 경계)으로 둔다.
    # 뜻: "이 라벨이 **아닌 것**" — 파일럿 문안의 "아닌 것: …" 자리에 실린다.
    exclusion: str


@dataclass(frozen=True, slots=True)
class SkillPolicy:
    """스킬의 집행 정책 — 코드가 결정적으로 지키는 규칙(LLM 에게 맡기지 않는다).

    ``selection`` 은 프롬프트 문안과 응답 검증 **양쪽**에 쓰인다 — 문안으로 "하나만 골라라"라고
    지시하고, 그래도 둘이 오면 판정 실패로 처리한다(spec §4 — 지시와 검증의 이중 장치).
    """

    selection: str
    unassigned: str
    max_labels: int


@dataclass(frozen=True, slots=True)
class ClassificationSkill:
    """검증을 통과한 분류 스킬 하나(불변).

    한 스킬 = 한 LLM 호출이다 — 여러 스킬을 한 프롬프트에 섞지 않는다(격리·버전 분리 ·
    085 plan §Global Constraints). 그래서 프롬프트·판정 함수는 이 객체 **하나만** 받는다.
    """

    name: str
    version: int
    policy: SkillPolicy
    labels: tuple[SkillLabel, ...]
    # DB 자연키(``mm_skill.skill_code``)·OS 패싯 키(``"스킬코드/라벨코드"``)·API 파라미터로 그대로
    # 나가는 코드. **필수**다(구현확정 G1) — 등록은 preview→apply 2단계이고 그 사이 재현의 단위가
    # 파일 하나이므로, CLI 인자로 빼면 같은 파일이 다른 스킬로 등록될 수 있다.
    skill_code: str

    @property
    def is_multi(self) -> bool:
        """``selection`` 이 multi 인가(= 라벨을 여러 개 고를 수 있는 스킬인가)."""
        return self.policy.selection == "multi"

    @property
    def label_codes(self) -> tuple[str, ...]:
        """라벨 코드들 — **설정에 적힌 순서 그대로**(정렬하지 않는다)."""
        return tuple(label.code for label in self.labels)

    @property
    def label_names(self) -> tuple[str, ...]:
        """라벨 표시명들 — 설정 순서 그대로. 프롬프트 나열 순서이자 LLM 응답의 어휘다."""
        return tuple(label.name for label in self.labels)

    @property
    def vocabulary(self) -> frozenset[str]:
        """LLM 응답에서 **허용되는 라벨명 전부**(라벨 표시명 + 미부여 라벨명).

        judge 가 "어휘 밖 원소가 하나라도 있으면 판정 실패"를 판정할 때 쓰는 기준집합이다
        (spec §4 — 조용한 필터링 금지).
        """
        return frozenset(self.label_names) | {self.policy.unassigned}

    @property
    def code_by_name(self) -> dict[str, str]:
        """라벨명 → 저장용 코드 맵. 미부여 라벨명은 ``UNASSIGNED_LABEL_CODE`` 로 간다.

        LLM 은 사람이 읽는 **라벨명**으로 답하지만(spec §4) 저장·패싯 키는 **코드**다. 이름은
        개정으로 바뀔 수 있고 코드는 고정이므로, 이름을 코드로 바꿔 두는 지점이 필요하다.
        """
        out = {label.name: label.code for label in self.labels}
        out[self.policy.unassigned] = UNASSIGNED_LABEL_CODE
        return out


def _fail(where: str, reason: str) -> SkillConfigError:
    """진단 가능한 오류를 만든다 — **어디가**(경로) **왜**(사유) 틀렸는지 함께 담는다.

    손으로 쓴 설정을 고치는 사람이 읽는 메시지다. ``labels[1].definition`` 처럼 위치를 적어야
    라벨이 30개일 때 어느 줄을 고칠지 알 수 있다.

    Args:
        where: 문제가 난 위치 경로(예: ``policy.selection``·``labels[2].code``).
        reason: 사람이 읽는 사유(한국어).

    Returns:
        메시지가 채워진 ``SkillConfigError``(호출부가 ``raise`` 한다).
    """
    return SkillConfigError(f"분류 스킬 설정 오류: {where} — {reason}")


def _require_mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    """dict(JSON 객체)여야 하는 자리를 검증한다.

    Args:
        value: 검사할 값(무엇이든 올 수 있다 — JSON 파일에서 온 값이다).
        where: 오류 메시지에 쓸 위치 경로.

    Returns:
        같은 값(Mapping 으로 좁혀서).

    Raises:
        SkillConfigError: dict 가 아닐 때. 리스트·문자열이 오면 아래 키 접근이 엉뚱하게 성공하거나
            (문자열 인덱싱) 조용히 실패하므로 여기서 끊는다.
    """
    if not isinstance(value, Mapping):
        raise _fail(where, f"JSON 객체(dict)여야 한다(받은 형: {type(value).__name__})")
    return value


def _reject_unknown_keys(
    container: Mapping[str, Any], allowed: frozenset[str], *, where: str
) -> None:
    """허용 키 밖의 키가 있으면 거부한다(오타 가드).

    Args:
        container: 검사할 dict.
        allowed: 허용 키 집합.
        where: 오류 메시지에 쓸 위치 경로.

    Raises:
        SkillConfigError: 모르는 키가 하나라도 있을 때. 키 이름을 **정렬해** 보여준다(같은 입력이면
            같은 메시지 — 테스트·로그 안정).
    """
    unknown = sorted(str(k) for k in container if k not in allowed)
    if unknown:
        raise _fail(
            where,
            f"알 수 없는 키 {unknown} (허용: {sorted(allowed)}) — 오타를 무시하면 의도와 다른 설정이 등록된다",
        )


def _require_text(container: Mapping[str, Any], key: str, *, where: str) -> str:
    """필수 문자열 필드를 꺼내 검증하고 **앞뒤 공백을 잘라** 돌려준다.

    Args:
        container: 값을 꺼낼 dict.
        key: 필드 이름.
        where: 오류 메시지에 쓸 위치 경로(필드 이름이 뒤에 붙는다).

    Returns:
        strip 된 문자열.

    Raises:
        SkillConfigError: 키가 없거나, 문자열이 아니거나, 공백뿐일 때. 공백뿐인 정의문을 통과시키면
            "라벨명만 등록"이 우회로 성립한다(spec §1 금지).
    """
    if key not in container:
        raise _fail(f"{where}.{key}", "필수 항목이 없다")
    value = container[key]
    if not isinstance(value, str):
        raise _fail(f"{where}.{key}", f"문자열이어야 한다(받은 형: {type(value).__name__})")
    text = value.strip()
    if not text:
        raise _fail(f"{where}.{key}", "빈 값을 쓸 수 없다")
    return text


def _require_positive_int(
    container: Mapping[str, Any],
    key: str,
    *,
    where: str,
    default: int | None = None,
) -> int:
    """1 이상 정수 필드를 꺼내 검증한다.

    Args:
        container: 값을 꺼낼 dict.
        key: 필드 이름.
        where: 오류 메시지에 쓸 위치 경로.
        default: 키가 없을 때 쓸 기본값. ``None``(기본)이면 **필수 항목**이라 없으면 거부한다.

    Returns:
        검증된 정수.

    Raises:
        SkillConfigError: 키가 없고 기본값도 없을 때, 정수가 아닐 때(``"1"``·``1.0``·``True``),
            또는 1 미만일 때. ``bool`` 은 파이썬에서 ``int`` 하위형이라 **따로 막는다** —
            ``version: true`` 가 1 로 통과하면 버전 비교(백필 선별)가 조용히 망가진다.
    """
    if key not in container:
        if default is None:
            raise _fail(f"{where}.{key}", "필수 항목이 없다")
        return default
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{where}.{key}", f"1 이상 정수여야 한다(받은 값: {value!r})")
    if value < 1:
        raise _fail(f"{where}.{key}", f"1 이상이어야 한다(받은 값: {value})")
    return value


def _require_code(value: str, *, where: str) -> str:
    """코드 문법(소문자 스네이크·100자 이하)을 검증한다.

    Args:
        value: 검사할 코드 문자열(이미 strip 된 값).
        where: 오류 메시지에 쓸 위치 경로.

    Returns:
        같은 코드.

    Raises:
        SkillConfigError: 문법 위반일 때. 코드는 DB 키·패싯 키(``"스킬코드/라벨코드"``)·API
            파라미터로 그대로 나가므로 공백·대문자·한글·기호를 허용하지 않는다.
    """
    if not _CODE_RE.match(value):
        raise _fail(
            where,
            f"코드 문법 위반: {value!r} — 소문자로 시작하는 [a-z][a-z0-9_]* 100자 이하여야 한다",
        )
    return value


def _parse_policy(raw: Any) -> SkillPolicy:
    """``policy`` 블록을 파싱·검증한다.

    Args:
        raw: 설정의 ``policy`` 값(dict 여야 한다).

    Returns:
        검증된 ``SkillPolicy``. ``selection`` 미지정은 **multi**(spec §1 기본값),
        ``max_labels`` 미지정은 ``DEFAULT_MAX_LABELS``.

    Raises:
        SkillConfigError: dict 가 아닐 때, 모르는 키가 있을 때, ``unassigned`` 가 없을 때,
            ``selection`` 이 어휘 밖일 때, ``max_labels`` 가 1 이상 정수가 아닐 때.
    """
    policy = _require_mapping(raw, where="policy")
    _reject_unknown_keys(policy, _POLICY_KEYS, where="policy")

    # selection 은 생략 가능(기본 multi · spec §1 "multi(기본·2026-08-24 사용자 결정)").
    # 단 **값을 적었으면** 어휘 안이어야 한다 — ``"many"`` 같은 오기를 기본값으로 흡수하면
    # 사용자가 의도한 정책과 다르게 집행된다.
    selection_raw = policy.get("selection", SELECTION_MODES[0])
    if not isinstance(selection_raw, str) or selection_raw.strip() not in SELECTION_MODES:
        raise _fail(
            "policy.selection",
            f"{list(SELECTION_MODES)} 중 하나여야 한다(받은 값: {selection_raw!r})",
        )
    # 미부여 라벨명은 필수다 — 없으면 "어디에도 해당 안 됨"을 표현할 수단이 사라져 판정이 억지
    # 배정으로 흐른다(파일럿 발견 2: 미부여가 정직하게 작동하는 것이 커버리지 갭을 드러낸다).
    unassigned = _require_text(policy, "unassigned", where="policy")
    max_labels = _require_positive_int(
        policy, "max_labels", where="policy", default=DEFAULT_MAX_LABELS
    )
    return SkillPolicy(
        selection=selection_raw.strip(), unassigned=unassigned, max_labels=max_labels
    )


def _parse_labels(raw: Any, *, policy: SkillPolicy) -> tuple[SkillLabel, ...]:
    """``labels`` 목록을 파싱·검증한다(중복·상한·미부여 충돌까지).

    Args:
        raw: 설정의 ``labels`` 값(비어 있지 않은 list 여야 한다).
        policy: 이미 검증된 정책. ``max_labels`` 상한과 ``unassigned`` 이름 충돌 검사에 쓴다.

    Returns:
        ``SkillLabel`` 튜플 — **설정 순서 보존**(프롬프트 나열 순서가 이 순서다).

    Raises:
        SkillConfigError: list 가 아닐 때, 비었을 때, 항목이 dict 가 아닐 때, 필수 필드가
            없거나 비었을 때, 코드 문법 위반, 코드·이름 중복, 미부여 라벨명·예약 코드와 충돌,
            개수가 ``max_labels`` 초과일 때.
    """
    if not isinstance(raw, (list, tuple)):
        raise _fail("labels", f"라벨 배열(list)이어야 한다(받은 형: {type(raw).__name__})")
    if not raw:
        raise _fail("labels", "라벨이 하나도 없다 — 분류할 축이 없는 스킬은 등록할 수 없다")
    if len(raw) > policy.max_labels:
        raise _fail(
            "labels",
            f"라벨 수 상한 초과: {len(raw)}개 > max_labels={policy.max_labels} "
            "— 프롬프트 비대를 막는 가드다(상한을 올리려면 policy.max_labels 를 조정한다)",
        )

    labels: list[SkillLabel] = []
    seen_codes: dict[str, int] = {}
    seen_names: dict[str, int] = {}
    for index, item in enumerate(raw):
        where = f"labels[{index}]"
        entry = _require_mapping(item, where=where)
        _reject_unknown_keys(entry, _LABEL_KEYS, where=where)
        code = _require_code(_require_text(entry, "code", where=where), where=f"{where}.code")
        name = _require_text(entry, "name", where=where)
        # 정의문·경계는 **필수**다(spec §1 — 라벨명만 등록 불가). 파일럿 발견 1: 정의문 문구가
        # 곧 분류 기준이므로, 비워 두면 LLM 이 라벨명만 보고 자기 상식으로 채운다.
        definition = _require_text(entry, "definition", where=where)
        exclusion = _require_text(entry, "not", where=where)

        # 예약 코드 충돌 — 미부여 행의 label_code 로 쓰는 값이라 라벨이 가져가면 저장 시
        # "해당없음"과 그 라벨이 같은 행으로 뭉개진다(spec §2).
        if code == UNASSIGNED_LABEL_CODE:
            raise _fail(f"{where}.code", f"{UNASSIGNED_LABEL_CODE!r} 는 미부여 행 전용 예약 코드다")
        if code in seen_codes:
            raise _fail(f"{where}.code", f"코드 중복: {code!r} (labels[{seen_codes[code]}] 와 같다)")
        # 라벨명은 LLM 응답의 식별자다(spec §4 — 응답은 라벨명 배열). 겹치면 어느 라벨인지
        # 되돌릴 수 없어 저장 코드를 결정할 수 없다.
        if name in seen_names:
            raise _fail(f"{where}.name", f"라벨명 중복: {name!r} (labels[{seen_names[name]}] 와 같다)")
        if name == policy.unassigned:
            raise _fail(
                f"{where}.name",
                f"미부여 라벨명({policy.unassigned!r})과 같다 — 판정 결과가 미부여인지 이 라벨인지 구분할 수 없다",
            )
        seen_codes[code] = index
        seen_names[name] = index
        labels.append(SkillLabel(code=code, name=name, definition=definition, exclusion=exclusion))
    return tuple(labels)


def load_skill(raw: Mapping[str, Any]) -> ClassificationSkill:
    """분류 스킬 설정(dict) → 검증된 ``ClassificationSkill``(순수·fail-fast).

    입력은 등록 CLI 가 JSON 파일에서 읽어 온 dict 다(파일 I/O 는 여기서 하지 않는다 — 정본은 DB
    등록 행이고 JSON 은 입력 수단일 뿐 · spec §1). **입력 dict 를 변경하지 않는다** — 호출부가
    원본을 그대로 다시 쓸 수 있어야 한다(미리보기 후 등록 2단계 · spec §3).

    검사 순서에 뜻이 있다: 정책을 먼저 확정해야(``max_labels``·``unassigned``) 라벨 검사에서
    상한·이름 충돌을 판정할 수 있다.

    Args:
        raw: 설정 dict. 필수 키는 ``skill``(스킬 표시명)·``skill_code``(DB 자연키 — 소문자 스네이크)·
            ``version``(1 이상 정수)·``policy``·``labels``. 선택 키는 없다.
            모르는 키가 있으면 거부한다(오타 가드).

    Returns:
        검증된 불변 스킬 객체. 라벨 순서는 설정에 적힌 순서를 그대로 보존한다.

    Raises:
        SkillConfigError: 어느 검사든 실패하면 **첫 위반에서 즉시** 올린다(위치·사유 포함).
            여러 위반을 모아 보고하지 않는 이유: 설정은 사람이 한 파일을 고치는 작업이라
            "가장 먼저 막힌 곳"만 알려 주면 충분하고, 부분 통과 객체를 만들지 않는 편이 안전하다.
    """
    skill = _require_mapping(raw, where="(최상위)")
    _reject_unknown_keys(skill, _SKILL_KEYS, where="(최상위)")
    # 블록 키(policy·labels)는 값 파싱 전에 **존재부터** 확인한다 — 없는 키를 파싱 함수에 넘기면
    # "dict 가 아니다" 같은 엉뚱한 사유가 나와 고칠 곳을 못 찾는다.
    for block in ("policy", "labels"):
        if block not in skill:
            raise _fail(f"(최상위).{block}", "필수 항목이 없다")
    name = _require_text(skill, "skill", where="(최상위)")
    version = _require_positive_int(skill, "version", where="(최상위)")
    policy = _parse_policy(skill["policy"])
    labels = _parse_labels(skill["labels"], policy=policy)
    # 자연키는 필수다(구현확정 G1) — 없으면 어느 행으로 등록·개정할지 정할 수 없다.
    skill_code = _require_code(
        _require_text(skill, "skill_code", where="(최상위)"), where="(최상위).skill_code"
    )
    return ClassificationSkill(
        name=name, version=version, policy=policy, labels=labels, skill_code=skill_code
    )


__all__ = [
    "DEFAULT_MAX_LABELS",
    "MM_META_TYPE_SKILL_CODE",
    "NON_CLASSIFY_SKILL_CODES",
    "SELECTION_MODES",
    "UNASSIGNED_LABEL_CODE",
    "ClassificationSkill",
    "SkillConfigError",
    "SkillLabel",
    "SkillPolicy",
    "load_skill",
]
