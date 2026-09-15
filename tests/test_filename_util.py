"""filename_util(코어) 단위 테스트 — basename 추출·asset_id 프리픽스 제거·표시명 산출.

077 레포 분리 배경: 표시 유틸(``strip_asset_id_prefix``·``display_file_name``)을 코어(config)로
승격하면서 그 검증 테스트(``test_display_file_name``)는 백엔드 레포로 이관됐다. 이 함수들은 이제
코어 자산이므로 **코어 레포에서 직접** 검증한다(순수·표준 라이브러리만·DB/OS 불필요).
"""
from __future__ import annotations

import unittest

from src.config.filename_util import (
    basename_of,
    display_file_name,
    meaningful_file_name,
    strip_asset_id_prefix,
)

# 실제 UUIDv7 형태 asset_id(8-4-4-4-12 hex) — 프리픽스 매칭 검증용.
_AID = "018f0000-0000-7000-8000-000000000277"


class BasenameOfTest(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(basename_of("/data/archive/report.pdf"), "report.pdf")

    def test_backslash_normalized(self):
        # 윈도우 경로 백슬래시도 마지막 세그먼트를 취한다.
        self.assertEqual(basename_of(r"C:\docs\a.txt"), "a.txt")

    def test_trailing_slash_stripped(self):
        self.assertEqual(basename_of("/data/dir/"), "dir")

    def test_query_and_fragment_removed(self):
        self.assertEqual(basename_of("https://h/v/clip.mp4?t=3#x"), "clip.mp4")

    def test_empty_returns_empty(self):
        self.assertEqual(basename_of(""), "")

    def test_query_only_falls_back_to_tail(self):
        # 쿼리만 남아 앞이 비면 마지막 세그먼트로 폴백(기존 3벌 공통 ``or tail``).
        self.assertEqual(basename_of("http://h/?q=1"), "?q=1")

    def test_local_path_hash_is_literal(self):
        # 🔴 회귀 방지(2026-08-24 실사고): 유튜브 수집 파일명의 해시태그(#)를 URI 프래그먼트로
        # 오인해 잘라내면, 재색인 시 file_name 이 통째로 비어 BM25 신호가 사라진다(9자산 실측).
        # 로컬 경로(스킴 없음)의 # 과 ? 는 파일명 문자 그대로다.
        self.assertEqual(
            basename_of("/inbox/#식혜#전통식혜 (식혜).mp3"), "#식혜#전통식혜 (식혜).mp3"
        )

    def test_local_path_question_is_literal(self):
        self.assertEqual(basename_of("/inbox/뭐지?.txt"), "뭐지?.txt")

    def test_url_fragment_still_removed(self):
        # URL(스킴 있음)에서는 종전대로 쿼리·프래그먼트를 제거한다.
        self.assertEqual(basename_of("file:///v/a.mp4#frag"), "a.mp4")

    def test_asset_id_prefix_not_stripped(self):
        # basename_of 는 표시용 strip 을 하지 않는다(색인·원본 경로 보존).
        self.assertEqual(basename_of(f"/a/{_AID}__orig.txt"), f"{_AID}__orig.txt")


class StripAssetIdPrefixTest(unittest.TestCase):
    def test_prefix_removed(self):
        self.assertEqual(strip_asset_id_prefix(f"{_AID}__orig.txt"), "orig.txt")

    def test_no_prefix_unchanged(self):
        self.assertEqual(strip_asset_id_prefix("orig.txt"), "orig.txt")

    def test_double_underscore_in_name_preserved(self):
        # 맨 앞이 UUID 형태가 아니면 ``__`` 가 있어도 건드리지 않는다.
        self.assertEqual(strip_asset_id_prefix("my__file.txt"), "my__file.txt")

    def test_empty_and_none(self):
        self.assertEqual(strip_asset_id_prefix(""), "")
        self.assertEqual(strip_asset_id_prefix(None), "")


class DisplayFileNameTest(unittest.TestCase):
    def test_path_with_prefix_returns_original(self):
        self.assertEqual(display_file_name(f"/data/{_AID}__orig.txt"), "orig.txt")

    def test_plain_path(self):
        self.assertEqual(display_file_name("/data/orig.txt"), "orig.txt")

    def test_none_and_empty_return_empty(self):
        self.assertEqual(display_file_name(None), "")
        self.assertEqual(display_file_name(""), "")


if __name__ == "__main__":
    unittest.main()


class MeaningfulFileNameTest(unittest.TestCase):
    """판정 재료용 파일 이름 게이트 — 뜻 없는 이름은 아예 싣지 않는다(2026-09-11).

    사용자 요구: *"파일명과 상위 폴더명이 의미가 없는 경우 이게 실제 판정에 큰영향을 주지 않도록"*.
    LLM 에게 묻지 않고 **코드가 먼저** 정하므로 여기서 봉인한다.
    """

    def test_숫자뿐인_출처_일련번호는_싣지_않는다(self) -> None:
        # 국보 사진 1,083장이 전부 이 꼴이다(국가유산청 이미지 API 가 붙인 번호).
        for name in ("1612816.jpg", "2021042017434700.JPG", "2584288.jpg"):
            with self.subTest(name=name):
                self.assertIsNone(meaningful_file_name(name))

    def test_기기_앱_상투_이름도_싣지_않는다(self) -> None:
        for name in ("IMG_4821.jpg", "DSC00123.JPG", "KakaoTalk_20240315_123456.jpg",
                     "Screenshot 2024-03-15 at 14.22.31.png"):
            with self.subTest(name=name):
                self.assertIsNone(meaningful_file_name(name))

    def test_뜻_있는_이름은_확장자만_떼어_돌려준다(self) -> None:
        self.assertEqual(meaningful_file_name("서울 숭례문.txt"), "서울 숭례문")
        self.assertEqual(meaningful_file_name("/d/2007 NIGHT of FGI 윤석화 인터뷰.jpg"),
                         "2007 NIGHT of FGI 윤석화 인터뷰")

    def test_자산id_접두는_벗기고_본다(self) -> None:
        # 보관 경로는 `<asset_id>__<원본명>` 이라 접두를 남기면 판정 재료가 지저분해진다.
        got = meaningful_file_name("/a/018f0000-0000-7000-8000-0000000000a1__아내 얘기.mp3")
        self.assertEqual(got, "아내 얘기")

    def test_빈_값은_None(self) -> None:
        for bad in (None, "", "   ", "/a/b/"):
            with self.subTest(bad=bad):
                self.assertIsNone(meaningful_file_name(bad))
