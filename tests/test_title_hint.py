"""spec 114 — 요약에 쓸 제목(``summary_title``) · 제목 블록 · ``title_match`` 해석 단위 테스트(순수 · LLM/DB 불필요).

무엇을 봉인하나
① 뜻 없는 · 일반어뿐인 이름은 제목으로 쓰지 않는다 — 「새 녹음 3」 같은 이름이 실리면 빈 STT 음성에
   「새 녹음 3이라는 녹음 파일」 같은 쓸모없는 요약이 생긴다.
② 해시태그 · 이모지 · 장식 기호는 걷는다 — 해시태그를 작품명에 붙여 없는 작품을 만들지 않게. 단 제목 전체가 해시태그면 「#」만 뗀다(「#살아있다」는 영화 제목).
③ 제목 블록은 「같은 호출 안에서 제목이 맞는지 판단」 · 「yes 일 때만 반영」을 지시한다 — 틀린 제목 차단의 근거.
④ ``title_match`` 는 yes · no · unsure 셋으로만 — 그 밖의 값은 unsure(제목을 쓰지 않는 쪽).
⑤ 자모로 풀린(NFD) 파일명도 같은 제목으로 — macOS 에서 만든 파일명이 조용히 제목을 잃지 않게.
"""

from __future__ import annotations

import unicodedata
import unittest

from src.config.filename_util import summary_title
from src.llm.title_hint import (
    TITLE_HINT_VERSION,
    build_title_block,
    build_title_only_prompt,
    insert_title_block,
    parse_title_match,
)


class SummaryTitleTest(unittest.TestCase):
    def test_뜻_없는_이름은_None(self) -> None:
        for name in ("1612816.jpg", "IMG_4821.jpg", "무제.mp4", "untitled.png", "DSC00123.JPG", ""):
            with self.subTest(name=name):
                self.assertIsNone(summary_title(name))

    def test_일반어뿐인_이름도_None(self) -> None:
        for name in ("새 녹음 3.m4a", "녹음.m4a", "최종본.docx", "final_v2.mp4", "영상.mp4", "다운로드.jpg",
                     "음성 메모 12.m4a", "복사본 (2).txt", "새 문서.hwp", "사진 원본.jpg"):
            with self.subTest(name=name):
                self.assertIsNone(summary_title(name))

    def test_뜻_있는_제목은_확장자를_떼고_그대로(self) -> None:
        self.assertEqual("천마총 관모_02", summary_title("천마총 관모_02.jpg"))
        self.assertEqual("[온스테이지2.0] 신지훈 - 목련 필 무렵", summary_title("[온스테이지2.0] 신지훈 - 목련 필 무렵.mp3"))

    def test_해시태그_이모지_장식기호는_걷는다(self) -> None:
        self.assertEqual("트랩 2화", summary_title("트랩 2화 #거기를_왜들어가 #이서진.mp4"))
        self.assertEqual("역대급 미스터리스릴러 자백의 대가", summary_title("🔥역대급 미스터리스릴러★ 자백의 대가.mp4"))

    def test_제목_전체가_해시태그면_샵만_뗀다(self) -> None:
        self.assertEqual("살아있다", summary_title("#살아있다.jpg"))

    def test_자산id_접두는_벗긴다(self) -> None:
        self.assertEqual("서울 숭례문", summary_title("018f0000-0000-7000-8000-000000000277__서울 숭례문.txt"))

    def test_자모로_풀린_파일명도_같은_제목(self) -> None:
        # macOS 파일명은 NFD 로 저장되기도 한다 — 풀린 꼴이라고 제목을 잃거나 풀린 꼴로 넘기지 않는다.
        nfd = unicodedata.normalize("NFD", "천마총 관모_02.jpg")
        self.assertNotEqual("천마총 관모_02.jpg", nfd)
        self.assertEqual("천마총 관모_02", summary_title(nfd))
        self.assertIsNone(summary_title(unicodedata.normalize("NFD", "새 녹음 3.m4a")))

    def test_너무_긴_제목은_자른다(self) -> None:
        t = summary_title("가" * 10 + " " + "나" * 300 + ".txt")
        self.assertIsNotNone(t)
        self.assertLessEqual(len(t), 150)


class TitleBlockTest(unittest.TestCase):
    def test_블록에_제목과_판단_지시가_있다(self) -> None:
        b = build_title_block("천마총 관모", keep_appearance=False)
        self.assertIn("천마총 관모", b)
        self.assertIn('"title_match"', b)
        for word in ("yes", "no", "unsure"):
            self.assertIn(word, b)
        self.assertIn("yes 일 때만", b)
        self.assertNotIn("외형", b)

    def test_사진_영상은_외형_묘사_유지_줄이_붙는다(self) -> None:
        b = build_title_block("천마총 관모", keep_appearance=True)
        self.assertIn("외형", b)

    def test_블록_끼우기는_표지_앞에_한_번(self) -> None:
        p = "형식...\n텍스트:\n본문"
        out = insert_title_block(p, "\n텍스트:", "BLOCK")
        self.assertEqual("형식...BLOCK\n텍스트:\n본문", out)

    def test_표지가_없으면_끝에_붙인다(self) -> None:
        self.assertEqual("abcBLOCK", insert_title_block("abc", "\n없는표지", "BLOCK"))

    def test_제목만_요약_프롬프트(self) -> None:
        p = build_title_only_prompt("심수봉 - 부부행진곡", summary_max_chars=1000, top_k_keywords=5)
        self.assertIn("심수봉 - 부부행진곡", p)
        self.assertIn("제목에 없는 내용", p)

    def test_판_스탬프(self) -> None:
        self.assertTrue(TITLE_HINT_VERSION.startswith("title_hint.v"))


class ParseTitleMatchTest(unittest.TestCase):
    def test_세_값만_통과(self) -> None:
        self.assertEqual("yes", parse_title_match("YES"))
        self.assertEqual("no", parse_title_match(" no "))
        self.assertEqual("unsure", parse_title_match("unsure"))

    def test_그_밖의_값은_unsure(self) -> None:
        for v in (None, "", "maybe", 1, ["yes"]):
            with self.subTest(v=v):
                self.assertEqual("unsure", parse_title_match(v))


if __name__ == "__main__":
    unittest.main()
