"""084 T001 — 멀티모달 메타 **결정 규칙**(순수) 단위 테스트(``src/mm_meta/rules.py``).

무엇을 검증하나: LLM 판정 뒤에 붙는 **결정적 후처리 3단**(광역 제외 → 지정·자격 스톱패턴 →
행정 접미 병합)이 사전 검증(2026-08-20)에서 채택된 값 그대로 동작하는지 본다.

왜 이 규칙이 LLM 밖에 있나(사전 검증 §6-③ 실측): '정식 종목' 같은 **지정·자격 경계**에서 LLM 은
비일관했다 — 같은 패턴에서 어떤 종목은 제외하고 어떤 종목은 남기고, 금메달리스트(인물)까지 과잉
제거했다. 문자열 매칭은 그런 흔들림이 없다(헌법 3조 결정성). 그래서 "판정=LLM, 규칙=코드"로 갈랐다.

값의 출처는 `specs/084-entity-bundle/spec.md` §3 표와 `docs/개체묶음_사전검증_20260820.md` §6 이며,
이 테스트가 그 값을 **봉인**한다(목록이 조용히 늘거나 줄면 여기서 빨간불).

⚠️ 픽스처는 실 자산 데이터가 아니다 — 보고서에 적힌 **유형**(13개 키워드가 한 개체로 접힘 등)만
더미 값으로 재현한다. 개체명(제주도·서울 등)은 규칙 목록·합격선에 적힌 설계 값이라 그대로 쓴다.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator, Mapping

from src.domain.text_norm import normalize_text_key
from src.mm_meta.rules import (
    ENTITY_TYPE_ORDER,
    ENTITY_TYPES,
    ENTITY_TYPE_DEFS,
    EXCLUDED_ENTITIES,
    MIN_BASE_LENGTH,
    RULE_VERSION,
    STOP_PATTERNS,
    SUFFIX_MERGE_TYPES,
    SUFFIXES,
    ExtractedEntity,
    apply_rules,
    build_official_name_index,
    is_excluded_entity,
    is_stopped_keyword,
)


def _e(keyword: str, name: str, entity_type: str = "장소") -> ExtractedEntity:
    """판정 하나(키워드 → 개체 표준표기)를 짧게 만드는 도우미.

    Args:
        keyword: 원문 키워드(판정의 출처 — reason 스탬프의 ``kw=`` 가 될 값).
        name: LLM 이 고른 개체 표준표기.
        entity_type: 닫힌 5종 중 하나. 기본은 장소(보고서 사례가 대부분 장소다).

    Returns:
        검증을 통과한 ``ExtractedEntity``.
    """
    return ExtractedEntity(keyword=keyword, name=name, entity_type=entity_type)


class _LookupOnlyIndex(Mapping[tuple[str, str], str]):
    """룩업만 허용하는 색인 — **훑거나 복사하면 실패**한다(발산 결함 ② 재발 방지 봉인).

    무엇을 막나: ``apply_rules`` 가 주입 색인을 자산마다 훑거나(``{**index}``·``dict(index)``·
    ``keys()``) 다시 조립하면, 자산 1건 처리에 **전체 개체 수**만큼 일이 생긴다(자산 10만 × 개체
    10만 = 100억). 색인은 배치 시작에 한 번 만들고 자산 루프에서는 **dict 룩업만** 해야 한다.
    이 가짜 색인은 순회를 시도하는 순간 터지므로 그 규율을 코드로 못 박는다.

    Args:
        data: ``{(타입, 몸통 표기 키): 공식 표기}`` 초기값.
    """

    def __init__(self, data: Mapping[tuple[str, str], str]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: tuple[str, str]) -> str:
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        raise AssertionError(
            "주입 색인을 훑었다 — 자산 루프 안에서 전체 개체 수만큼 도는 발산 결함이다"
        )


class TestClosedVocabulary(unittest.TestCase):
    """닫힌 어휘·버전 상수 — 값이 계약이다(reason 스탬프의 ``rv=`` 가 이것을 가리킨다)."""

    def test_개체_타입_5종이다(self) -> None:
        self.assertEqual(ENTITY_TYPES, frozenset({"인물", "장소", "조직", "작품", "사건"}))

    def test_타입_제시_순서가_고정되어_있다(self) -> None:
        # frozenset 은 순서가 없다 — 프롬프트에 실릴 나열 순서는 별 상수로 고정해야 같은 입력이
        # 같은 문안을 만든다(결정성 · 헌법 3조).
        self.assertEqual(ENTITY_TYPE_ORDER, ("인물", "장소", "조직", "작품", "사건"))
        self.assertEqual(frozenset(ENTITY_TYPE_ORDER), ENTITY_TYPES)

    def test_규칙_버전은_비교_가능한_정수다(self) -> None:
        # 백필 대상 선별이 "이력 버전 < 현행 버전"으로 판단한다(spec §6) — 순서 비교가 되어야 한다.
        self.assertIsInstance(RULE_VERSION, int)
        self.assertGreaterEqual(RULE_VERSION, 1)

    def test_광역_제외는_정의문으로_옮겨_갔다(self) -> None:
        """🔴 계약 변경(spec 087 T005) — 목록 20종 중 19종을 `장소` 정의문으로 이관했다.

        근거(전량 대조 18건 · `fixtures/entity_skill/wide_exclusion_full.json`):
        광역 개체 생성 **0건** · 과잉 제거 **0건** · 최종 결과 18/18 동일.
        논증 — 후보 쪽 코드 목록에는 `북극` 만 있으므로, LLM 이 여전히 `대한민국` 을 줬다면
        규칙이 걸러낼 수 없어 결과에 나타나야 한다. 나타나지 않았으므로 정의문이 걸러낸 것이다.
        """
        # 코드 목록에는 `북극` 하나만 남는다.
        self.assertEqual(EXCLUDED_ENTITIES, frozenset({normalize_text_key("북극")}))
        # 🔴 이관된 19종은 이제 **정의문**이 막는다 — 코드 목록에는 없다.
        for name in ("대한민국", "한국", "아시아", "태평양", "지중해"):
            with self.subTest(name=name):
                self.assertNotIn(normalize_text_key(name), EXCLUDED_ENTITIES)
        place = next(d for d in ENTITY_TYPE_DEFS if d.name == "장소")
        self.assertIn("너무 넓은 범위", place.exclusion)
        self.assertIn("개별 외국 국가", place.exclusion)  # 정책 예외도 정의문이 담는다

    def test_북극만_코드에_남는_이유(self) -> None:
        # 정책이 "북극은 빼고 남극은 남긴다"(측정된 응집력)라 **의미가 아니라 정책**이고,
        # 파일럿에서 LLM 이 유일하게 못 맞춘 항목이다. 정의문에 억지로 넣지 않는다.
        self.assertTrue(is_excluded_entity("북극"))
        self.assertFalse(is_excluded_entity("남극"))

    def test_외국_국가는_제외하지_않는다_V2_기각(self) -> None:
        # 검증 §6-①: 외국 국가 묶음은 구성원 전수 열람에서 응집력 양호(이집트=피라미드·나일강,
        # 일본=후지산·라멘, 남극=펭귄·빙하) → V2(전 국가 제외) 기각. 목록에 들어가면 안 된다.
        for name in ("일본", "이집트", "중국", "이탈리아", "프랑스", "미국", "남극"):
            with self.subTest(name=name):
                self.assertNotIn(normalize_text_key(name), EXCLUDED_ENTITIES)

    def test_제외_목록은_정규화_키로_저장된다(self) -> None:
        # 비교를 정규화 키로 하니 목록도 같은 형태여야 한다(083 공용 정본 · 자체 정규화 금지).
        for key in EXCLUDED_ENTITIES:
            with self.subTest(key=key):
                self.assertEqual(key, normalize_text_key(key))

    def test_스톱패턴_3종이다(self) -> None:
        self.assertEqual(
            set(STOP_PATTERNS),
            {normalize_text_key(p) for p in ("정식 종목", "세계문화유산", "인류무형문화유산")},
        )

    def test_행정_접미_7종이다(self) -> None:
        self.assertEqual(
            set(SUFFIXES),
            {"특별시", "광역시", "특별자치도", "특별자치시", "시", "군", "도"},
        )

    def test_접미_병합_타입은_장소_한_종류다(self) -> None:
        # 🔴 T018 결함 ③ — '시'·'군' 은 **행정 접미로만** 쓴다. 실측 색인에 '리오넬 메시'(인물)·
        # '영국 해군'(조직)·'한사군'(사건)이 몸통을 대표하는 항목으로 들어가 있었다.
        self.assertEqual(SUFFIX_MERGE_TYPES, frozenset({"장소"}))
        # 확장 지점이 되려면 닫힌 5종의 부분집합이어야 한다(어휘 밖 타입은 노드가 될 수 없다).
        self.assertTrue(SUFFIX_MERGE_TYPES <= ENTITY_TYPES)

    def test_몸통_최소_길이는_2다(self) -> None:
        # 🔴 T018 결함 ② — 1글자 몸통('독'·'인')은 엉뚱한 판정을 '독도'·'인도' 로 빨아들인다.
        self.assertEqual(MIN_BASE_LENGTH, 2)


class TestExtractedEntityShape(unittest.TestCase):
    """판정 한 건의 모양 — 닫힌 어휘·빈 값은 **경계에서** 막는다(fail-fast)."""

    def test_표기_키는_공용_정규화_함수를_쓴다(self) -> None:
        # 083(태그)과 084(메타)가 키를 공유한다 — 사본을 만들면 언젠가 한쪽만 고쳐진다.
        self.assertEqual(_e("가키워드", "서울 특별시").uid, normalize_text_key("서울 특별시"))
        self.assertEqual(_e("가키워드", "서울특별시").uid, _e("나키워드", "서울 특별시").uid)

    def test_어휘_검사는_이_클래스가_하지_않는다(self) -> None:
        """🔴 계약 변경(spec 087 T001) — 어휘 검사가 **상위 두 관문**으로 옮겨 갔다.

        전에는 여기서 5종을 검사했고, 그 때문에 등록 어휘를 늘려도 이 자리에서 막혔다. 이
        dataclass 는 허용 어휘를 알 방법이 없다(등록 행은 DB 에 있다). 지금 어휘를 보는 곳은
        ``judge._entity_from_entry``(주입 어휘로 필터)와 ``persist.ensure_entity_node``
        (쓰기 직전 · 실질 게이트)다.
        """
        # 어휘 밖 이름도 **모양이 맞으면** 만들어진다 — 걸러내는 것은 상위 관문의 책임이다.
        got = ExtractedEntity(keyword="가키워드", name="가개체", entity_type="지명")
        self.assertEqual(got.entity_type, "지명")

    def test_빈_타입은_거부한다(self) -> None:
        # 모양 검사는 남는다 — 타입은 저장 유니크 키의 절반이라 비면 키를 만들 수 없다.
        for bad in ("", "   "):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ExtractedEntity(keyword="가키워드", name="가개체", entity_type=bad)

    def test_빈_표기는_거부한다(self) -> None:
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ExtractedEntity(keyword="가키워드", name=name, entity_type="장소")

    def test_빈_키워드는_거부한다(self) -> None:
        # 출처 키워드는 reason 스탬프(``kw=``)에 그대로 실린다 — 빈 값이면 근거를 잃는다.
        with self.assertRaises(ValueError):
            ExtractedEntity(keyword="  ", name="가개체", entity_type="장소")

    def test_불변객체다(self) -> None:
        from dataclasses import FrozenInstanceError

        entity = _e("가키워드", "가개체")
        with self.assertRaises(FrozenInstanceError):
            entity.name = "나개체"  # type: ignore[misc]


class TestExcludeWideAreaEntities(unittest.TestCase):
    """① 광역 제외 — 잡동사니 서랍이 되는 국가·대륙·해양급 개체를 떨군다(검증 §2·§6-①)."""

    def test_자국은_이제_규칙이_아니라_정의문이_막는다(self) -> None:
        """🔴 계약 변경(spec 087 T005). 규칙 단계에서는 **더 이상 떨어지지 않는다.**

        왜 그래도 안전한가: LLM 이 애초에 개체로 주지 않는다(전량 대조 18건 · 생성 0건).
        규칙은 판정 **뒤**에 도는 안전망이고, 이 항목의 방어선이 앞으로 옮겨 간 것이다.
        실측 배경: '대한민국'(16건)에 단풍 가이드·김밥 프랜차이즈·낚시 채널이 한 묶음이 됐다.
        """
        kept = apply_rules([_e("가키워드", "대한민국"), _e("나키워드", "한국")])
        self.assertEqual([e.name for e in kept], ["대한민국", "한국"])

    def test_남은_항목은_표기가_흔들려도_같은_판정이다(self) -> None:
        # 정규화 키 비교는 그대로다 — `북극`·`북 극`·전각이 같은 개체로 본다.
        self.assertEqual(apply_rules([_e("가키워드", "북 극")]), ())

    def test_외국_국가는_살아남는다(self) -> None:
        kept = apply_rules([_e("가키워드", "일본"), _e("나키워드", "남극")])
        self.assertEqual([entity.name for entity in kept], ["일본", "남극"])

    def test_구체_개체는_영향받지_않는다(self) -> None:
        kept = apply_rules([_e("가키워드", "제주도"), _e("나키워드", "경복궁")])
        self.assertEqual([entity.name for entity in kept], ["제주도", "경복궁"])

    def test_판정_함수를_따로도_쓸_수_있다(self) -> None:
        # 배치 diff 리포트가 "왜 떨어졌나"를 사유별로 세려면 술어가 노출돼 있어야 한다.
        # 대상은 코드에 남은 `북극` 뿐이다(나머지는 정의문 소관 · spec 087 T005).
        self.assertTrue(is_excluded_entity("북극"))
        self.assertFalse(is_excluded_entity("제주도"))
        self.assertFalse(is_excluded_entity("동아시아"))  # 이관됨 — 규칙은 모른다


class TestStopPatterns(unittest.TestCase):
    """② 지정·자격 스톱패턴 — 그 **키워드**를 판정에서 빼되, 인접 키워드는 건드리지 않는다."""

    def test_정식_종목_키워드는_탈락한다(self) -> None:
        # 실측: '올림픽 정식 종목' 때문에 올림픽 묶음(11건) 절반이 종목 일반 문서였다.
        self.assertEqual(apply_rules([_e("올림픽 정식 종목", "올림픽", "사건")]), ())

    def test_금메달_키워드는_통과한다(self) -> None:
        # 🔴 과잉 제거 방지(검증 §6-③): LLM 규칙은 금메달리스트(인물)까지 지웠다. 스톱패턴은
        # '정식 종목'만 보므로 '올림픽 금메달'은 살아남는다.
        kept = apply_rules([_e("올림픽 금메달", "올림픽", "사건")])
        self.assertEqual([entity.name for entity in kept], ["올림픽"])

    def test_지정_자격_패턴_3종을_모두_막는다(self) -> None:
        stopped = [
            _e("올림픽 정식 종목", "올림픽", "사건"),
            _e("세계문화유산 등재", "유네스코", "조직"),
            _e("인류무형문화유산", "유네스코", "조직"),
        ]
        self.assertEqual(apply_rules(stopped), ())

    def test_공백_표기_차이를_흡수한다(self) -> None:
        # '정식종목'(붙여 쓴 표기)도 같은 패턴이다 — 키워드도 정규화 키로 비교한다.
        self.assertEqual(apply_rules([_e("올림픽 정식종목", "올림픽", "사건")]), ())

    def test_같은_자산의_다른_키워드는_남는다(self) -> None:
        # 스톱은 **키워드 단위**다(자산 단위 폐기가 아니다 · spec §3).
        kept = apply_rules([
            _e("올림픽 정식 종목", "올림픽", "사건"),
            _e("경복궁 야간개장", "경복궁"),
        ])
        self.assertEqual([entity.name for entity in kept], ["경복궁"])

    def test_판정_함수를_따로도_쓸_수_있다(self) -> None:
        self.assertTrue(is_stopped_keyword("올림픽 정식 종목"))
        self.assertFalse(is_stopped_keyword("올림픽 금메달"))


class TestSuffixMerge(unittest.TestCase):
    """③ 행정 접미 병합 — 실측 분열 1쌍(서울/서울특별시)을 공식형으로 합친다(검증 §6-②)."""

    def test_같은_목록에_두_표기가_있으면_공식형으로_합친다(self) -> None:
        kept = apply_rules([_e("가키워드", "서울"), _e("나키워드", "서울특별시")])
        self.assertEqual([entity.name for entity in kept], ["서울특별시"])
        self.assertEqual(len({entity.uid for entity in kept}), 1)

    def test_공식형이_없으면_짧은_표기를_유지한다(self) -> None:
        # 접미를 **붙이지 않는다** — 어떤 접미가 공식인지는 규칙이 알 수 없다(서울시/서울특별시).
        kept = apply_rules([_e("가키워드", "서울")])
        self.assertEqual([entity.name for entity in kept], ["서울"])

    def test_접미가_붙은_표기는_그대로_둔다(self) -> None:
        # '제주도'를 '제주'로 깎으면 합격선(제주도 묶음 14±1)의 이름이 달라진다 — 깎지 않는다.
        kept = apply_rules([_e("가키워드", "제주도"), _e("나키워드", "경주시")])
        self.assertEqual([entity.name for entity in kept], ["제주도", "경주시"])

    def test_공식형이_여럿이면_긴_접미를_고른다(self) -> None:
        # 결정성 — 후보가 둘이면 항상 같은 하나를 골라야 한다(더 공식적인 긴 접미 우선).
        kept = apply_rules([
            _e("가키워드", "서울"),
            _e("나키워드", "서울시"),
            _e("다키워드", "서울특별시"),
        ])
        self.assertEqual({entity.name for entity in kept}, {"서울특별시", "서울시"})

    def test_접미만_남는_표기는_병합_대상이_아니다(self) -> None:
        # '도'·'시' 한 글자짜리 개체가 모든 '…도' 개체의 짧은 표기로 오인되면 안 된다.
        kept = apply_rules([_e("가키워드", "도", "작품"), _e("나키워드", "제주도")])
        self.assertEqual([entity.name for entity in kept], ["도", "제주도"])


class TestBuildOfficialNameIndex(unittest.TestCase):
    """공식 표기 색인 — **(타입, 몸통 표기 키) → 공식 표기**(배치가 한 번 만들어 재사용).

    왜 별 함수인가(2026-08-24 결함 ②): 예전에는 ``apply_rules`` 가 자산마다 "이번 판정 + 등록된 전체
    표기"로 대응표를 다시 만들었다. 자산 10만 × 개체 10만이면 100억 연산이다. 색인 만들기를 밖으로
    빼서 **배치 시작 1회**로 못 박는다.

    왜 키에 타입이 들어가나(결함 ①): 표기만 보면 타입이 다른 동음이의가 잘못 합쳐졌다 —
    ``[사건] 경주``(경마) 가 등록된 ``[장소] 경주시`` 로 끌려갔다. 별칭 매칭은 이미 (타입, 표기 키)
    스코프인데 접미 병합만 어긋나 있었다.
    """

    def test_타입까지_포함한_키를_돌려준다(self) -> None:
        index = build_official_name_index([("장소", "경주시")])
        self.assertEqual(dict(index), {("장소", "경주"): "경주시"})

    def test_접미가_없는_표기는_키가_되지_않는다(self) -> None:
        # 병합의 목표는 "접미가 붙은 공식형"뿐이다 — 접미 없는 표기는 아무 것도 대표하지 않는다.
        self.assertEqual(dict(build_official_name_index([("장소", "경복궁")])), {})

    def test_같은_몸통_다른_타입은_섞이지_않는다(self) -> None:
        # T018 개정 — 타입 스코프가 (타입, 몸통) 키에서 **장소만 색인**으로 좁아졌다(결함 ③).
        # 섞이지 않는다는 계약은 그대로이고, 사건 쪽은 아예 칸이 생기지 않아 더 강해졌다.
        index = build_official_name_index([("장소", "경주시"), ("사건", "경주시")])
        self.assertEqual(dict(index), {("장소", "경주"): "경주시"})

    def test_공식형이_여럿이면_긴_접미를_고른다(self) -> None:
        # 현행 관례 유지 — 더 공식적인 표기를 고른다(서울시 < 서울특별시).
        index = build_official_name_index([("장소", "서울시"), ("장소", "서울특별시")])
        self.assertEqual(index[("장소", "서울")], "서울특별시")

    def test_동점이면_표기_키_사전순이라_입력_순서에_흔들리지_않는다(self) -> None:
        # 접미 길이가 같으면 표기 키 사전순 — 같은 입력이면 늘 같은 하나(헌법 3조 결정성).
        forward = build_official_name_index([("장소", "가나시"), ("장소", "가나군")])
        backward = build_official_name_index([("장소", "가나군"), ("장소", "가나시")])
        self.assertEqual(forward[("장소", "가나")], backward[("장소", "가나")])

    def test_표기_흔들림은_정규화_키로_흡수한다(self) -> None:
        # 색인 키는 표기 키다 — '서울 특별시'로 등록돼 있어도 '서울' 판정이 찾아온다.
        index = build_official_name_index([("장소", "서울 특별시")])
        self.assertIn(("장소", normalize_text_key("서울")), index)

    def test_접미만_남는_표기는_담지_않는다(self) -> None:
        # 타입은 **장소**로 둔다 — 타입 스코프(결함 ③)가 아니라 "몸통이 없다"는 이유로 빠지는 것을
        # 검증하는 케이스이므로, 병합 대상 타입에서 확인해야 뜻이 산다.
        self.assertEqual(dict(build_official_name_index([("장소", "도")])), {})

    def test_빈_입력은_빈_색인이다(self) -> None:
        self.assertEqual(dict(build_official_name_index([])), {})

    def test_두번_만들어도_같은_색인이다(self) -> None:
        pairs = [("장소", "서울시"), ("장소", "서울특별시"), ("사건", "경주시")]
        self.assertEqual(dict(build_official_name_index(pairs)),
                         dict(build_official_name_index(pairs)))


class TestOfficialNameByBundleSize(unittest.TestCase):
    """🔴 T018 결함 ① — 공식형은 **자산이 많이 붙은 표기**로 고른다(묶음이 갈라지지 않게).

    무엇이 잘못됐었나(2026-08-24 dev 색인 실측): 규칙이 "접미가 긴 쪽"만 봤다. 그래서
    ``(장소,'제주')`` 의 공식형이 자산 **1건**짜리 '제주특별자치도' 로 정해졌는데, 정작 알찬 묶음은
    **13건**짜리 '제주도' 였다. 앞으로 '제주' 가 판정되면 13건 묶음이 아니라 1건짜리로 흘러가
    묶음이 둘로 갈라진다(강원도 2 vs 강원특별자치도 1 도 같은 상태였다).

    왜 "큰 쪽"이 정본 해석인가: 주입 색인의 확정 문구가 "기존 노드 표기 우선"이고 그 의도가
    **이미 자산이 붙은 곳으로 합류**시키는 것이다(spec §구현 확정 2). 비유하면 모임 장소다 —
    사람이 열세 명 모인 방과 한 명뿐인 방이 있으면, 늦게 온 사람은 열세 명 쪽으로 간다.
    """

    def test_묶음이_큰_표기를_공식형으로_고른다(self) -> None:
        # 실측 재현: 제주도 13건 · 제주특별자치도 1건 → 공식형은 '제주도'.
        index = build_official_name_index([("장소", "제주특별자치도", 1), ("장소", "제주도", 13)])
        self.assertEqual(index[("장소", "제주")], "제주도")

    def test_입력_순서를_바꿔도_같은_결과다(self) -> None:
        # 결정성 — 색인 재료가 어느 순서로 오든 같은 하나가 골라진다(헌법 3조).
        forward = build_official_name_index([("장소", "제주도", 13), ("장소", "제주특별자치도", 1)])
        backward = build_official_name_index([("장소", "제주특별자치도", 1), ("장소", "제주도", 13)])
        self.assertEqual(dict(forward), dict(backward))

    def test_강원도_사례도_같은_규칙이다(self) -> None:
        index = build_official_name_index([("장소", "강원특별자치도", 1), ("장소", "강원도", 2)])
        self.assertEqual(index[("장소", "강원")], "강원도")

    def test_묶음_크기가_같으면_긴_접미를_고른다(self) -> None:
        # 동점 규칙은 **현행 유지**(더 공식적인 표기) — 새 축이 옛 축을 지우지 않는다.
        index = build_official_name_index([("장소", "제주도", 3), ("장소", "제주특별자치도", 3)])
        self.assertEqual(index[("장소", "제주")], "제주특별자치도")

    def test_크기를_주지_않으면_0으로_보고_현행_규칙을_쓴다(self) -> None:
        # 폴백 — 2튜플(크기 미지정)은 전부 0 이라 동점이 되고, 그때 규칙은 긴 접미다.
        # 자산 하나 안의 판정끼리 만드는 지역 색인이 바로 이 경우다(자산 안에서는 묶음 크기가 없다).
        index = build_official_name_index([("장소", "서울시"), ("장소", "서울특별시")])
        self.assertEqual(index[("장소", "서울")], "서울특별시")

    def test_크기가_섞여_있으면_큰_쪽이_이긴다(self) -> None:
        index = build_official_name_index([("장소", "서울시", 9), ("장소", "서울특별시")])
        self.assertEqual(index[("장소", "서울")], "서울시")

    def test_음수_크기는_0으로_흡수한다(self) -> None:
        # 방어 — 조회가 어긋난 값을 줘도 순서가 뒤집히지 않는다(0 이하는 모두 "붙은 자산 없음").
        index = build_official_name_index([("장소", "서울시", -5), ("장소", "서울특별시", 0)])
        self.assertEqual(index[("장소", "서울")], "서울특별시")

    def test_크기_0끼리도_결정적이다(self) -> None:
        pairs = [("장소", "가나시", 0), ("장소", "가나군", 0)]
        self.assertEqual(dict(build_official_name_index(pairs)),
                         dict(build_official_name_index(list(reversed(pairs)))))


class TestMinBaseLength(unittest.TestCase):
    """🔴 T018 결함 ② — **1글자 몸통은 색인에 담지 않는다**(오병합 위험).

    실측 색인에 ``(장소,'독')→'독도'``·``(장소,'인')→'인도'`` 가 있었다. '독'·'인' 이 장소로
    판정되는 일은 드물지만, 한 번이라도 나오면 엉뚱한 자산이 독도·인도 묶음으로 들어간다. 얻는
    것(1글자 지명의 병합)보다 잃는 것(오병합)이 크다 — 그래서 몸통은 최소 2글자다.
    '제주'·'경주'(2글자)는 유효해야 한다.
    """

    def test_한글자_몸통은_색인에_없다(self) -> None:
        self.assertEqual(dict(build_official_name_index([("장소", "독도", 5), ("장소", "인도", 9)])),
                         {})

    def test_두글자_몸통은_유효하다(self) -> None:
        index = build_official_name_index([("장소", "제주도", 13), ("장소", "경주시", 2)])
        self.assertEqual(dict(index), {("장소", "제주"): "제주도", ("장소", "경주"): "경주시"})

    def test_한글자_판정은_흡수되지_않는다(self) -> None:
        # 결과적으로 '독'(장소) 판정은 '독' 그대로 남는다 — 별개 메타로 남는 편이 오병합보다 낫다.
        index = build_official_name_index([("장소", "독도", 5)])
        kept = apply_rules([_e("가키워드", "독")], official_index=index)
        self.assertEqual([entity.name for entity in kept], ["독"])

    def test_자산_안_병합도_한글자_몸통은_안_한다(self) -> None:
        # 지역 색인도 같은 빌더를 쓴다 — 한 자산에 '독'·'독도' 가 함께 나와도 합치지 않는다.
        kept = apply_rules([_e("가키워드", "독"), _e("나키워드", "독도")])
        self.assertEqual([entity.name for entity in kept], ["독", "독도"])


class TestSuffixMergeTypeScope(unittest.TestCase):
    """🔴 T018 결함 ③ — 접미 병합은 **장소 타입만**(``SUFFIX_MERGE_TYPES``).

    '시'·'군' 은 사람 이름·부대 이름·역사 용어의 끝 글자로도 흔하다. 실측 색인이 그 셋을 행정
    접미로 오인해 몸통 항목을 만들어 뒀다 — ``(인물,'리오넬메')``·``(조직,'영국해')``·
    ``(사건,'한사')``. 그 칸이 있으면 '리오넬 메'·'영국 해' 같은 판정이 엉뚱하게 흡수된다.

    타입 어휘가 늘어날 때는 이 상수가 확장 지점이다(예: 행정 단위를 쓰는 조직 타입이 생기면 여기).
    """

    def test_장소가_아닌_타입은_색인에_없다(self) -> None:
        known = [("인물", "리오넬 메시", 3), ("조직", "영국 해군", 2), ("사건", "한사군", 2)]
        self.assertEqual(dict(build_official_name_index(known)), {})

    def test_장소는_그대로_색인된다(self) -> None:
        known = [("인물", "리오넬 메시", 3), ("장소", "제주도", 13)]
        self.assertEqual(dict(build_official_name_index(known)), {("장소", "제주"): "제주도"})

    def test_인물_판정은_공식형으로_갈아타지_않는다(self) -> None:
        index = build_official_name_index([("인물", "리오넬 메시", 3)])
        kept = apply_rules([_e("가키워드", "리오넬 메", "인물")], official_index=index)
        self.assertEqual([(entity.name, entity.entity_type) for entity in kept],
                         [("리오넬 메", "인물")])

    def test_사건_판정도_자산_안에서_합쳐지지_않는다(self) -> None:
        # 지역 색인 경로 — 한 자산에 '한사'·'한사군'(사건)이 함께 나와도 별개 메타다.
        kept = apply_rules([_e("가키워드", "한사", "사건"), _e("나키워드", "한사군", "사건")])
        self.assertEqual([entity.name for entity in kept], ["한사", "한사군"])


class TestSuffixMergeWithOfficialIndex(unittest.TestCase):
    """③ 접미 병합의 **주입 색인** 경로 — 결함 ①② 수정 봉인(5케이스).

    현행 유지 3(자산 내 동시 등장 병합 · 같은 타입 등록 공식형 병합 2건) + 결함 수정 2(타입이 다르면
    합치지 않는다 · 색인이 없으면 표기 유지)를 한 자리에서 못 박는다.
    """

    def test_케이스1_한_자산의_두_표기는_공식형으로_합쳐진다(self) -> None:
        kept = apply_rules([_e("가키워드", "경주"), _e("나키워드", "경주시")])
        self.assertEqual([entity.name for entity in kept], ["경주시"])
        self.assertEqual(len(kept), 1)

    def test_케이스2_같은_타입의_등록_공식형과_합친다(self) -> None:
        # 증분 배치의 현실: 공식형은 지난 배치에서 노드가 됐고 이번 판정에는 짧은 표기만 온다.
        index = build_official_name_index([("장소", "경주시")])
        kept = apply_rules([_e("가키워드", "경주")], official_index=index)
        self.assertEqual([entity.name for entity in kept], ["경주시"])

    def test_케이스3_서울도_같은_규칙으로_합친다(self) -> None:
        index = build_official_name_index([("장소", "서울특별시")])
        kept = apply_rules([_e("가키워드", "서울")], official_index=index)
        self.assertEqual([entity.name for entity in kept], ["서울특별시"])

    def test_케이스4_타입이_다르면_합치지_않는다(self) -> None:
        # 🔴 결함 ① 수정 — '경주(경마·사건)'는 '경주시(장소)'와 별개 개체다. 표기만 보고 합치면
        # 동음이의가 한 묶음이 된다(실데이터 발생 0건의 잠재 결함이었다).
        index = build_official_name_index([("장소", "경주시")])
        kept = apply_rules([_e("가키워드", "경주", "사건")], official_index=index)
        self.assertEqual([(entity.name, entity.entity_type) for entity in kept],
                         [("경주", "사건")])

    def test_케이스5_색인이_없으면_입력_표기를_유지한다(self) -> None:
        # 미주입·빈 색인 둘 다 "병합은 자산 안에서만" — 기존 동작 그대로다.
        self.assertEqual([e.name for e in apply_rules([_e("가키워드", "경주")])], ["경주"])
        self.assertEqual(
            [e.name for e in apply_rules([_e("가키워드", "경주")], official_index={})], ["경주"])

    def test_주입_색인이_자산_내_공식형보다_우선한다(self) -> None:
        # 우선순위 = **기존 노드 표기 > 공식형**(spec §구현 확정 2). 등록된 '서울시'에 이미 자산이
        # 붙어 있으므로 짧은 표기는 그쪽으로 합류시킨다(묶음 응집 + 표기 1회 고정).
        index = build_official_name_index([("장소", "서울시")])
        kept = apply_rules([_e("가키워드", "서울"), _e("나키워드", "서울특별시")],
                           official_index=index)
        self.assertEqual([entity.name for entity in kept], ["서울시", "서울특별시"])

    def test_주입_색인을_훑지_않는다(self) -> None:
        # 🔴 결함 ② 수정 봉인 — 자산 루프 안에서는 dict 룩업만 한다(복사·병합도 순회다).
        index = _LookupOnlyIndex({("장소", "서울"): "서울특별시"})
        kept = apply_rules([_e("가키워드", "서울")], official_index=index)
        self.assertEqual([entity.name for entity in kept], ["서울특별시"])

    def test_옛_known_names_인자는_받지_않는다(self) -> None:
        # 하위호환 껍데기를 남기지 않는다(호출부는 코어 안뿐이라 전수 갱신했다) — 남겨 두면
        # 새 코드가 옛 계약으로 다시 O(전체 개체 수)를 부른다.
        with self.assertRaises(TypeError):
            apply_rules([_e("가키워드", "서울")], known_names=("서울특별시",))  # type: ignore[call-arg]


class TestMergeIntoSingleMeta(unittest.TestCase):
    """여러 키워드가 한 개체로 접히는 것 — 이 축의 핵심 효과(검증 §1: 제주도 6→14건)."""

    def test_열세개_키워드가_한_개체가_된다(self) -> None:
        # 유형 재현: 서로 다른 키워드 13개가 모두 같은 표준표기로 판정되면 개체는 하나다
        # (자산→개체 엣지도 하나 — 유니크 (src,dst,kind) · spec §4).
        judged = [_e(f"가키워드{index}", "제주도") for index in range(13)]
        kept = apply_rules(judged)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].name, "제주도")

    def test_대표_키워드는_입력_순서의_첫번째다(self) -> None:
        # reason 스탬프의 ``kw=`` 는 한 칸이다 — 무엇을 남길지 결정적이어야 한다.
        kept = apply_rules([_e("가키워드", "제주도"), _e("나키워드", "제주 도")])
        self.assertEqual([entity.keyword for entity in kept], ["가키워드"])

    def test_표기가_같아도_타입이_다르면_별개다(self) -> None:
        # 동음이의 분리는 **의도**다(spec §4 — 유니크 키가 (type, uid)).
        kept = apply_rules([_e("가키워드", "가온", "인물"), _e("나키워드", "가온", "조직")])
        self.assertEqual(len(kept), 2)


class TestPurityAndDeterminism(unittest.TestCase):
    """순수 함수로서의 성질 — 같은 입력이면 같은 출력, 입력은 건드리지 않는다."""

    def test_두번_호출해도_같은_결과다(self) -> None:
        judged = [
            _e("올림픽 정식 종목", "올림픽", "사건"),
            _e("가키워드", "대한민국"),
            _e("나키워드", "서울"),
            _e("다키워드", "서울특별시"),
            _e("라키워드", "제주도"),
        ]
        self.assertEqual(apply_rules(judged), apply_rules(judged))

    def test_결과에_적용_순서가_드러난다(self) -> None:
        # 제외 → 스톱 → 병합 3단을 한 번에 통과시킨 결과(살아남는 것은 병합된 공식형과 제주도).
        # 🔴 제외 예시를 `북극` 으로 바꿨다(spec 087 T005) — `대한민국` 은 정의문 소관이 됐고,
        #    이 테스트가 보는 것은 **규칙 3단의 순서**이므로 규칙에 남은 항목으로 확인한다.
        judged = [
            _e("가키워드", "북극"),
            _e("올림픽 정식 종목", "올림픽", "사건"),
            _e("나키워드", "서울"),
            _e("다키워드", "서울특별시"),
            _e("라키워드", "제주도"),
        ]
        self.assertEqual([entity.name for entity in apply_rules(judged)],
                         ["서울특별시", "제주도"])

    def test_입력_목록을_변형하지_않는다(self) -> None:
        judged = [_e("가키워드", "서울"), _e("나키워드", "북극")]
        before = list(judged)
        apply_rules(judged)
        self.assertEqual(judged, before)

    def test_빈_입력은_빈_결과다(self) -> None:
        self.assertEqual(apply_rules([]), ())

    def test_반환값은_튜플이다(self) -> None:
        # 호출부가 실수로 담아 두고 고치는 일을 막는다(판정 결과는 사실이다).
        self.assertIsInstance(apply_rules([_e("가키워드", "제주도")]), tuple)


if __name__ == "__main__":
    unittest.main()
