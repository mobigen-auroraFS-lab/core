"""045 Phase B — filter_kw·filter_date 파생 필드 단위."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from src.search.filter_index_fields import (
    build_filter_index_fields,
    derive_file_ext,
    derive_source_dataset,
)


class DeriveFileExtTest(unittest.TestCase):
    def test_last_extension_lowercase(self) -> None:
        self.assertEqual(derive_file_ext("/data/a.stt.txt"), "txt")

    def test_no_extension(self) -> None:
        self.assertIsNone(derive_file_ext("/data/noext"))
        self.assertIsNone(derive_file_ext("/data/trailing."))


class DeriveSourceDatasetTest(unittest.TestCase):
    def test_sample_data_buckets(self) -> None:
        self.assertEqual(derive_source_dataset("/x/sample_data/data2/foo.mp4"), "data2")

    def test_wikipedia_youtube(self) -> None:
        self.assertEqual(derive_source_dataset("/corpus/wikipedia/article.txt"), "wikipedia")
        self.assertEqual(derive_source_dataset("/yt/youtube/abc.mp4"), "youtube")

    def test_unknown(self) -> None:
        self.assertEqual(derive_source_dataset("/random/path/file.pdf"), "unknown")


class BuildFilterIndexFieldsTest(unittest.TestCase):
    def test_full_shape(self) -> None:
        out = build_filter_index_fields(
            fs_path="/sample_data/data1/doc.pdf",
            created_at=datetime(2026, 3, 15, 12, 0, 0),
        )
        self.assertEqual(
            out,
            {
                "filter_kw": {"file_ext": "pdf", "source_dataset": "data1"},
                "filter_date": {"created_at": "2026-03-15"},
                # 096 — 표시명은 늘 실린다(경로만 있으면 뽑을 수 있다). 크기·수정일은 값이 없으면 생략.
                "file_name_sort": "doc.pdf",
            },
        )

    def test_date_only_created_at(self) -> None:
        out = build_filter_index_fields(fs_path="/x/y.txt", created_at=date(2025, 12, 1))
        self.assertEqual(out["filter_date"], {"created_at": "2025-12-01"})

    def test_sort_fields(self) -> None:
        # 096 정렬 — 표에 찍는 값(표시명·크기·수정일)이 색인에 실려야 그것으로 줄을 세울 수 있다.
        out = build_filter_index_fields(
            fs_path="/x/018f0000-0000-7000-8000-000000000001__보고서 최종.pdf",
            created_at=datetime(2026, 3, 15), updated_at="2026-08-25T23:25:02.5+00:00",
            file_size=2048,
        )
        # 🔴 화면에 보이는 그 문자열이어야 한다 — 자산 id 접두는 벗기고 확장자는 남긴다.
        self.assertEqual(out["file_name_sort"], "보고서 최종.pdf")
        self.assertEqual(out["file_size"], 2048)
        self.assertEqual(out["filter_date"], {"created_at": "2026-03-15", "updated_at": "2026-08-25"})

    def test_missing_sort_values_are_omitted(self) -> None:
        # 0 으로 채우면 "모른다"와 "0바이트"가 같아진다(빈 값 생략 관례 · 083 keywords_norm 과 같은 결).
        out = build_filter_index_fields(fs_path="/x/y.txt")
        self.assertNotIn("file_size", out)
        self.assertNotIn("filter_date", out)
        self.assertNotIn("file_size", build_filter_index_fields(fs_path="/x/y.txt", file_size=None))
        # 불리언은 크기가 아니다(파이썬에서 True 는 int 이므로 명시로 막는다).
        self.assertNotIn("file_size", build_filter_index_fields(fs_path="/x/y.txt", file_size=True))


if __name__ == "__main__":
    unittest.main()
