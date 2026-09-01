"""089 T004 — 멀티모달 메타 **개체 검색**(질의 토큰 분리) 단위 테스트(``src/mm_meta/entity_search.py``).

무엇을 검증하나: 검색어를 **공백으로 쪼개 토큰마다 찾고, 맞은 토큰 수로 줄 세우는** 규칙이
설계대로 도는지 본다. DB·LLM·서버가 필요 없는 순수 함수라 mock 없이 시험한다.

왜 이 규칙인가(착수 전 실측 2026-08-31 · `fixtures/mm_meta_search/baseline_20260831.json`):
현행은 검색어를 정규화한 뒤 **통짜로** 찾는다 — `"여자 솔로 가수"` 라는 글자가 이름·근거 키워드·
설명문 어딘가에 연속으로 있어야 걸린다. 그럴 리가 없어 다어절 질의 30개 중 **29개가 0건**이었다
(재현율 0%). 토큰으로 쪼개면 `가수` 하나로 걸린다(예측 43.3% · spec 089 §2).

이 파일이 봉인하는 일곱 가지(tasks 089 T004):
    ① **한 토큰 동치** — 공백 없는 질의는 현행과 **같은 개체**를 고른다. 이름 검색 재현율
       100%(B2)가 구조적으로 지켜지는 근거다. 현행 로직 사본(``_legacy_narrow``)과 맞대 본다.
    ② **다어절 OR** — 한 토큰만 맞아도 남는다(AND 면 0건이 그대로 재현된다).
    ③ **정렬** — 맞은 토큰 수 → 묶음 크기 → 이름. 동점을 **두 단계 다** 시험한다(결정성 B4).
    ④ **빈 질의** — ``None``·``""``·공백뿐인 질의는 **전체**(현행 계약). 기호만 든 질의는
       정규화가 기호를 지우지 않으므로 **토큰이 되고**, 따라서 걸리는 개체가 없으면 0건이다.
    ⑤ **이유 문자열** — 이름으로 걸렸으면 ``None``(현행 계약), 그 외는 **맞은 토큰**을 싣는다.
    ⑥ **공백 포함 이름 회귀** — 노출 개체 82개 중 10개(12%)가 이름에 공백을 갖는다. 2토큰으로
       쪼개져도 두 토큰을 다 맞춰 **정답이 1위**여야 한다(실측 이름 3건을 케이스로 고정).
    ⑦ **한 글자 토큰** — 최소 길이 필터를 **두지 않는다**. 근거는 구현 모듈 주석에 실측치와 함께
       남겼다(요지: 필터를 켜면 다어절 재현율 43.3%→36.7% 로 떨어지고, `강`·`산` 같은 한 글자
       질의가 토큰 0개가 되어 ④의 계약에 따라 **전체**가 나온다 — 검색이 고장 난 것처럼 보인다).

⚠️ 픽스처는 실 자산 데이터가 아니다 — 실측 코퍼스의 **모양**(이름·근거 키워드 1~2개·40자 안팎의
설명문·묶음 크기)만 더미 값으로 재현한다. 개체 이름은 spec·tasks 에 적힌 공개 지명·프로그램명이다.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.domain.text_norm import normalize_text_key
from src.mm_meta.entity_search import (
    REASON_SEMANTIC,
    entity_refine_fields,
    fuse_entity_results,
    gate_semantic_hits,
    match_entity,
    narrow_entities,
    split_query,
)


def _item(
    name: str,
    keywords: list[str],
    description: str,
    confirmed_count: int,
) -> dict[str, Any]:
    """목록 행 하나(``/mm-meta`` 응답 모양)를 짧게 만드는 도우미.

    Args:
        name: 개체 표준표기(화면 카드 제목).
        keywords: 근거 키워드 — 이 개체로 묶인 이유가 된 원문 낱말들.
        description: 개체 설명문(084 생성 · 실측 평균 42자).
        confirmed_count: 묶음 크기 = 확인된 자료 수. 정렬 2순위다.

    Returns:
        목록 행 dict.
    """
    return {
        "entity_type": "장소",
        "entity_uid": name,
        "name": name,
        "keywords": keywords,
        "description": description,
        "confirmed_count": confirmed_count,
    }


# 실측 코퍼스(노출 개체 82개)의 모양을 본뜬 픽스처. 입력 순서는 현행 SQL 순서(묶음 크기 내림차순)다 —
# ④에서 "전체를 돌려줄 때 순서가 보존되는가"를 이 순서로 본다.
_ITEMS: tuple[dict[str, Any], ...] = (
    _item("아이유", ["아이유"], "가수 아이유의 앨범 정보와 무대 퍼포먼스를 담은 영상과 이미지.", 14),
    _item("제주도", ["제주도", "제주 해녀"], "제주도의 자연경관과 전통문화, 관광 정보.", 14),
    _item("올림픽", ["올림픽"], "올림픽과 월드컵 등 국제 대회의 역사와 종목 소개.", 10),
    _item("경기도", ["경기도"], "수원과 인접 도시의 도심 풍경과 교통.", 8),
    _item("강원도", ["강원도"], "동해안과 산간 지역의 사계절 풍경.", 6),
    _item("나이아가라 폭포", ["나이아가라 폭포"], "폭포의 지형적 특징과 경관, 관광 정보.", 6),
    _item("한강", ["한강"], "서울을 가로지르는 강의 사계절 풍경과 시민 여가.", 5),
    _item("나일강", ["나일강"], "이집트를 흐르는 긴 강과 주변 유적.", 4),
    _item("FIFA 월드컵", ["FIFA 월드컵", "월드컵"], "국제 축구 대회의 운영 체계와 대표팀 성과.", 4),
    _item("장범준", ["장범준"], "가수 장범준의 공연 실황과 스케치북 무대 인터뷰.", 3),
    _item("수원 화성", ["수원 화성", "수원화성"], "수원 화성의 역사적 가치와 건축 특징.", 3),
    _item("유희열의 스케치북", ["유희열의 스케치북"], "음악 방송 무대의 공연 영상과 이미지.", 3),
    _item("빅토리아 폭포", ["빅토리아 폭포"], "폭포의 지리적 특성과 생태계, 웅장한 경관.", 3),
)


def _names(rows: list[dict[str, Any]]) -> list[str]:
    """결과 행에서 이름만 순서대로 뽑는다(단언을 읽기 쉽게).

    Args:
        rows: ``narrow_entities`` 결과.

    Returns:
        이름 목록(결과 순서 그대로).
    """
    return [r["name"] for r in rows]


# ── 현행(교체 대상) 로직 사본 ────────────────────────────────────────────────────
# 백엔드 데모 라우트 ``routes_mm_meta_demo.py`` 의 ``_narrow``·``_match_reason`` 을 2026-08-31
# 시점 그대로 옮긴 것이다. **비교 기준**으로만 쓴다 — 한 토큰 질의에서 새 구현이 같은 개체를
# 고르는지(①)를 사람 눈이 아니라 코드로 맞대기 위해서다. 라우트가 교체돼도 이 사본은 남는다.
def _legacy_reason(item: dict[str, Any], needle: str) -> str | None:
    """현행 ``_match_reason`` 사본 — 이름으로 걸렸으면 ``None``.

    Args:
        item: 목록 행.
        needle: 정규화된 검색어(통짜).

    Returns:
        ``"근거 키워드 일치"``·``"설명 일치"`` 또는 ``None``.
    """
    if normalize_text_key(item.get("name") or "").find(needle) >= 0:
        return None
    for kw in item.get("keywords") or []:
        if normalize_text_key(str(kw)).find(needle) >= 0:
            return "근거 키워드 일치"
    if normalize_text_key(item.get("description") or "").find(needle) >= 0:
        return "설명 일치"
    return None


def _legacy_narrow(items: tuple[dict[str, Any], ...], q: str | None) -> list[dict[str, Any]]:
    """현행 ``_narrow`` 사본 — 정규화 후 **통짜 부분 문자열** 포함.

    Args:
        items: 목록 행들.
        q: 검색어(``None``·공백이면 좁히지 않는다).

    Returns:
        좁혀진 목록(각 행에 ``match_reason``).
    """
    needle = normalize_text_key(q or "")
    if not needle:
        return [{**it, "match_reason": None} for it in items]
    out: list[dict[str, Any]] = []
    for it in items:
        hit_name = needle in normalize_text_key(it.get("name") or "")
        hit_kw = any(needle in normalize_text_key(str(k)) for k in (it.get("keywords") or []))
        hit_desc = needle in normalize_text_key(it.get("description") or "")
        if hit_name or hit_kw or hit_desc:
            out.append({**it, "match_reason": _legacy_reason(it, needle)})
    return out


class TestSplitQuery(unittest.TestCase):
    """T001 — 질의를 토큰으로 쪼갠다(정규화는 코어 정본 ``normalize_text_key`` 하나만)."""

    def test_공백으로_쪼개고_토큰마다_정규화한다(self) -> None:
        self.assertEqual(split_query("여자 솔로 가수"), ("여자", "솔로", "가수"))

    def test_정규화_정본을_그대로_쓴다(self) -> None:
        """대소문자·전각·연속 공백 처리를 이 모듈이 따로 정의하지 않는다는 봉인.

        규칙이 두 벌이 되면 색인 키와 필터 키가 갈라진다(083 plan §Global Constraints).
        여기서 기대값은 ``normalize_text_key`` 의 규칙(NFKC → 공백 제거 → casefold)이다.
        """
        self.assertEqual(split_query("FIFA 월드컵"), ("fifa", "월드컵"))
        self.assertEqual(split_query("  아이유   앨범  "), ("아이유", "앨범"))
        # 전각 문자는 NFKC 로 반각이 된다(정본 규칙 1번).
        self.assertEqual(split_query("ＮＡＳＡ"), ("nasa",))

    def test_빈_질의는_토큰이_0개다(self) -> None:
        for q in (None, "", "   ", "\t\n "):
            with self.subTest(q=q):
                self.assertEqual(split_query(q), ())

    def test_한_글자_토큰을_버리지_않는다(self) -> None:
        """⑦ — 최소 길이 필터 없음(결정 근거는 구현 모듈 주석)."""
        self.assertEqual(split_query("아프리카 큰 강"), ("아프리카", "큰", "강"))
        self.assertEqual(split_query("강"), ("강",))


class TestMatchEntity(unittest.TestCase):
    """T002 — 개체 하나가 토큰 몇 개를 맞췄나 + 걸린 이유(⑤)."""

    def _by_name(self, name: str) -> dict[str, Any]:
        """픽스처에서 이름으로 행 하나를 꺼낸다.

        Args:
            name: 개체 이름.

        Returns:
            목록 행.
        """
        return next(it for it in _ITEMS if it["name"] == name)

    def test_이름_근거키워드_설명문_어디든_맞으면_센다(self) -> None:
        item = self._by_name("제주도")
        self.assertEqual(match_entity(item, ("제주도",))[0], 1)      # 이름
        self.assertEqual(match_entity(item, ("해녀",))[0], 1)        # 근거 키워드
        self.assertEqual(match_entity(item, ("자연경관",))[0], 1)     # 설명문
        self.assertEqual(match_entity(item, ("해녀", "자연경관"))[0], 2)

    def test_맞은_토큰이_없으면_0이고_이유도_없다(self) -> None:
        self.assertEqual(match_entity(self._by_name("한강"), ("도자기",)), (0, None))
        self.assertEqual(match_entity(self._by_name("한강"), ()), (0, None))

    def test_이름으로_걸리면_이유는_None이다(self) -> None:
        """⑤ 현행 계약 — 이름은 화면에 이미 보이므로 설명이 불필요하다."""
        self.assertEqual(match_entity(self._by_name("아이유"), ("아이유",)), (1, None))
        # 이름 + 다른 축이 함께 걸려도 이름이 우선이다.
        count, reason = match_entity(self._by_name("제주도"), ("제주도", "해녀"))
        self.assertEqual(count, 2)
        self.assertIsNone(reason)

    def test_이유에_맞은_토큰이_실린다(self) -> None:
        """⑤ — 여러 토큰 중 무엇으로 걸렸는지 보이지 않으면 OR 결과를 믿을 수 없다."""
        self.assertEqual(
            match_entity(self._by_name("제주도"), ("여자", "해녀")),
            (1, "근거 키워드 일치: 해녀"),
        )
        self.assertEqual(
            match_entity(self._by_name("아이유"), ("여자", "솔로", "가수")),
            (1, "설명 일치: 가수"),
        )

    def test_이유는_근거키워드가_설명문보다_앞선다(self) -> None:
        """⑤ 우선순위 — 이름 > 근거 키워드 > 설명문(현행 ``_match_reason`` 과 같은 순서)."""
        count, reason = match_entity(self._by_name("제주도"), ("자연경관", "해녀"))
        self.assertEqual(count, 2)
        self.assertEqual(reason, "근거 키워드 일치: 해녀")

    def test_같은_축에서는_질의_순서가_앞선_토큰을_쓴다(self) -> None:
        """결정성 — 같은 축에 여러 토큰이 걸리면 **질의에 먼저 나온** 토큰을 싣는다."""
        count, reason = match_entity(self._by_name("제주도"), ("관광", "자연경관"))
        self.assertEqual(count, 2)
        self.assertEqual(reason, "설명 일치: 관광")


class TestNarrowEntitiesEmptyQuery(unittest.TestCase):
    """④ 빈 질의 — 전체와 0건을 가른다."""

    def test_질의가_없으면_전체를_입력_순서대로_준다(self) -> None:
        for q in (None, "", "   ", "\t\n "):
            with self.subTest(q=q):
                rows = narrow_entities(list(_ITEMS), q)
                self.assertEqual(_names(rows), [it["name"] for it in _ITEMS])
                self.assertTrue(all(r["match_reason"] is None for r in rows))

    def test_기호만_든_질의는_토큰이_되어_0건이_된다(self) -> None:
        """🔴 기호는 정규화가 지우지 않는다(규칙은 공백 제거·casefold 뿐) → 토큰 1개.

        그래서 걸리는 개체가 없으면 **0건**이고 전체가 아니다. 현행과 같은 동작이며
        spec §5 "결과 0건과 전체를 가른다"가 요구하는 바다.
        """
        rows = narrow_entities(list(_ITEMS), "!!!")
        self.assertEqual(rows, [])
        self.assertEqual(_names(rows), _names(_legacy_narrow(_ITEMS, "!!!")))

    def test_입력을_건드리지_않는다(self) -> None:
        """순수 함수 봉인 — 원본 행에 ``match_reason`` 이 새로 생기면 안 된다."""
        source = [dict(it) for it in _ITEMS]
        narrow_entities(source, "아이유")
        narrow_entities(source, None)
        self.assertTrue(all("match_reason" not in it for it in source))


class TestNarrowEntitiesSingleToken(unittest.TestCase):
    """① 한 토큰 동치 — 공백 없는 질의는 현행과 같은 개체를 고른다(B2 의 실질)."""

    # 이름 질의·개념 낱말 질의·한 글자 질의를 섞는다. 기준선 질의셋에서 뽑은 형태다.
    QUERIES = ("아이유", "제주도", "한강", "폭포", "가수", "월드컵", "강", "도자기")

    def test_현행과_같은_개체를_고른다(self) -> None:
        for q in self.QUERIES:
            with self.subTest(q=q):
                new = narrow_entities(list(_ITEMS), q)
                legacy = _legacy_narrow(_ITEMS, q)
                # 동치는 "어떤 개체가 걸리나" 기준이다 — 줄 세우는 순서는 새 규칙이 정한다(③).
                self.assertEqual(set(_names(new)), set(_names(legacy)))

    def test_현행과_같은_이유_계층을_준다(self) -> None:
        """이름으로 걸린 것은 양쪽 다 ``None``, 그 밖은 현행 문구로 시작한다(토큰만 덧붙는다)."""
        for q in self.QUERIES:
            legacy_by_name = {r["name"]: r["match_reason"] for r in _legacy_narrow(_ITEMS, q)}
            for row in narrow_entities(list(_ITEMS), q):
                with self.subTest(q=q, name=row["name"]):
                    old = legacy_by_name[row["name"]]
                    if old is None:
                        self.assertIsNone(row["match_reason"])
                    else:
                        self.assertIsNotNone(row["match_reason"])
                        self.assertTrue(row["match_reason"].startswith(old))
                        self.assertIn(normalize_text_key(q), row["match_reason"])


class TestNarrowEntitiesMultiToken(unittest.TestCase):
    """② 다어절 OR — 한 토큰만 맞아도 남는다."""

    def test_다어절_질의가_한_토큰으로_걸린다(self) -> None:
        rows = narrow_entities(list(_ITEMS), "여자 솔로 가수")
        # `가수` 는 두 개체의 설명문에 있다. AND 였다면 0건(현행 재현율 0%)이다.
        self.assertEqual(_names(rows), ["아이유", "장범준"])
        self.assertEqual(rows[0]["match_reason"], "설명 일치: 가수")

    def test_현행은_같은_질의에서_0건이었다(self) -> None:
        """개선의 전제를 함께 봉인한다 — 통짜 매칭으로는 걸릴 수 없다."""
        self.assertEqual(_legacy_narrow(_ITEMS, "여자 솔로 가수"), [])


class TestNarrowEntitiesOrdering(unittest.TestCase):
    """③ 정렬 — 맞은 토큰 수 → 묶음 크기 → 이름. 동점을 **두 단계 다** 시험한다(B4)."""

    # 정렬만 보기 위한 전용 픽스처. 이름에는 질의 토큰이 들어 있지 않아 근거 키워드로만 갈린다.
    SORT_ITEMS = (
        _item("델타", ["봄"], "", 50),
        _item("베타", ["봄"], "", 99),
        _item("알파", ["봄", "여름"], "", 1),
        _item("감마", ["봄"], "", 50),
    )

    def test_맞은_토큰_수가_묶음_크기보다_앞선다(self) -> None:
        """1순위 — 2점짜리(묶음 1건)가 1점짜리(묶음 99건)보다 위다."""
        rows = narrow_entities(list(self.SORT_ITEMS), "봄 여름")
        self.assertEqual(_names(rows)[0], "알파")

    def test_토큰_수가_같으면_묶음_크기로_가른다(self) -> None:
        """2순위 — 베타(99) > 감마·델타(50)."""
        rows = narrow_entities(list(self.SORT_ITEMS), "봄 여름")
        self.assertEqual(_names(rows)[1], "베타")

    def test_묶음_크기까지_같으면_이름으로_가른다(self) -> None:
        """3순위 — 감마·델타는 1점·50건으로 완전 동점이라 이름 오름차순으로 갈린다."""
        rows = narrow_entities(list(self.SORT_ITEMS), "봄 여름")
        self.assertEqual(_names(rows), ["알파", "베타", "감마", "델타"])

    def test_같은_질의는_언제나_같은_순서다(self) -> None:
        """B4 결정성 — 입력 순서를 바꿔도 결과 순서는 같다."""
        forward = _names(narrow_entities(list(self.SORT_ITEMS), "봄 여름"))
        backward = _names(narrow_entities(list(reversed(self.SORT_ITEMS)), "봄 여름"))
        self.assertEqual(forward, backward)


class TestNarrowEntitiesSpacedNames(unittest.TestCase):
    """⑥ 공백 포함 이름 회귀 — 2토큰으로 쪼개져도 정답이 1위여야 한다.

    노출 개체 82개 중 10개(12%)가 이름에 공백을 갖는다(실측 2026-08-31). 정규화가 공백을 지우므로
    이름 자체는 붙어 있고(``"수원 화성"``→``"수원화성"``), 질의 두 토큰이 **모두** 그 안에 있어
    2점이 된다. 1점짜리 방해 개체가 묶음 크기에서 앞서더라도 토큰 수가 이긴다.
    """

    CASES = (
        ("수원 화성", "수원 화성", "경기도"),               # 방해: 설명문에 `수원`(묶음 8건)
        ("FIFA 월드컵", "FIFA 월드컵", "올림픽"),          # 방해: 설명문에 `월드컵`(묶음 10건)
        ("유희열의 스케치북", "유희열의 스케치북", "장범준"),   # 방해: 설명문에 `스케치북`
    )

    def test_정답이_1위다(self) -> None:
        for q, answer, distractor in self.CASES:
            with self.subTest(q=q):
                rows = narrow_entities(list(_ITEMS), q)
                self.assertEqual(_names(rows)[0], answer)
                # 방해 개체도 결과에는 있다(OR) — 순위로 갈릴 뿐이다.
                self.assertIn(distractor, _names(rows))

    def test_현행도_이름_질의는_찾았다(self) -> None:
        """B2 — 이 세 이름은 현행에서도 찾혔다(정규화가 공백을 지우므로). 회귀 여부의 기준선."""
        for q, answer, _ in self.CASES:
            with self.subTest(q=q):
                self.assertIn(answer, _names(_legacy_narrow(_ITEMS, q)))


class TestNarrowEntitiesSingleCharToken(unittest.TestCase):
    """⑦ 한 글자 토큰 — 최소 길이 필터를 두지 않는다(결정 근거는 구현 모듈 주석)."""

    def test_한_글자_질의는_전체가_아니라_걸린_것만_준다(self) -> None:
        """🔴 필터를 켰다면 토큰 0개가 되어 ④의 계약대로 **전체**가 나왔을 자리다."""
        rows = narrow_entities(list(_ITEMS), "강")
        self.assertLess(len(rows), len(_ITEMS))
        self.assertEqual(_names(rows), ["강원도", "한강", "나일강"])

    def test_한_글자_토큰도_점수에_들어간다(self) -> None:
        """실측 사례 — `아프리카 큰 강` 의 정답(나일강)은 `강` 으로만 걸린다.

        필터를 켜면 이 질의는 0건이 된다(측정: 다어절 재현율 43.3%→36.7%).
        """
        rows = narrow_entities(list(_ITEMS), "아프리카 큰 강")
        self.assertIn("나일강", _names(rows))


if __name__ == "__main__":
    unittest.main()


class TestFuseEntityResults(unittest.TestCase):
    """090 G3 — 융합. 문자열 결과 **위에** 의미 결과를 얹는다.

    무엇을 봉인하나: 틀리면 **이름 검색 100%가 깨지거나 건수가 어긋나는** 다섯 가지를 본다.

        ① 문자열 결과가 항상 위 — C3(이름 회귀 0)의 구조적 보장이다.
        ② 문자열 0건이면 의미 결과만 — 089 가 0건이던 자리를 채우는 것이 이 기능의 목적.
        ③ 상한을 넘게 더하지 않는다 — 노이즈가 늘면 순위가 무의미해진다.
        ④ 같은 입력 → 같은 순서(헌법 결정성 · 재측정이 성립해야 한다).
        ⑤ 임베딩이 없는 개체가 섞여도 깨지지 않는다(배치가 아직 안 돈 상태).
    """

    _ITEMS = [
        {"entity_type": "인물", "entity_uid": "아이유", "name": "아이유", "confirmed_count": 14},
        {"entity_type": "음식", "entity_uid": "김치", "name": "김치", "confirmed_count": 13},
        {"entity_type": "장소", "entity_uid": "제주도", "name": "제주도", "confirmed_count": 14},
        {"entity_type": "장소", "entity_uid": "수원화성", "name": "수원 화성", "confirmed_count": 5},
    ]

    @staticmethod
    def _sem(*pairs: tuple[str, str, float]) -> list[dict[str, Any]]:
        return [{"entity_type": t, "entity_uid": u, "similarity": s} for t, u, s in pairs]

    def test_문자열_결과가_항상_위(self) -> None:
        # 🔴 C3 의 구조적 보장 — 이름으로 걸린 것은 확실한 것이라 위에 둔다.
        string_hits = [{**self._ITEMS[1], "match_reason": None}]          # 김치
        got = fuse_entity_results(self._ITEMS, string_hits,
                                  self._sem(("인물", "아이유", 0.61)))
        self.assertEqual([r["entity_uid"] for r in got], ["김치", "아이유"])
        self.assertIsNone(got[0]["match_reason"])

    def test_유사도가_더_높아도_문자열이_위(self) -> None:
        string_hits = [{**self._ITEMS[1], "match_reason": None}]
        got = fuse_entity_results(self._ITEMS, string_hits,
                                  self._sem(("장소", "제주도", 0.99)))
        self.assertEqual(got[0]["entity_uid"], "김치")

    def test_문자열_0건이면_의미_결과만(self) -> None:
        got = fuse_entity_results(self._ITEMS, [],
                                  self._sem(("음식", "김치", 0.37), ("인물", "아이유", 0.31)))
        self.assertEqual([r["entity_uid"] for r in got], ["김치", "아이유"])

    def test_의미_결과가_없으면_089_동작_그대로(self) -> None:
        # ⚠️ 되돌림의 실질 — semantic_hits 를 비우면 융합이 없던 것과 같다.
        string_hits = [{**self._ITEMS[0], "match_reason": None}]
        self.assertEqual(fuse_entity_results(self._ITEMS, string_hits), string_hits)
        self.assertEqual(fuse_entity_results(self._ITEMS, string_hits, []), string_hits)

    def test_문자열이_이미_잡은_것은_두_번_넣지_않는다(self) -> None:
        # 같은 개체가 두 줄로 보이면 건수가 어긋난다.
        string_hits = [{**self._ITEMS[1], "match_reason": None}]
        got = fuse_entity_results(self._ITEMS, string_hits,
                                  self._sem(("음식", "김치", 0.9), ("인물", "아이유", 0.5)))
        self.assertEqual([r["entity_uid"] for r in got], ["김치", "아이유"])

    def test_상한을_넘게_더하지_않는다(self) -> None:
        got = fuse_entity_results(
            self._ITEMS, [],
            self._sem(("음식", "김치", 0.5), ("인물", "아이유", 0.4), ("장소", "제주도", 0.3)),
            max_semantic=2)
        self.assertEqual(len(got), 2)

    def test_상한이_없으면_받은_전부(self) -> None:
        got = fuse_entity_results(
            self._ITEMS, [],
            self._sem(("음식", "김치", 0.5), ("인물", "아이유", 0.4), ("장소", "제주도", 0.3)))
        self.assertEqual(len(got), 3)

    def test_목록에_없는_개체는_건너뛴다(self) -> None:
        # 🔴 벡터는 남아 있어도 목록이 정본이다(노출 임계 아래로 내려간 개체 등).
        got = fuse_entity_results(self._ITEMS, [],
                                  self._sem(("인물", "없는사람", 0.9), ("음식", "김치", 0.4)))
        self.assertEqual([r["entity_uid"] for r in got], ["김치"])

    def test_임베딩이_없는_개체가_섞여도_깨지지_않는다(self) -> None:
        # 배치가 아직 안 돈 상태 — 의미 결과가 일부 개체만 담고 있다.
        string_hits = [{**self._ITEMS[3], "match_reason": None}]          # 수원화성
        got = fuse_entity_results(self._ITEMS, string_hits, self._sem(("음식", "김치", 0.4)))
        self.assertEqual([r["entity_uid"] for r in got], ["수원화성", "김치"])

    def test_이유에_유사도가_실린다(self) -> None:
        got = fuse_entity_results(self._ITEMS, [], self._sem(("음식", "김치", 0.5321)))
        self.assertEqual(got[0]["match_reason"], f"{REASON_SEMANTIC} (0.53)")

    def test_유사도가_없어도_이유는_붙는다(self) -> None:
        got = fuse_entity_results(self._ITEMS, [],
                                  [{"entity_type": "음식", "entity_uid": "김치"}])
        self.assertEqual(got[0]["match_reason"], REASON_SEMANTIC)

    def test_같은_입력이면_같은_순서(self) -> None:
        # 헌법 결정성 — 재측정이 성립해야 한다.
        sem = self._sem(("음식", "김치", 0.5), ("인물", "아이유", 0.5))
        a = fuse_entity_results(self._ITEMS, [], sem)
        b = fuse_entity_results(self._ITEMS, [], sem)
        self.assertEqual([r["entity_uid"] for r in a], [r["entity_uid"] for r in b])

    def test_입력_행을_고치지_않는다(self) -> None:
        # 순수 함수 — 호출한 쪽의 목록이 오염되면 다음 질의가 이상해진다.
        items = [dict(it) for it in self._ITEMS]
        before = [dict(it) for it in items]
        fuse_entity_results(items, [], self._sem(("음식", "김치", 0.5)))
        self.assertEqual(items, before)

    def test_문자열_행도_사본이다(self) -> None:
        string_hits = [{**self._ITEMS[0], "match_reason": None}]
        got = fuse_entity_results(self._ITEMS, string_hits, [])
        got[0]["match_reason"] = "바뀜"
        self.assertIsNone(string_hits[0]["match_reason"])


class TestGateSemanticHits(unittest.TestCase):
    """090 후속 — 정답이 **없는** 질의에서 의미 결과를 통째로 접는 게이트(2026-09-01).

    왜 필요했나(사용자 지적 2026-08-31): 090 은 유사도 컷오프를 폐기했다(그 판단은 옳았다 —
    0.45 는 정답 16건을 버렸다). 대가로 **정답이 아예 없는 질의에도 상위 3이 무조건 나갔다**
    (`전자제품`→NASA·전주한옥마을·식혜). 기준선 80개 질의가 전부 "정답이 있는" 질의여서
    이 실패 모드는 측정된 적이 없었다.

    무엇을 쓰나: **자산 검색이 쓰는 그 공식**(``src/search/fusion.py`` 의 ``gate_signal`` +
    ``passes_cutoff``)을 그대로 가져왔다 — ``유지 = (top − baseline) ≥ eps``. baseline 은
    개체 전량 코사인의 **하위 절반 평균**이라 "1등이 무리보다 튀어나왔는가"를 본다.
    공식을 베껴 쓰지 않고 import 하는 이유는 한쪽만 고쳐지는 사고를 막기 위해서다.

    ⚠️ **절대 유사도로는 못 가른다**(측정 2026-08-31·09-01 재확인): 정답 없는 질의의 1위가
    최대 0.495(`날씨`)인데 정답 있는 질의의 1위가 최소 0.343 이라 분포가 겹친다. 그래서
    절대 하한(floor)은 쓰지 않는다 — 스윕에서도 0.35 부터는 재현율만 깎았다.

    실측 임계 0.15 의 대가(fixtures/mm_meta_search/gate_sweep_20260901.json):
      무관 질의 24개 중 22개 차단(결과 3.0→0.2건) · C1 80→70% · C2 80→73.3% · C3 100% 유지.
    """

    @staticmethod
    def _hits(*sims: float) -> list[dict[str, Any]]:
        """유사도만 다른 의미 결과를 만든다(내림차순 가정은 호출부가 지킨다)."""
        return [{"entity_type": "음식", "entity_uid": f"e{i}", "similarity": s}
                for i, s in enumerate(sims)]

    def test_일등이_튀면_통과한다(self) -> None:
        # 0.60 − (하위 절반 0.20·0.21 평균 0.205) = 0.395 ≥ 0.15
        got = gate_semantic_hits(self._hits(0.60, 0.30, 0.21, 0.20), eps=0.15, top_n=3)
        self.assertEqual([h["entity_uid"] for h in got], ["e0", "e1", "e2"])

    def test_뭉쳐_있으면_전부_버린다(self) -> None:
        # 1등이 무리에서 튀지 않는다 = 아무거나 걸린 것이다.
        self.assertEqual(gate_semantic_hits(self._hits(0.40, 0.39, 0.38, 0.37),
                                            eps=0.15, top_n=3), [])

    def test_끄면_판정_없이_그대로_나간다(self) -> None:
        """되돌림의 실질 — 설정 하나로 090 동작이 그대로 돌아온다."""
        hits = self._hits(0.40, 0.39, 0.38, 0.37)
        got = gate_semantic_hits(hits, eps=0.15, top_n=3, enabled=False)
        self.assertEqual([h["entity_uid"] for h in got], ["e0", "e1", "e2"])

    def test_상위_N_만_돌려준다(self) -> None:
        got = gate_semantic_hits(self._hits(0.60, 0.30, 0.25, 0.20, 0.19), eps=0.15, top_n=2)
        self.assertEqual(len(got), 2)

    def test_빈_입력은_빈_결과다(self) -> None:
        self.assertEqual(gate_semantic_hits([], eps=0.15, top_n=3), [])

    def test_한_건뿐이면_통과시킨다(self) -> None:
        """표본이 1개면 baseline 이 0.0 이라(gate_signal 계약) 사실상 통과다 — 그 계약을 못 박는다."""
        got = gate_semantic_hits(self._hits(0.30), eps=0.15, top_n=3)
        self.assertEqual(len(got), 1)

    def test_같은_입력이면_같은_결과(self) -> None:
        hits = self._hits(0.50, 0.30, 0.22, 0.20)
        first = gate_semantic_hits(hits, eps=0.15, top_n=3)
        for _ in range(4):
            self.assertEqual(gate_semantic_hits(hits, eps=0.15, top_n=3), first)

    def test_입력을_고치지_않는다(self) -> None:
        hits = self._hits(0.60, 0.30, 0.20)
        before = [dict(h) for h in hits]
        gate_semantic_hits(hits, eps=0.15, top_n=3)
        self.assertEqual(hits, before)

    # ── 실 DB 실측 재현(2026-09-01 · 개체 82개 · fixtures gate_sweep_20260901) ──
    @staticmethod
    def _with_signal(top: float, baseline: float) -> list[dict[str, Any]]:
        """실측 ``(top, baseline)`` 을 그대로 재현하는 최소 유사도 목록을 만든다.

        게이트는 (최고값, 하위 절반 평균) 두 값만 쓴다. 하위 두 칸을 ``baseline`` 으로 채우면
        그 평균이 곧 ``baseline`` 이 되어, 실 DB 82개 코사인을 들고 다니지 않고도 같은 판정을
        재현할 수 있다(테스트 전용 우회로를 구현에 뚫지 않으려는 것이다).

        Args:
            top: 1위 유사도(실측값).
            baseline: 하위 절반 평균(실측값).

        Returns:
            ``similarity`` 만 든 4행 — 유사도 내림차순.
        """
        return [{"similarity": s} for s in (top, (top + baseline) / 2, baseline, baseline)]

    def test_실측_차단_사례(self) -> None:
        """무관 질의 — 이것이 이 게이트를 만든 이유다."""
        for q, top, base in (("전자제품", 0.3893, 0.2753), ("발효", 0.3777, 0.2706)):
            with self.subTest(q=q):
                self.assertEqual(
                    gate_semantic_hits(self._with_signal(top, base), eps=0.15, top_n=3), [])

    def test_실측_통과_사례(self) -> None:
        for q, top, base in (("여자 솔로 가수", 0.4567, 0.2304), ("아이유", 0.5695, 0.1944)):
            with self.subTest(q=q):
                got = gate_semantic_hits(self._with_signal(top, base), eps=0.15, top_n=3)
                self.assertEqual(len(got), 3)

    def test_실측_잃는_사례를_숨기지_않는다(self) -> None:
        """🔴 `장군`→이순신은 **정답 1위인데** 차단된다(임계 0.15 의 대가 4건 중 하나).

        어제 대안(격차 0.03)도 같은 것을 잃으면서 차단은 67% 에 그쳤다. 잃는 것을 테스트로
        적어 두는 이유는, 나중에 이 4건이 문제가 되면 임계를 내리는 판단을 하라는 뜻이다
        (`보양식`·`얼음 대륙`·`푸른빛 도자기` 가 나머지 셋이다).
        """
        self.assertEqual(
            gate_semantic_hits(self._with_signal(0.3719, 0.2465), eps=0.15, top_n=3), [])

    def test_날씨는_임계_0_15_를_통과한다(self) -> None:
        """무관한데 유일하게 살아남는 질의 — 개체 설명문 전반이 지역·기후를 언급해 판 전체가 들렸다.

        임계를 0.16 으로 올리면 이것도 막히지만 C1 이 65% 로 떨어진다(스윕 표). 하나를 잡으려고
        재현율을 깎지 않는다.
        """
        got = gate_semantic_hits(self._with_signal(0.4954, 0.3257), eps=0.15, top_n=3)
        self.assertEqual(len(got), 3)

    def test_임계를_낮추면_실측_차단_사례가_살아난다(self) -> None:
        """임계가 실제로 판정을 가르는지 — 상수가 장식이 아님을 못 박는다."""
        got = gate_semantic_hits(self._with_signal(0.3893, 0.2753), eps=0.10, top_n=3)
        self.assertEqual(len(got), 3)


class TestEntityRefineFields(unittest.TestCase):
    """091 T004 — 개체 행에서 **결과 내 재검색** 대상 필드를 뽑는다.

    089 ``match_entity`` 와 보는 곳은 같다(이름 · 근거 키워드 · 설명문). 다른 것은 **용도**다 —
    ``match_entity`` 는 찾아오기(OR · 하나만 맞아도 남김)이고, 재검색은 골라내기(AND · 전부
    맞아야 남김)다. 그래서 ``match_entity`` 를 고치지 않고 **필드만 뽑아** 코어 좁히기 함수
    (``src/search/refine.refine_rows``)에 넘긴다.
    """

    def test_세_축을_모두_싣는다(self) -> None:
        got = entity_refine_fields(
            {"name": "김치", "keywords": ["발효", "전통음식"], "description": "한국의 발효 채소 요리"})
        self.assertEqual(got, ["김치", "발효", "전통음식", "한국의 발효 채소 요리"])

    def test_설명문이_없어도_죽지_않는다(self) -> None:
        # 집계 결과라 설명이 아직 없는 개체가 실제로 있다(089 match_entity 와 같은 상황).
        got = entity_refine_fields({"name": "김치", "keywords": ["발효"], "description": None})
        self.assertEqual(got, ["김치", "발효"])

    def test_필드_결측_타입이상에도_문자열_목록을_준다(self) -> None:
        for item in ({}, {"name": None, "keywords": None, "description": None},
                     {"name": "김치", "keywords": "문자열이_왔다"}, {"keywords": [None, "", "정상"]}):
            with self.subTest(item=item):
                got = entity_refine_fields(item)  # type: ignore[arg-type]
                self.assertIsInstance(got, list)
                self.assertTrue(all(isinstance(x, str) and x for x in got))

    def test_좁히기와_이어_붙여_동작한다(self) -> None:
        """실제 사용 경로 — 코어 좁히기 함수에 이 추출기를 넘긴다(AND · 원 순서)."""
        from src.search.refine import refine_rows
        items = [
            {"name": "김치", "keywords": ["발효", "전통음식"], "description": "한국의 발효 채소 요리"},
            {"name": "된장", "keywords": ["발효"], "description": "콩을 발효한 장"},
            {"name": "아이유", "keywords": ["가수"], "description": "대한민국의 가수"},
        ]
        got = refine_rows(items, "발효 채소", fields_of=entity_refine_fields)
        self.assertEqual([r["name"] for r in got], ["김치"])
