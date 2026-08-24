"""083 T101·T102 — 검색 태그 패싯 순수 함수 단위 테스트(``src/search/tag_facets.py``).

무엇을 검증하나:
  - ``normalize_tag_key`` 가 ``src.domain.text_norm.normalize_text_key`` **그 함수 자체**인지
    (재수출 — 규칙 사본이 두 벌 생기면 색인 키와 필터 키가 갈라진다 · 083 plan §Global Constraints).
  - ``display_label`` 이 그룹 안에서 **원문 최빈**을 고르고, 동률이면 **유니코드 코드포인트 오름차순**
    첫 값을 고르는지(결정성 — 헌법 3조. spec 은 "사전순"을 코드포인트 순으로 정의한다).

DB·LLM·OpenSearch 불필요한 순수 단위 테스트다.
"""

from __future__ import annotations

import unittest

from src.domain.text_norm import normalize_text_key
from src.search.tag_facets import aggregate_tag_facets, display_label, normalize_tag_key


def _rows(*tag_lists: list[str]) -> list[dict]:
    """태그 배열만 가진 검색 결과 행 목록을 만든다(집계는 다른 필드를 보지 않는다).

    Args:
        *tag_lists: 행마다의 원문 태그 배열.

    Returns:
        ``[{"tags": [...]}, ...]`` 형태의 행 목록.
    """
    return [{"tags": list(t)} for t in tag_lists]


class TestNormalizeTagKeyReexport(unittest.TestCase):
    """태그 정규화는 별도 구현이 아니라 도메인 정본의 **재수출**이어야 한다."""

    def test_같은_함수_객체다(self) -> None:
        # 별 구현을 두면(복사 붙여넣기) 083 태그와 084 개체의 규칙이 언젠가 갈라진다.
        self.assertIs(normalize_tag_key, normalize_text_key)

    def test_병합_사례가_그대로_통한다(self) -> None:
        self.assertEqual(normalize_tag_key("전통 음식"), "전통음식")


class TestDisplayLabel(unittest.TestCase):
    """표시 라벨 = 그룹 내 원문 최빈, 동률은 코드포인트 오름차순 첫 값."""

    def test_최빈_원문을_고른다(self) -> None:
        # dev 실측: `전통음식` 10건 · `전통 음식` 6건 → 화면에는 많이 쓰인 표기를 보여준다.
        originals = ["전통음식"] * 10 + ["전통 음식"] * 6
        self.assertEqual(display_label(originals), "전통음식")

    def test_소수_표기가_최빈이면_그것을_고른다(self) -> None:
        # "짧은 표기 우선" 같은 규칙이 아니라 순수 빈도임을 봉인한다.
        self.assertEqual(display_label(["전통 음식", "전통 음식", "전통음식"]), "전통 음식")

    def test_동률이면_코드포인트_오름차순_첫값(self) -> None:
        # 대소문자를 무시하는 사전순이 아니라 **코드포인트** 순서다: 'Z'(0x5A) < 'a'(0x61).
        self.assertEqual(display_label(["apple", "Zoo"]), "Zoo")
        self.assertEqual(display_label(["Zoo", "apple"]), "Zoo")

    def test_입력_순서가_결과를_바꾸지_않는다(self) -> None:
        # 결정성(헌법 3조) — 행이 오는 순서가 라벨을 흔들면 같은 질의가 다르게 보인다.
        a = ["전통 음식", "전통음식", "전통음식", "전통 음식"]
        self.assertEqual(display_label(a), display_label(list(reversed(a))))

    def test_앞뒤_공백은_같은_라벨로_센다(self) -> None:
        # "전통 음식 "(뒤 공백)은 화면에서 같은 라벨이며, 따로 세면 최빈 판정이 흔들린다.
        self.assertEqual(display_label(["전통 음식 ", "전통 음식", "전통음식"]), "전통 음식")

    def test_빈_목록은_빈_문자열(self) -> None:
        # 호출부가 빈 키를 배제하므로 정상 경로에서는 오지 않지만, 방어적으로 계약을 고정한다.
        self.assertEqual(display_label([]), "")

    def test_공백뿐인_원문은_라벨_후보가_아니다(self) -> None:
        self.assertEqual(display_label(["  ", "김치"]), "김치")
        self.assertEqual(display_label(["  ", ""]), "")


class TestAggregateTagFacets(unittest.TestCase):
    """T102 결과-스코프 집계 — 행당 1회 계수·표기 병합·건수·상위 N·has_more·결정적 정렬."""

    def test_표기_분산은_한_항목으로_병합된다(self) -> None:
        # SC-03: `전통음식`(3행) + `전통 음식`(2행) → 한 항목 5건. 두 항목으로 뜨면 건수가 거짓이 된다.
        rows = _rows(["전통음식"], ["전통음식"], ["전통음식"], ["전통 음식"], ["전통 음식"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(out["items"], [{"label": "전통음식", "count": 5}])

    def test_같은_행의_다른_표기는_1건으로만_센다(self) -> None:
        # 자산 1건이 2건으로 부풀면 "패싯 건수 == 클릭 후 결과 수"(SC-02)가 깨진다.
        rows = _rows(["전통음식", "전통 음식"], ["전통 음식"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(out["items"], [{"label": "전통 음식", "count": 2}])

    def test_min_count_미만은_감춘다(self) -> None:
        # 실측상 태그의 83.8% 가 1건짜리 — 감추지 않으면 목록이 파편으로 찬다(SC-07).
        rows = _rows(["김치", "일회성"], ["김치"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual([i["label"] for i in out["items"]], ["김치"])

    def test_min_count_1_이면_전부_노출(self) -> None:
        rows = _rows(["김치", "일회성"], ["김치"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=1)
        self.assertEqual([i["label"] for i in out["items"]], ["김치", "일회성"])

    def test_정렬은_건수_내림차순_다음_라벨_코드포인트_오름차순(self) -> None:
        # 동률 tie-break 가 코드포인트 순서임을 봉인한다: 'Z'(0x5A) < 'a'(0x61) < '가'(0xAC00).
        rows = _rows(
            ["많음", "Zoo", "apple", "가나"],
            ["많음", "Zoo", "apple", "가나"],
            ["많음"],
        )
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(
            out["items"],
            [
                {"label": "많음", "count": 3},
                {"label": "Zoo", "count": 2},
                {"label": "apple", "count": 2},
                {"label": "가나", "count": 2},
            ],
        )

    def test_상위_N_절단과_has_more(self) -> None:
        rows = _rows(["a", "b", "c"], ["a", "b", "c"])
        out = aggregate_tag_facets(rows, top_n=2, min_count=2)
        self.assertEqual([i["label"] for i in out["items"]], ["a", "b"])
        self.assertTrue(out["has_more"])

    def test_절단할_것이_없으면_has_more_는_거짓(self) -> None:
        rows = _rows(["a", "b"], ["a", "b"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertFalse(out["has_more"])

    def test_has_more_는_감춰진_1건짜리를_세지_않는다(self) -> None:
        # "더 보기"는 노출 대상(min_count 통과) 중 잘린 게 있을 때만 참이어야 한다 —
        # 감춘 파편 때문에 참이 되면 눌러도 아무것도 안 나온다.
        rows = _rows(["a", "혼자1"], ["a", "혼자2"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual([i["label"] for i in out["items"]], ["a"])
        self.assertFalse(out["has_more"])

    def test_label_by_key_는_감춰진_키까지_포함한다(self) -> None:
        # 행 태그 칩 표시가 이 매핑을 쓴다(spec §⑥) — 상위 목록 밖 태그도 라벨로 바꿀 수 있어야 한다.
        rows = _rows(["김치", "일회성"], ["김치"])
        out = aggregate_tag_facets(rows, top_n=1, min_count=2)
        self.assertEqual(out["label_by_key"], {"김치": "김치", "일회성": "일회성"})

    def test_label_by_key_는_정규화키에서_최빈_원문으로(self) -> None:
        rows = _rows(["전통음식"], ["전통음식"], ["전통 음식"])
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(out["label_by_key"], {"전통음식": "전통음식"})

    def test_빈_결과는_빈_패싯(self) -> None:
        out = aggregate_tag_facets([], top_n=12, min_count=2)
        self.assertEqual(out, {"items": [], "has_more": False, "label_by_key": {}})

    def test_태그가_없거나_모양이_다른_행은_건너뛴다(self) -> None:
        # 검색 백엔드가 예상과 다른 모양(문자열 하나·None·비문자 원소)을 줘도 집계가 죽지 않아야
        # 한다. 특히 문자열 하나를 그대로 순회하면 글자 단위 쓰레기 키가 생긴다.
        rows: list[dict] = [
            {},
            {"tags": None},
            {"tags": "김치"},
            {"tags": ["김치", None, 3, "  "]},
            {"tags": ["김치"]},
        ]
        out = aggregate_tag_facets(rows, top_n=12, min_count=2)
        self.assertEqual(out["items"], [{"label": "김치", "count": 2}])

    def test_같은_입력_두번은_완전히_동일한_출력(self) -> None:
        # SC-06 결정성(헌법 3조) — 집계에 집합·dict 순회가 섞여 있어 tie-break 없이는 흔들린다.
        rows = _rows(
            ["전통음식", "김치", "발효"],
            ["전통 음식", "김치"],
            ["발효", "Zoo", "apple"],
            ["Zoo", "apple", "김치"],
        )
        first = aggregate_tag_facets(rows, top_n=3, min_count=2)
        second = aggregate_tag_facets(rows, top_n=3, min_count=2)
        self.assertEqual(first, second)

    def test_행_순서가_결과를_바꾸지_않는다(self) -> None:
        rows = _rows(["전통음식", "김치"], ["전통 음식", "발효"], ["김치", "발효"])
        forward = aggregate_tag_facets(rows, top_n=12, min_count=2)
        backward = aggregate_tag_facets(list(reversed(rows)), top_n=12, min_count=2)
        self.assertEqual(forward["items"], backward["items"])

    def test_잘못된_임계는_즉시_ValueError(self) -> None:
        # 조용히 빈 목록을 돌려주면 "태그 없는 결과"와 설정 오류가 구분되지 않는다(fail-fast).
        with self.assertRaises(ValueError):
            aggregate_tag_facets(_rows(["김치"]), top_n=0, min_count=2)
        with self.assertRaises(ValueError):
            aggregate_tag_facets(_rows(["김치"]), top_n=12, min_count=0)


if __name__ == "__main__":
    unittest.main()
