"""``src.mm_meta`` 패키지 공개 API(재수출) 단위 테스트. DB·LLM 불필요.

무엇을 막나: ``__all__`` 에 이름만 적고 import 를 빼먹거나(반대도) 모듈을 옮긴 뒤 목록을 안 고치는
드리프트다. 이 패키지는 지연 export 가 아니라 **즉시 import** 라서 목록과 실제가 갈리면
``from src.mm_meta import *`` 나 문서화된 경로가 조용히 깨진다(속성 접근 시점에야 드러난다).

네 층(``judge``·``rules``·``persist``·``describe``)이 한 이름으로 나가는지도 함께 본다 —
소비 레포(파이프 배치)가 이 패키지를 창구로 쓴다.
"""

from __future__ import annotations

import unittest

import src.mm_meta as mm_meta


class TestPackageExports(unittest.TestCase):
    """공개 목록과 실제 속성이 1:1 인가."""

    def test_all_names_resolve(self) -> None:
        # __all__ 의 모든 이름이 실제 속성에 닿는다(잔재·오타 조기 검출).
        for name in mm_meta.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(mm_meta, name))

    def test_all_is_ordered_and_unique(self) -> None:
        """중복 없음 + 이 레포의 ``__all__`` 관례 순서(상수 → 클래스 → 함수, 각 그룹은 이름 순).

        ruff 설정에 ``RUF022``(정렬 검사)가 없어 게이트가 잡아 주지 않는다 — 목록이 커질 때 같은
        이름이 두 번 들어오거나 그룹이 섞이는 것을 이 테스트가 막는다(다른 패키지 목록도 이 순서다).
        """
        def key(name: str) -> tuple[int, str]:
            # 0=상수(SCREAMING_CASE) · 1=클래스(CamelCase) · 2=함수(snake_case).
            return (0 if name.isupper() else 1 if name[0].isupper() else 2, name)

        self.assertEqual(len(mm_meta.__all__), len(set(mm_meta.__all__)))
        self.assertEqual(list(mm_meta.__all__), sorted(mm_meta.__all__, key=key))

    def test_설명_생성_API가_재수출된다(self) -> None:
        # T015 — 배치(파이프)가 설명 생성을 이 창구로 부른다.
        for name in ("DESC_PROMPT_VERSION", "DescribeFailure", "MetaDescription",
                     "build_description_prompt", "describe_meta", "interpret_description"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)
        self.assertIs(mm_meta.describe_meta, __import__(
            "src.mm_meta.describe", fromlist=["describe_meta"]).describe_meta)

    def test_설명_영속_API가_재수출된다(self) -> None:
        for name in ("fetch_meta_description_targets", "fetch_meta_members",
                     "upsert_meta_description"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)

    def test_공식_표기_색인_API가_재수출된다(self) -> None:
        # T017 — 배치가 색인을 **배치 시작에 한 번** 이 창구로 만든다(자산 루프 안에서 만들면 발산).
        for name in ("DESC_REGEN_MIN_DELTA_RATIO", "build_official_name_index",
                     "fetch_official_name_index"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)

    def test_접미_병합_문턱_상수가_재수출된다(self) -> None:
        # T018 — 병합 대상 타입·몸통 최소 길이는 **값이 계약**이다(색인이 무엇을 담는지 정한다).
        # 소비 레포(파이프 배치의 후보 리포트)가 "왜 이 표기는 병합되지 않았나"를 설명할 때 읽는다.
        for name in ("MIN_BASE_LENGTH", "SUFFIX_MERGE_TYPES"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)
        self.assertEqual(mm_meta.SUFFIX_MERGE_TYPES, frozenset({"장소"}))
        self.assertEqual(mm_meta.MIN_BASE_LENGTH, 2)

    def test_타입_어휘_API가_재수출된다(self) -> None:
        # F05 — 배치가 **배치 시작에 한 번** 어휘를 읽어 판정 프롬프트에 실어야 한다(spec §10).
        # 창구가 없으면 그 배선이 내부 모듈 경로로 새고, 폴백·검증 규칙이 우회된다.
        for name in ("ENTITY_TYPE_DEFS", "PROMPT_VERSION_WITHOUT_TYPE_DEFS", "EntityTypeDef",
                     "fetch_meta_type_vocab", "prompt_version_for"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)
        self.assertEqual(tuple(d.name for d in mm_meta.ENTITY_TYPE_DEFS),
                         mm_meta.ENTITY_TYPE_ORDER)

    def test_옛_표기_힌트_창구는_남아_있지_않다(self) -> None:
        # 하위호환 껍데기를 남기지 않는다(T017) — 남으면 새 코드가 옛 계약(타입 없는 표기 대조 ·
        # 자산마다 전체 재조립)을 다시 쓴다. 호출부는 코어 안뿐이라 전수 갱신했다.
        self.assertNotIn("fetch_known_entity_names", mm_meta.__all__)
        self.assertFalse(hasattr(mm_meta, "fetch_known_entity_names"))

    def test_판정_영속_규칙_API가_그대로_있다(self) -> None:
        # 회귀 가드 — 설명 추가가 기존 창구를 밀어내지 않았는지 본다(헌법 8조).
        for name in ("judge_asset_entities", "apply_rules", "upsert_entity_edges",
                     "register_mm_meta", "PROMPT_VERSION", "RULE_VERSION"):
            with self.subTest(name=name):
                self.assertIn(name, mm_meta.__all__)


if __name__ == "__main__":
    unittest.main()
