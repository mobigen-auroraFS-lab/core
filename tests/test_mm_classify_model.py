"""085 T102 — 분류 스킬 **설정 객체** 파싱·검증 단위 테스트(``src/mm_classify/model.py``).

무엇을 검증하나: 사람이 손으로 쓴 설정 값(스킬)을 등록 전에 걸러내는 문지기다. 스킬은 코드가 아니라
**데이터**라서(085 plan §Global Constraints) 잘못 쓰면 조용히 이상한 분류가 나온다 — 그래서 파싱은
관대하지 않고 **fail-fast**(문제를 발견하면 그 자리에서 거부)여야 한다. 여기서는 정상 스킴 하나가
그대로 통과하는 것과, 결함 스킴 각각이 **어떤 이유로** 거부되는지를 못 박는다.

비유하면 서식 접수 창구다 — 칸이 비었으면(정의문 누락) 접수 거부, 같은 이름이 두 번 적혀 있으면
(라벨명 중복) 거부, 장수 제한을 넘겼으면(라벨 수 상한) 거부. 접수된 뒤에 되돌릴 수 없기 때문이다
(등록 = DB 정본 · spec §1).

DB·LLM·네트워크 불필요한 순수 단위 테스트다.
"""

from __future__ import annotations

import copy
import unittest
from dataclasses import FrozenInstanceError
from typing import Any

from src.mm_classify.model import (
    DEFAULT_MAX_LABELS,
    SELECTION_MODES,
    UNASSIGNED_LABEL_CODE,
    ClassificationSkill,
    SkillConfigError,
    load_skill,
)


def _scheme(**over: Any) -> dict[str, Any]:
    """spec §1 예시와 **같은 구조**의 정상 스킴(값은 더미)을 만든다.

    실 데이터·실 자산 문구를 코드 레포에 넣지 않기 위해 라벨명·정의문은 모두 더미다
    (구조만 spec §1 과 같다).

    Args:
        **over: 최상위 키를 덮어쓴다(``labels=[]`` 처럼 결함 스킴을 만들 때 쓴다).

    Returns:
        스킬 설정 dict.
    """
    base: dict[str, Any] = {
        "skill": "샘플 분류",
        # skill_code 는 **필수 키**다(구현확정 G1) — preview→apply 가 이 파일 하나로 재현돼야 하고,
        # 스킬명이 한국어라 코드를 자동 파생할 수도 없다.
        "skill_code": "sample_skill",
        "version": 1,
        "policy": {"selection": "multi", "unassigned": "해당없음", "max_labels": 30},
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
    base.update(over)
    return base


def _label(**over: Any) -> dict[str, Any]:
    """정상 라벨 하나(더미)를 만들고 지정 키만 덮어쓴다.

    Args:
        **over: 덮어쓸 라벨 키(``definition=None`` 등 결함 주입용).

    Returns:
        라벨 dict.
    """
    base: dict[str, Any] = {
        "code": "gamma",
        "name": "다라벨",
        "definition": "다에 해당하는 내용이 중심인 자산",
        "not": "가·나에 해당하는 내용",
    }
    base.update(over)
    return base


class TestLoadSkillHappyPath(unittest.TestCase):
    """정상 스킴은 그대로 통과하고, 값이 손실 없이 객체에 담긴다."""

    def test_spec_예시_구조가_통과한다(self) -> None:
        skill = load_skill(_scheme())
        self.assertIsInstance(skill, ClassificationSkill)
        self.assertEqual(skill.name, "샘플 분류")
        self.assertEqual(skill.version, 1)
        self.assertEqual(skill.policy.selection, "multi")
        self.assertEqual(skill.policy.unassigned, "해당없음")
        self.assertEqual(skill.policy.max_labels, 30)
        self.assertEqual(skill.label_codes, ("alpha", "beta"))
        self.assertEqual(skill.label_names, ("가라벨", "나라벨"))

    def test_라벨_순서는_설정_순서_그대로다(self) -> None:
        # 프롬프트에 나열되는 순서가 곧 이 순서다 — 알파벳 정렬 등으로 흔들면 문안이 바뀐다.
        scheme = _scheme(labels=[_label(code="zeta", name="차라벨"), _label(code="alpha", name="가라벨")])
        self.assertEqual(load_skill(scheme).label_codes, ("zeta", "alpha"))

    def test_라벨_필드가_손실없이_담긴다(self) -> None:
        first = load_skill(_scheme()).labels[0]
        self.assertEqual(first.code, "alpha")
        self.assertEqual(first.name, "가라벨")
        self.assertEqual(first.definition, "가에 해당하는 내용이 중심인 자산")
        # JSON 키 ``not`` 은 파이썬 예약어라 필드명이 ``exclusion``(경계)이다.
        self.assertEqual(first.exclusion, "나에 해당하는 내용")

    def test_앞뒤_공백은_잘라서_담는다(self) -> None:
        scheme = _scheme(skill="  샘플 분류  ", labels=[_label(name="  다라벨 ")])
        skill = load_skill(scheme)
        self.assertEqual(skill.name, "샘플 분류")
        self.assertEqual(skill.labels[0].name, "다라벨")

    def test_어휘와_코드맵을_제공한다(self) -> None:
        # judge 가 "어휘 안/밖"을 판정할 때 쓰는 계약 — 미부여 라벨명도 어휘에 포함된다.
        skill = load_skill(_scheme())
        self.assertEqual(skill.vocabulary, frozenset({"가라벨", "나라벨", "해당없음"}))
        self.assertEqual(skill.code_by_name["나라벨"], "beta")
        self.assertEqual(skill.code_by_name["해당없음"], UNASSIGNED_LABEL_CODE)

    def test_selection_판별_속성(self) -> None:
        self.assertTrue(load_skill(_scheme()).is_multi)
        single = _scheme(policy={"selection": "single", "unassigned": "해당없음"})
        self.assertFalse(load_skill(single).is_multi)

    def test_입력_dict를_변경하지_않는다(self) -> None:
        # 순수 함수 — 호출부(등록 CLI)가 원본 JSON 을 그대로 다시 쓸 수 있어야 한다.
        scheme = _scheme()
        before = copy.deepcopy(scheme)
        load_skill(scheme)
        self.assertEqual(scheme, before)

    def test_결과는_불변객체다(self) -> None:
        # 등록 후 누군가 정책을 슬쩍 바꾸면 판정과 저장이 어긋난다(frozen 으로 봉인).
        skill = load_skill(_scheme())
        with self.assertRaises(FrozenInstanceError):
            skill.version = 2  # type: ignore[misc]

    def test_같은_입력_두번이면_같은_객체값(self) -> None:
        # 결정성(헌법 3조) — 같은 설정은 언제나 같은 스킬이다.
        self.assertEqual(load_skill(_scheme()), load_skill(_scheme()))


class TestSkillCodeRequired(unittest.TestCase):
    """``skill_code``(자연키·DB 행 식별)는 **필수 키**다(구현확정 G1 · 2026-08-24).

    왜 필수로 올렸나: 등록은 ``--preview`` 로 확인한 그 파일이 ``--apply`` 로 그대로 등록돼야
    하는 2단계 절차다(spec §3). 코드를 CLI 인자로 빼면 파일만으로 재현되지 않고(같은 파일이 다른
    스킬로 등록될 수 있다), 스킬명이 한국어라 코드를 기계적으로 파생할 수도 없다.
    """

    def test_있으면_담는다(self) -> None:
        self.assertEqual(load_skill(_scheme(skill_code="food_content")).skill_code, "food_content")

    def test_없으면_거부한다(self) -> None:
        scheme = _scheme()
        del scheme["skill_code"]
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_문법_위반은_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(skill_code="Food Content"))


class TestPolicyDefaults(unittest.TestCase):
    """정책 기본값 — 생략 가능한 것과 필수인 것을 구분한다."""

    def test_max_labels_생략시_기본_30(self) -> None:
        skill = load_skill(_scheme(policy={"selection": "multi", "unassigned": "해당없음"}))
        self.assertEqual(skill.policy.max_labels, DEFAULT_MAX_LABELS)
        self.assertEqual(DEFAULT_MAX_LABELS, 30)

    def test_selection_생략시_multi(self) -> None:
        # spec §1 — multi 가 기본(2026-08-24 사용자 결정).
        skill = load_skill(_scheme(policy={"unassigned": "해당없음"}))
        self.assertEqual(skill.policy.selection, "multi")

    def test_selection_어휘는_두개뿐(self) -> None:
        self.assertEqual(set(SELECTION_MODES), {"multi", "single"})


class TestRejectMissingOrBadTopLevel(unittest.TestCase):
    """최상위 필수 필드 — 없거나 모양이 틀리면 거부."""

    def test_dict가_아니면_거부(self) -> None:
        for bad in ([], "x", None, 3):
            with self.subTest(bad=bad), self.assertRaises(SkillConfigError):
                load_skill(bad)  # type: ignore[arg-type]

    def test_스킬명_누락_거부(self) -> None:
        scheme = _scheme()
        del scheme["skill"]
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_스킬명_빈문자_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(skill="   "))

    def test_version_누락_거부(self) -> None:
        scheme = _scheme()
        del scheme["version"]
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_version_0이하_거부(self) -> None:
        for bad in (0, -1):
            with self.subTest(bad=bad), self.assertRaises(SkillConfigError):
                load_skill(_scheme(version=bad))

    def test_version_정수가_아니면_거부(self) -> None:
        # "1"(문자열)·1.0(실수)·True(bool) 모두 거부 — DB 버전 비교(백필 선별)가 정수 계약이다.
        for bad in ("1", 1.0, True, None):
            with self.subTest(bad=bad), self.assertRaises(SkillConfigError):
                load_skill(_scheme(version=bad))

    def test_알수없는_최상위_키_거부(self) -> None:
        # 오타(``labelss``)를 조용히 무시하면 라벨 없는 스킬이 등록된다.
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labelss=[]))


class TestRejectBadPolicy(unittest.TestCase):
    """정책 블록 검증 — 미부여 라벨 강제가 핵심(spec §1)."""

    def test_policy_누락_거부(self) -> None:
        scheme = _scheme()
        del scheme["policy"]
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_policy_비dict_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(policy=["multi"]))

    def test_unassigned_누락_거부(self) -> None:
        # 미부여 라벨이 없으면 "어디에도 해당 안 됨"을 표현할 수단이 사라진다(억지 배정 유발).
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(policy={"selection": "multi"}))

    def test_unassigned_빈문자_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(policy={"selection": "multi", "unassigned": "  "}))

    def test_selection_어휘밖_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(policy={"selection": "many", "unassigned": "해당없음"}))

    def test_max_labels_0이하_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(policy={"selection": "multi", "unassigned": "해당없음", "max_labels": 0}))

    def test_max_labels_정수아님_거부(self) -> None:
        for bad in ("30", 30.5, True):
            with self.subTest(bad=bad), self.assertRaises(SkillConfigError):
                load_skill(
                    _scheme(policy={"selection": "multi", "unassigned": "해당없음", "max_labels": bad})
                )

    def test_알수없는_정책키_거부(self) -> None:
        # ``max_label``(오타)을 무시하면 상한이 조용히 기본값으로 돌아간다.
        with self.assertRaises(SkillConfigError):
            load_skill(
                _scheme(policy={"selection": "multi", "unassigned": "해당없음", "max_label": 3})
            )


class TestRejectBadLabels(unittest.TestCase):
    """라벨 목록 검증 — 정의문·경계 필수(발견 1: "정의문이 곧 분류")."""

    def test_labels_누락_거부(self) -> None:
        scheme = _scheme()
        del scheme["labels"]
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_labels_빈목록_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labels=[]))

    def test_labels_비리스트_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labels={"code": "alpha"}))

    def test_라벨이_dict가_아니면_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labels=["가라벨"]))

    def test_필수필드_누락_거부(self) -> None:
        for key in ("code", "name", "definition", "not"):
            scheme = _scheme()
            label = _label()
            del label[key]
            scheme["labels"] = [label]
            with self.subTest(key=key), self.assertRaises(SkillConfigError):
                load_skill(scheme)

    def test_필수필드_빈문자_거부(self) -> None:
        # 라벨명만 등록하고 정의문을 비우는 것이 가장 흔한 실수다(spec §1 — 라벨명만 등록 불가).
        for key in ("code", "name", "definition", "not"):
            with self.subTest(key=key), self.assertRaises(SkillConfigError):
                load_skill(_scheme(labels=[_label(**{key: "   "})]))

    def test_필수필드_비문자_거부(self) -> None:
        for key in ("code", "name", "definition", "not"):
            with self.subTest(key=key), self.assertRaises(SkillConfigError):
                load_skill(_scheme(labels=[_label(**{key: 3})]))

    def test_code_문법위반_거부(self) -> None:
        # 규칙: 소문자 스네이크 — ``[a-z][a-z0-9_]*``(관계 kind_code 와 같은 문자 집합).
        for bad in ("Alpha", "1alpha", "al pha", "al-pha", "_alpha", "알파", "alpha!"):
            with self.subTest(bad=bad), self.assertRaises(SkillConfigError):
                load_skill(_scheme(labels=[_label(code=bad)]))

    def test_code_허용예시는_통과(self) -> None:
        for ok in ("a", "alpha", "alpha_1", "a1_b2"):
            with self.subTest(ok=ok):
                self.assertEqual(load_skill(_scheme(labels=[_label(code=ok)])).label_codes, (ok,))

    def test_code_길이상한_초과_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labels=[_label(code="a" * 101)]))

    def test_code_중복_거부(self) -> None:
        scheme = _scheme(labels=[_label(code="alpha", name="가라벨"), _label(code="alpha", name="나라벨")])
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_name_중복_거부(self) -> None:
        # 라벨명은 LLM 응답의 식별자다 — 겹치면 어느 라벨인지 되돌릴 수 없다.
        scheme = _scheme(labels=[_label(code="alpha", name="같은이름"), _label(code="beta", name="같은이름")])
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_공백차이만_있는_name_중복_거부(self) -> None:
        # strip 후 같은 이름이면 같은 이름이다(공백으로 중복 검사를 우회하지 못한다).
        scheme = _scheme(labels=[_label(code="alpha", name="가라벨"), _label(code="beta", name=" 가라벨 ")])
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_미부여_라벨명과_충돌_거부(self) -> None:
        # 라벨명이 ``해당없음`` 이면 판정 결과가 "미부여"인지 "그 라벨"인지 구분 불가.
        scheme = _scheme(labels=[_label(name="해당없음")])
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_예약_코드와_충돌_거부(self) -> None:
        # 미부여 행의 ``label_code`` 로 예약된 코드다(spec §2 — 해당없음 단독 1행).
        scheme = _scheme(labels=[_label(code=UNASSIGNED_LABEL_CODE)])
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_라벨_수_상한_초과_거부(self) -> None:
        # 프롬프트 비대 가드(spec §1 max_labels).
        labels = [_label(code=f"c{i}", name=f"라벨{i}") for i in range(4)]
        scheme = _scheme(policy={"selection": "multi", "unassigned": "해당없음", "max_labels": 3}, labels=labels)
        with self.assertRaises(SkillConfigError):
            load_skill(scheme)

    def test_라벨_수_상한_경계는_통과(self) -> None:
        labels = [_label(code=f"c{i}", name=f"라벨{i}") for i in range(3)]
        scheme = _scheme(policy={"selection": "multi", "unassigned": "해당없음", "max_labels": 3}, labels=labels)
        self.assertEqual(len(load_skill(scheme).labels), 3)

    def test_알수없는_라벨키_거부(self) -> None:
        with self.assertRaises(SkillConfigError):
            load_skill(_scheme(labels=[_label(definitions="오타 키")]))


class TestErrorDiagnostics(unittest.TestCase):
    """오류 메시지는 **어디가 문제인지** 가리켜야 한다(손으로 쓴 설정을 고치는 사람이 읽는다)."""

    def test_ValueError_하위형이다(self) -> None:
        # 호출부(등록 CLI)가 ValueError 로 잡아도 동작하도록(ExtMetaValidationError 선례).
        self.assertTrue(issubclass(SkillConfigError, ValueError))

    def test_문제_위치를_메시지에_담는다(self) -> None:
        scheme = _scheme(labels=[_label(), _label(code="delta", name="라라벨", definition="")])
        with self.assertRaises(SkillConfigError) as ctx:
            load_skill(scheme)
        message = str(ctx.exception)
        self.assertIn("labels[1]", message)
        self.assertIn("definition", message)

    def test_정책_문제도_위치를_담는다(self) -> None:
        with self.assertRaises(SkillConfigError) as ctx:
            load_skill(_scheme(policy={"selection": "many", "unassigned": "해당없음"}))
        self.assertIn("selection", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
