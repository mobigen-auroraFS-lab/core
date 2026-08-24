"""083 T101 — 표기 정규화 키 단일 정본(``src/domain/text_norm.py``) 순수 단위 테스트.

**왜 이 함수가 따로 있나**: 같은 뜻인데 표기만 다른 문자열("전통음식"·"전통 음식")을 한 칸으로
묶어야 검색 태그 패싯의 건수 표시가 거짓이 되지 않는다(083 spec §"표기 정규화 없이는 건수 표시가
거짓이 된다" — dev 실측 `전통음식(10)`+`전통 음식(6)`=실제 16건). 083 태그와 084 멀티모달 메타가
**같은 규칙**을 써야 하므로 규칙 사본이 파이썬 안에 둘 생기지 않도록 이 모듈 하나만 둔다.

규칙 = NFKC 정규화 → 모든 공백 제거 → ``casefold``. 순서가 계약이다(NFKC 가 전각 공백 U+3000 을
보통 공백으로 바꿔 놓아야 공백 제거가 그것까지 지운다).

DB·LLM·OpenSearch 불필요한 순수 단위 테스트다.
"""

from __future__ import annotations

import unittest

from src.domain.text_norm import normalize_text_key


class TestNormalizeTextKey(unittest.TestCase):
    """정규화 키의 계약 — 병합·casefold·NFKC·멱등·빈 값."""

    def test_공백_유무만_다른_표기는_같은_키(self) -> None:
        # 083 실측 병합 사례 그대로: 태그 목록에 두 항목으로 뜨면 건수 표시가 거짓이 된다.
        self.assertEqual(normalize_text_key("전통음식"), normalize_text_key("전통 음식"))
        self.assertEqual(normalize_text_key("전통 음식"), "전통음식")

    def test_자연경관_병합_사례(self) -> None:
        # spec 병합 예(자연경관 + 자연 경관 = 24건).
        self.assertEqual(normalize_text_key("자연경관"), normalize_text_key("자연 경관"))

    def test_공백_종류를_가리지_않고_전부_제거(self) -> None:
        # 탭·개행·연속 공백·전각 공백(U+3000) 모두 제거된다 — NFKC 가 U+3000 을 보통 공백으로
        # 바꾼 뒤 공백 제거가 지우는 순서이기 때문이다.
        for raw in ("전통\t음식", "전통\n음식", "전통  음식", "전통　음식", " 전통 음식 "):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_text_key(raw), "전통음식")

    def test_영문_대소문자는_casefold_로_병합(self) -> None:
        self.assertEqual(normalize_text_key("Machine Learning"), "machinelearning")
        self.assertEqual(normalize_text_key("MACHINE learning"), normalize_text_key("machinelearning"))

    def test_casefold_는_lower_보다_강하다(self) -> None:
        # 독일어 ß 는 lower() 로는 그대로 남지만 casefold() 는 'ss' 로 접는다 — 규칙이
        # lower 로 바뀌면 이 테스트가 깨진다(계약 봉인).
        self.assertEqual(normalize_text_key("Straße"), "strasse")

    def test_전각_문자는_NFKC_로_반각화(self) -> None:
        # 전각 영문·숫자로 입력된 태그가 별개 항목으로 갈라지지 않게 한다.
        self.assertEqual(normalize_text_key("ＡＢＣ"), "abc")
        self.assertEqual(normalize_text_key("３Ｄ 프린팅"), "3d프린팅")

    def test_멱등성(self) -> None:
        # 색인 시점(asset_to_doc)과 필터 값 양쪽에서 같은 함수를 거치므로, 이미 정규화된 값이
        # 한 번 더 통과해도 값이 변하면 안 된다(두 번 적용된 키와 한 번 적용된 키가 갈라진다).
        for raw in ("전통 음식", "ＡＢＣ", "Straße", "  자연 경관  ", "김치"):
            with self.subTest(raw=raw):
                once = normalize_text_key(raw)
                self.assertEqual(normalize_text_key(once), once)

    def test_빈_문자열과_공백뿐인_입력은_빈_키(self) -> None:
        # 호출부(색인·집계)가 빈 키를 배제하는 판정 근거 — "" 로 수렴시켜 한 곳에서 걸러낸다.
        for raw in ("", " ", "\t\n", "　"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_text_key(raw), "")

    def test_다른_뜻의_값은_섞이지_않는다(self) -> None:
        # 병합이 과하지 않은지(공백만 무시하는 규칙이므로 다른 어휘는 그대로 갈라진다).
        self.assertNotEqual(normalize_text_key("전통음식"), normalize_text_key("전통의상"))


if __name__ == "__main__":
    unittest.main()
