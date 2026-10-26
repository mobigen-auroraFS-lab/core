"""포함 이름 잇기(``resolve_registered_contained``) 단위 테스트(순수 · DB 불필요).

왜 필요한가: 제목을 참고한 요약(spec 114)은 키워드 첫 항목에 제목 전체를 싣는다. 그러자 개체 판정이 제목 전체를
개체 하나로 골라(「작품:남구만 초상」 · 「장소:제주 삼양동 유적」) 등록 개체(「인물:남구만」 · 「장소:삼양동 유적」)와
이어지지 않는다 — 국가유산 해설 문서가 제 개체 묶음에서 빠진다.

무엇을 봉인하나
① 등록되지 않은 판정 이름 **안의** 낱말 구간이 등록 개체 이름과 같으면 그 등록 개체를 **더한다**(종류는 등록 개체의 것 ·
   원래 판정은 남긴다 — 미등록 후보로 보고된다).
② 긴 구간을 먼저 · 겹치지 않게 · 같은 이름이 여러 종류로 등록돼 있으면(동음이의) 잇지 않는다.
③ 일반 이름(여러 대상이 같이 쓰는 건물 · 유형명 — 「극락전」 「금동관음보살좌상」 「용추폭포」)은 잇지 않는다.
④ 정규화 키 3자 미만은 잇지 않는다 — 단 인물은 판정 이름의 **맨 앞 낱말**일 때 2자도 잇는다(「임장 초상」 → 임장 ·
   「돌고 돌아가는 길」 → 「길」 은 막는다).
⑤ 등록된 판정 이름은 쪼개지 않는다 · 근거 키워드는 원문 보존 · 이은 목록을 돌려준다.
"""

from __future__ import annotations

import unittest

from src.domain.text_norm import normalize_text_key
from src.mm_meta.persist import resolve_registered_contained
from src.mm_meta.rules import ExtractedEntity

_TYPES = ("사건", "인물", "작품", "장소", "조직")


def _ent(name: str, entity_type: str = "작품", keyword: str | None = None) -> ExtractedEntity:
    return ExtractedEntity(entity_type=entity_type, keyword=keyword or name, name=name)


def _index(*pairs: tuple[str, str]) -> dict[tuple[str, str], str]:
    return {(t, normalize_text_key(n)): n for t, n in pairs}


_IDX = _index(("인물", "남구만"), ("장소", "삼양동 유적"), ("장소", "황남대총"), ("인물", "임장"), ("인물", "길"), ("인물", "길동"),
              ("장소", "극락전"), ("작품", "금동관음보살좌상"), ("장소", "용추폭포"), ("장소", "백두산"), ("작품", "백두산"),
              ("작품", "여주 창리 삼층석탑"), ("장소", "보타사"), ("장소", "보타사 마애보살좌상"))


def _names(out):
    return [f"{e.entity_type}:{e.name}" for e in out]


class ResolveRegisteredContainedTest(unittest.TestCase):
    def test_제목_전체_판정에서_인물을_더한다(self) -> None:
        out, links = resolve_registered_contained([_ent("남구만 초상")], _IDX, _TYPES)
        self.assertEqual(["작품:남구만 초상", "인물:남구만"], _names(out))
        self.assertEqual(("작품", "남구만 초상", "인물", "남구만"), links[0])

    def test_지역명이_붙은_이름에서_등록_장소를_더한다(self) -> None:
        out, _ = resolve_registered_contained([_ent("제주 삼양동 유적", "장소")], _IDX, _TYPES)
        self.assertIn("장소:삼양동 유적", _names(out))

    def test_안쪽_구간도_찾는다(self) -> None:
        out, _ = resolve_registered_contained([_ent("황남대총 북분 금제 고배")], _IDX, _TYPES)
        self.assertIn("장소:황남대총", _names(out))

    def test_긴_구간을_먼저_겹치지_않게(self) -> None:
        out, links = resolve_registered_contained([_ent("서울 보타사 마애보살좌상")], _IDX, _TYPES)
        self.assertIn("장소:보타사 마애보살좌상", _names(out))
        self.assertNotIn("장소:보타사", _names(out))  # 더 긴 구간이 이미 그 낱말을 썼다
        self.assertEqual(1, len(links))

    def test_일반_이름은_잇지_않는다(self) -> None:
        for name in ("보성 대원사 극락전", "양평 용문사 금동관음보살좌상", "함양 심진동 용추폭포"):
            with self.subTest(name=name):
                out, links = resolve_registered_contained([_ent(name, "장소")], _IDX, _TYPES)
                self.assertEqual((), links)

    def test_재질_호칭_낱말은_일반_이름이다(self) -> None:
        # 「도기」가 인물로 등록돼 있어도 「도기 기마인물형 뿔잔」을 그 인물에 묶지 않는다.
        idx = _index(("인물", "도기"), ("인물", "항아"))
        for name in ("도기 기마인물형 뿔잔", "항아 당의"):
            with self.subTest(name=name):
                _, links = resolve_registered_contained([_ent(name)], idx, _TYPES)
                self.assertEqual((), links)

    def test_불상_유형명_패턴도_일반_이름이다(self) -> None:
        idx = _index(("작품", "목조아미타여래좌상"))
        out, links = resolve_registered_contained([_ent("안동 봉정사 목조아미타여래좌상")], idx, _TYPES)
        self.assertEqual((), links)

    def test_동음이의는_잇지_않는다(self) -> None:
        out, links = resolve_registered_contained([_ent("백두산 사진첩")], _IDX, _TYPES)
        self.assertEqual((), links)

    def test_두_글자_동음이의는_길이_조건보다_먼저_막는다(self) -> None:
        # 2자 이름이 인물 · 장소 둘로 등록돼 있으면, 장소가 길이 조건에서 빠져 인물만 남는 일이 없어야 한다.
        idx = _index(("인물", "임장"), ("장소", "임장"))
        _, links = resolve_registered_contained([_ent("임장 초상")], idx, _TYPES)
        self.assertEqual((), links)

    def test_두_글자_인물은_맨_앞_낱말일_때만(self) -> None:
        out, _ = resolve_registered_contained([_ent("임장 초상")], _IDX, _TYPES)
        self.assertIn("인물:임장", _names(out))
        out, links = resolve_registered_contained([_ent("돌아가는 길동")], _IDX, _TYPES)
        self.assertEqual((), links)  # 두 글자 인물이 맨 앞이 아니다
        out, links = resolve_registered_contained([_ent("돌고 돌아가는 길")], _IDX, _TYPES)
        self.assertEqual((), links)  # 한 글자는 어디서든 잇지 않는다

    def test_등록된_이름은_쪼개지_않는다(self) -> None:
        out, links = resolve_registered_contained([_ent("여주 창리 삼층석탑")], _IDX, _TYPES)
        self.assertEqual(["작품:여주 창리 삼층석탑"], _names(out))
        self.assertEqual((), links)

    def test_이미_있는_개체는_다시_더하지_않는다(self) -> None:
        out, links = resolve_registered_contained([_ent("남구만", "인물"), _ent("남구만 초상")], _IDX, _TYPES)
        self.assertEqual(["인물:남구만", "작품:남구만 초상"], _names(out))
        self.assertEqual((), links)

    def test_근거_키워드는_원문_보존(self) -> None:
        out, _ = resolve_registered_contained([_ent("남구만 초상", keyword="남구만 초상 보물")], _IDX, _TYPES)
        self.assertEqual("남구만 초상 보물", out[-1].keyword)

    def test_색인이_비면_그대로(self) -> None:
        ents = [_ent("남구만 초상")]
        out, links = resolve_registered_contained(ents, {}, _TYPES)
        self.assertEqual(_names(ents), _names(out))
        self.assertEqual((), links)


if __name__ == "__main__":
    unittest.main()
