"""099 T029 — 수집 장부 → 검색 골든 변환의 **순수 로직** 단위 테스트.

빌더(``scripts/build_golden_manifest.py``)는 두 가지를 한다:
  - **순수 변환**: 수집 장부 dict → 질의·정답 파일 목록(여기서 전부 덮는다 · DB·파일 불요).
  - **해소·기록**: 파일 basename 을 실 DB 의 ``asset_id`` 로 바꿔 JSON 으로 쓴다(실 DB 필요 · 사람 실행).

왜 장부인가: 예전 골든은 정답을 **파일명 주제표식**에서 뽑았는데 현 코퍼스에서 96.8% 가 퇴화해
자산마다 고유 토픽이 됐다(spec 099 G0/T002). 장부는 수집 시점 기록(국가유산청 공개 API·공식
유튜브 채널)이라 **파이프라인 산출물이 아니고**, 그래서 자기 결과로 자기를 채점하는 순환이 없다.

무엇을 봉인하나:
  - ``manifest_entries``: 파일 없는 item 제외 · **같은 이름은 한 질의로 병합**(국보 item 과 그
    영상 item 이 같은 이름을 쓴다 — 나누면 같은 질의를 두 번 재고 정답이 쪼개진다) ·
    파일 중복 제거 · 정렬로 **2회 실행 동일**(헌법 3조).
  - ``resolve_queries``: basename → 자산 id 해소 · 같은 basename 이 자산 둘이면 둘 다 정답 ·
    못 찾은 파일은 **버리지 않고 보고서에 남긴다**(장부에만 있는 92건의 사유 추적) ·
    정답이 하나도 없는 질의는 채점 불가라 제외하고 그 사실을 기록.
  - ``build_golden_doc``: ``measure_search_golden.py`` 가 읽는 계약(``queries[].{id,query,category,
    topics,relevant}``)을 그대로 낸다 — 형식이 어긋나면 채점이 아예 안 된다.
"""

from __future__ import annotations

import unittest

from scripts.build_golden_manifest import (
    asset_basename,
    build_golden_doc,
    corpus_only_basenames,
    explain_unmatched,
    manifest_entries,
    resolve_queries,
)


def _manifest(items: list[dict], downloads: dict | None = None) -> dict:
    """테스트용 최소 장부 dict 를 만든다.

    Args:
        items: ``items[]`` 에 넣을 item dict 목록.
        downloads: ``downloads`` 에 넣을 dict. ``None`` 이면 빈 dict(내려받기 기록 없음).

    Returns:
        빌더가 받는 모양의 장부 dict.
    """
    return {
        "collected_at": "2026-09-11", "source": "테스트",
        "items": items, "downloads": downloads or {},
    }


class TestAssetBasename(unittest.TestCase):
    """DB ``fs_path`` → 장부와 대조할 원본 파일명."""

    def test_uuid_접두를_벗긴다(self) -> None:
        # 적재 시 파일명이 `<uuid>__<원본명>` 으로 바뀐다 — 장부에는 원본명만 있다.
        self.assertEqual(
            asset_basename("/a/b/018f0000-0000-7000-8000-000000000001__순천 동화사.txt"),
            "순천 동화사.txt",
        )

    def test_접두가_없으면_파일명_그대로(self) -> None:
        self.assertEqual(asset_basename("/a/b/홍길동.mp4"), "홍길동.mp4")

    def test_원본명에_밑줄_둘이_또_있어도_첫_경계만_자른다(self) -> None:
        # 원본 제목에 `__` 가 들어 있을 수 있다 — 첫 경계만 접두 구분자다.
        self.assertEqual(asset_basename("/a/uuid__제목__부제.mp4"), "제목__부제.mp4")


class TestManifestEntries(unittest.TestCase):
    """장부 → 질의 후보 변환 규칙(순수)."""

    def test_파일_없는_item_은_제외한다(self) -> None:
        # "공식 채널 영상 없음" item — 정답이 없어 채점할 수 없다. ⚠️ no-match 질의로 쓰면 안 된다
        # (그 이름의 자산이 코퍼스에 없다는 뜻이 아니라 이 수집분에서 못 받았다는 뜻일 뿐이다).
        m = _manifest([
            {"name": "홍길동", "kind": "영상", "text_file": None, "images": [], "key": "홍길동"},
            {"name": "서울 숭례문", "kind": "국보", "text_file": "서울 숭례문.txt", "images": [], "key": "k1"},
        ])
        entries = manifest_entries(m)
        self.assertEqual([e["query"] for e in entries], ["서울 숭례문"])

    def test_본문과_이미지가_모두_정답_파일이_된다(self) -> None:
        m = _manifest([
            {"name": "서울 숭례문", "kind": "국보", "key": "k1",
             "text_file": "서울 숭례문.txt", "images": ["a.jpg", "b.jpg"]},
        ])
        (entry,) = manifest_entries(m)
        self.assertEqual(entry["files"], ["a.jpg", "b.jpg", "서울 숭례문.txt"])

    def test_같은_이름_item_은_한_질의로_병합한다(self) -> None:
        # 국보 item 과 같은 이름의 영상 item — 질의 문자열이 같으므로 한 질의로 합치고
        # 정답은 합집합. 나누면 같은 질의를 두 번 재게 되고 각 질의의 정답이 반쪽이 된다.
        m = _manifest([
            {"name": "서울 숭례문", "kind": "국보", "key": "k1",
             "text_file": "서울 숭례문.txt", "images": ["a.jpg"]},
            {"name": "서울 숭례문", "kind": "영상", "key": "서울 숭례문",
             "text_file": None, "images": ["숭례문, 다시 서다.mp4"]},
        ])
        entries = manifest_entries(m)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["files"], ["a.jpg", "서울 숭례문.txt", "숭례문, 다시 서다.mp4"])
        self.assertEqual(entries[0]["kinds"], ["국보", "영상"])
        self.assertEqual(entries[0]["keys"], ["k1", "서울 숭례문"])

    def test_중복_파일은_한_번만_넣는다(self) -> None:
        m = _manifest([
            {"name": "탑", "kind": "국보", "key": "k1", "text_file": "탑.txt", "images": ["a.jpg", "a.jpg"]},
            {"name": "탑", "kind": "보물", "key": "k2", "text_file": "탑.txt", "images": ["a.jpg"]},
        ])
        (entry,) = manifest_entries(m)
        self.assertEqual(entry["files"], ["a.jpg", "탑.txt"])

    def test_이름순_정렬과_연번_id(self) -> None:
        m = _manifest([
            {"name": "나", "kind": "보물", "key": "k2", "text_file": "나.txt", "images": []},
            {"name": "가", "kind": "국보", "key": "k1", "text_file": "가.txt", "images": []},
        ])
        entries = manifest_entries(m)
        self.assertEqual([(e["id"], e["query"]) for e in entries], [("M0001", "가"), ("M0002", "나")])

    def test_이름이_비면_제외한다(self) -> None:
        m = _manifest([
            {"name": "  ", "kind": "보물", "key": "k1", "text_file": "x.txt", "images": []},
            {"name": "가", "kind": "국보", "key": "k2", "text_file": "가.txt", "images": []},
        ])
        self.assertEqual([e["query"] for e in manifest_entries(m)], ["가"])

    def test_두_번_돌려도_같은_결과(self) -> None:
        # 헌법 3조 — 같은 장부면 2회 실행 동일(집합 순회 순서가 새지 않는다).
        m = _manifest([
            {"name": "나", "kind": "보물", "key": "k2", "text_file": "나.txt", "images": ["b.jpg", "a.jpg"]},
            {"name": "가", "kind": "국보", "key": "k1", "text_file": "가.txt", "images": []},
        ])
        self.assertEqual(manifest_entries(m), manifest_entries(m))


class TestManifestEntriesWithDownloads(unittest.TestCase):
    """``downloads`` 합산(선택) — 기본은 꺼져 있다.

    왜 선택인가: 099 T029 가 지정한 정답원은 ``items[]`` 다. 그런데 장부의 ``downloads``(2,777건)도
    같은 수집 기록이고 **코퍼스에 실제로 적재된 파일**이라, 빼면 DB 자산 2,489건이 어느 질의의
    정답도 아닌 상태가 된다(실측). 기본 동작은 지시대로 두고 **켤 수 있게만** 해 둔다.
    """

    def test_기본은_다운로드를_쓰지_않는다(self) -> None:
        m = _manifest(
            [{"name": "홍길동", "kind": "영상", "key": "홍길동", "text_file": None, "images": []}],
            {"abc:video": {"file": "actor/인터뷰 홍길동.mp4", "name": "홍길동", "part": "actor"}},
        )
        self.assertEqual(manifest_entries(m), [])

    def test_켜면_이름이_같은_질의에_합쳐진다(self) -> None:
        m = _manifest(
            [{"name": "서울 숭례문", "kind": "국보", "key": "k1",
              "text_file": "서울 숭례문.txt", "images": []}],
            {"abc:video": {"file": "drama/숭례문 다큐.mp4", "name": "서울 숭례문", "part": "drama"}},
        )
        (entry,) = manifest_entries(m, include_downloads=True)
        self.assertEqual(entry["files"], ["서울 숭례문.txt", "숭례문 다큐.mp4"])
        self.assertEqual(entry["kinds"], ["국보", "다운로드:drama"])

    def test_켜면_파일_없던_item_도_질의가_된다(self) -> None:
        # "공식 채널 영상 없음" 으로 비어 있던 item 이 내려받기 기록으로 정답을 얻는다.
        m = _manifest(
            [{"name": "홍길동", "kind": "영상", "key": "홍길동", "text_file": None, "images": []}],
            {"abc:video": {"file": "actor/인터뷰 홍길동.mp4", "name": "홍길동", "part": "actor"},
             "abc:audio": {"file": "actor/인터뷰 홍길동.mp3", "name": "홍길동", "part": "actor"}},
        )
        (entry,) = manifest_entries(m, include_downloads=True)
        self.assertEqual(entry["query"], "홍길동")
        self.assertEqual(entry["files"], ["인터뷰 홍길동.mp3", "인터뷰 홍길동.mp4"])
        self.assertEqual(entry["keys"], ["abc:audio", "abc:video"])

    def test_파일_경로에서_파일명만_취한다(self) -> None:
        # downloads[].file 은 `<part>/<파일명>` 상대경로다 — DB 대조는 파일명으로 한다.
        m = _manifest(
            [],
            {"abc:image": {"file": "singer/가수 공연.jpg", "name": "가수", "part": "singer"}},
        )
        (entry,) = manifest_entries(m, include_downloads=True)
        self.assertEqual(entry["files"], ["가수 공연.jpg"])

    def test_이름이_없는_내려받기는_건너뛴다(self) -> None:
        m = _manifest([], {"abc:video": {"file": "actor/x.mp4", "name": "", "part": "actor"}})
        self.assertEqual(manifest_entries(m, include_downloads=True), [])


class TestResolveQueries(unittest.TestCase):
    """파일 basename → 자산 id 해소 규칙(순수)."""

    def test_정답은_자산_id_로_바뀌고_정렬된다(self) -> None:
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보"], "keys": ["k1"],
                    "files": ["a.jpg", "탑.txt"]}]
        index = {"a.jpg": ["id-b"], "탑.txt": ["id-a"]}
        queries, report = resolve_queries(entries, index)
        self.assertEqual(queries[0]["relevant"], ["id-a", "id-b"])
        self.assertEqual(queries[0]["category"], "present")
        self.assertNotIn("expect_empty", queries[0])
        self.assertEqual(report["files_matched"], 2)

    def test_같은_basename_이_자산_둘이면_둘_다_정답(self) -> None:
        # 국보/보물 폴더에 같은 이름의 해설문이 있어 DB 에 자산이 둘 생긴다(장부 실측 80건).
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보", "보물"], "keys": ["k1", "k2"],
                    "files": ["탑.txt"]}]
        queries, _ = resolve_queries(entries, {"탑.txt": ["id-b", "id-a"]})
        self.assertEqual(queries[0]["relevant"], ["id-a", "id-b"])

    def test_못_찾은_파일은_보고서에_남는다(self) -> None:
        # 장부에만 있는 파일(수집 후 코퍼스에서 빠진 것) — 조용히 버리면 사유를 추적할 수 없다.
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보"], "keys": ["k1"],
                    "files": ["없는파일.jpg", "탑.txt"]}]
        queries, report = resolve_queries(entries, {"탑.txt": ["id-a"]})
        self.assertEqual(queries[0]["relevant"], ["id-a"])
        self.assertEqual(report["unmatched"], [{"id": "M0001", "query": "탑", "file": "없는파일.jpg"}])
        self.assertEqual(report["files_unmatched"], 1)

    def test_정답이_하나도_없으면_질의를_뺀다(self) -> None:
        # 정답 0 인 질의는 재현율 분모가 0 이라 채점 자체가 불가능하다. 뺀 사실은 기록한다.
        entries = [
            {"id": "M0001", "query": "없다", "kinds": ["영상"], "keys": ["k0"], "files": ["x.mp4"]},
            {"id": "M0002", "query": "탑", "kinds": ["국보"], "keys": ["k1"], "files": ["탑.txt"]},
        ]
        queries, report = resolve_queries(entries, {"탑.txt": ["id-a"]})
        self.assertEqual([q["id"] for q in queries], ["M0002"])
        self.assertEqual(report["dropped"], [{"id": "M0001", "query": "없다"}])

    def test_토픽_태그는_파일명_토픽키로_채운다(self) -> None:
        # 추적용 태그. ⚠️ 099 T031 이후 **정합 가드는 이 태그를 쓰지 않는다** —
        # 가드 기준이 자산 id 커버리지(golden_guard.uncovered_assets)로 바뀌었다.
        # 태그 자체는 옛 골든 도구 호환·사람 눈 확인용으로 계속 채운다(계약 유지).
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보"], "keys": ["k1"],
                    "files": ["youtube_사막_3bTA2c2n2QI.jpg", "탑.txt"]}]
        queries, _ = resolve_queries(entries, {"youtube_사막_3bTA2c2n2QI.jpg": ["id-b"], "탑.txt": ["id-a"]})
        self.assertEqual(queries[0]["topics"], ["사막", "탑"])

    def test_두_번_돌려도_같은_결과(self) -> None:
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보"], "keys": ["k1"],
                    "files": ["a.jpg", "탑.txt"]}]
        index = {"a.jpg": ["id-b"], "탑.txt": ["id-a"]}
        self.assertEqual(resolve_queries(entries, index), resolve_queries(entries, index))


class TestExplainUnmatched(unittest.TestCase):
    """장부에는 있는데 정답이 못 된 파일의 **사유**(T029 요구 — 조용히 버리지 않는다)."""

    def test_비등록_상태면_그_상태를_사유로_적는다(self) -> None:
        # 적재는 됐으나 status 가 registered 가 아니면 색인 대상이 아니라 정답이 될 수 없다.
        rows = [{"id": "M0001", "query": "탑", "file": "a.jpg"}]
        out = explain_unmatched(rows, {"a.jpg": ["deferred"]})
        self.assertEqual(out[0]["reason"], "비등록(deferred)")

    def test_상태가_여럿이면_모두_적는다(self) -> None:
        rows = [{"id": "M0001", "query": "탑", "file": "a.jpg"}]
        out = explain_unmatched(rows, {"a.jpg": ["failed", "deferred"]})
        self.assertEqual(out[0]["reason"], "비등록(deferred·failed)")

    def test_DB_에_아예_없으면_미적재로_적는다(self) -> None:
        rows = [{"id": "M0001", "query": "탑", "file": "a.jpg"}]
        out = explain_unmatched(rows, {})
        self.assertEqual(out[0]["reason"], "DB 미적재")

    def test_원래_필드를_지우지_않는다(self) -> None:
        rows = [{"id": "M0001", "query": "탑", "file": "a.jpg"}]
        out = explain_unmatched(rows, {})
        self.assertEqual(out[0]["id"], "M0001")
        self.assertEqual(out[0]["query"], "탑")
        self.assertEqual(out[0]["file"], "a.jpg")


class TestCorpusOnlyBasenames(unittest.TestCase):
    """DB 에만 있고 장부에 없는 파일 — 골든이 덮지 못하는 자산(사유 로그용)."""

    def test_장부에_없는_basename_만_정렬해_돌려준다(self) -> None:
        entries = [{"id": "M0001", "query": "탑", "kinds": ["국보"], "keys": ["k1"], "files": ["탑.txt"]}]
        index = {"탑.txt": ["id-a"], "b.jpg": ["id-b"], "a.jpg": ["id-c"]}
        self.assertEqual(corpus_only_basenames(entries, index), ["a.jpg", "b.jpg"])


class TestBuildGoldenDoc(unittest.TestCase):
    """measure_search_golden.py 가 읽는 계약."""

    def test_하니스_계약_필드를_갖춘다(self) -> None:
        queries = [{"id": "M0001", "query": "탑", "category": "present", "topics": ["탑"],
                    "relevant": ["id-a"], "kinds": ["국보"], "keys": ["k1"], "files": ["탑.txt"]}]
        doc = build_golden_doc(queries, report={"files_matched": 1, "files_unmatched": 0,
                                                "unmatched": [], "dropped": []},
                               corpus_only=["a.jpg"])
        self.assertIn("version", doc)
        self.assertIn("provenance", doc)
        self.assertEqual(doc["queries"], queries)
        # 하니스는 relevant 있는 질의만 채점하고 expect_empty 를 no-match 로 센다 — 여기엔 없어야 한다.
        self.assertTrue(all(q.get("relevant") for q in doc["queries"]))
        self.assertFalse(any(q.get("expect_empty") for q in doc["queries"]))

    def test_통계에_질의수와_정답분포가_들어간다(self) -> None:
        queries = [
            {"id": "M0001", "query": "가", "category": "present", "topics": ["가"],
             "relevant": ["a"], "kinds": ["국보"], "keys": ["k1"], "files": ["가.txt"]},
            {"id": "M0002", "query": "나", "category": "present", "topics": ["나"],
             "relevant": ["b", "c", "d"], "kinds": ["보물"], "keys": ["k2"], "files": ["나.txt"]},
        ]
        doc = build_golden_doc(queries, report={"files_matched": 4, "files_unmatched": 0,
                                                "unmatched": [], "dropped": []},
                               corpus_only=[])
        stats = doc["stats"]
        self.assertEqual(stats["queries"], 2)
        self.assertEqual(stats["relevant_total"], 4)
        self.assertEqual(stats["relevant_avg"], 2.0)
        self.assertEqual(stats["relevant_median"], 2.0)
        self.assertEqual(stats["relevant_min"], 1)
        self.assertEqual(stats["relevant_max"], 3)
        self.assertEqual(stats["corpus_only_files"], 0)

    def test_다운로드_포함_여부를_통계에_남긴다(self) -> None:
        # 어느 정답원으로 만든 골든인지 파일만 봐도 알아야 한다 — 두 판본의 점수를 섞으면
        # 척도가 다른 값을 비교하게 된다.
        queries = [{"id": "M0001", "query": "가", "category": "present", "topics": ["가"],
                    "relevant": ["a"], "kinds": ["국보"], "keys": ["k1"], "files": ["가.txt"]}]
        rep = {"files_matched": 1, "files_unmatched": 0, "unmatched": [], "dropped": []}
        self.assertFalse(build_golden_doc(queries, rep, [])["stats"]["include_downloads"])
        self.assertTrue(
            build_golden_doc(queries, rep, [], include_downloads=True)["stats"]["include_downloads"]
        )


if __name__ == "__main__":
    unittest.main()
