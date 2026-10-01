"""자산 자기주제 **read seam** 코어 단위 테스트 — fetch_asset_topic·find_same_topic_groups (mock·DB 불요).

078 레포 분리: asset_topic **read**(조회) 함수는 077에서 `src/topic/asset_topic_query.py`(코어)로 이동했고,
**write(classify)**는 파이프라인 레포로 갔다. 이 두 read 함수의 단위 검증(SQL 형상·짝 매칭·already_linked
대칭·정렬/절단)은 **코어 소속**이므로 여기서 유지한다(구 `tests/test_asset_topic_classify.py` 의
TestFetchAssetTopic·TestFindSameTopicGroups 를 코어로 재편입 — classify 이관 시 유실 방지·헌법 8조 회귀 0).
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE_PATH = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "topics", "same_topic_groups_contract.json"
)
# 이 계약 스냅샷은 **실 코퍼스 asset_id** 를 담고 있어 이 레포(공개)에 두지 않는다 — 비공개 문서
# 레포가 소유하고, 측정할 때만 이 경로로 가져온다. 그래서 부재 시 실패가 아니라 **skip** 이다
# (다른 골든 테스트들도 같은 규약: `RUN_OS_E2E` 게이트 또는 파일 존재 확인).
_HAS_FIXTURE = os.path.isfile(_FIXTURE_PATH)
_FIXTURE_REASON = f"계약 fixture 없음(비공개 문서 레포 소유): {_FIXTURE_PATH}"


def _mock_conn_seq(fetchone_val=None, fetchall_val=None):
    """``conn.cursor(...)`` 컨텍스트매니저 mock — fetchone/fetchall 을 각각 통제."""
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.return_value = fetchone_val
    cur.fetchall.return_value = fetchall_val if fetchall_val is not None else []
    conn.cursor.return_value = cur
    return conn, cur


@unittest.skipUnless(_HAS_FIXTURE, _FIXTURE_REASON)
class TestFetchAssetTopic(unittest.TestCase):
    """T204 — 정본 읽기(구 project_asset_topics 형상)·부재 []."""

    def _fixture_keys(self):
        with open(_FIXTURE_PATH, encoding="utf-8") as fh:
            fx = json.load(fh)
        return set(fx["project_asset_topics_shape"][0].keys())

    def test_row_present_returns_weight_one_shape(self) -> None:
        from src.topic.asset_topic_query import fetch_asset_topic

        row = {
            "topic_ko": "스포츠·레저",
            "subtopic_ko": "농구",
            "topic_en": "sports_leisure",
            "subtopic_en": "basketball",
        }
        conn, _ = _mock_conn_seq(fetchone_val=row)
        out = fetch_asset_topic(conn, "A1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["weight"], 1)
        # 필드명 수준 fixture(구 투영 형상)와 동일해야 소비처 무변경 스왑 가능.
        self.assertEqual(set(out[0].keys()), self._fixture_keys())
        self.assertEqual(out[0]["topic_ko"], "스포츠·레저")

    def test_absent_returns_empty(self) -> None:
        from src.topic.asset_topic_query import fetch_asset_topic

        conn, _ = _mock_conn_seq(fetchone_val=None)
        self.assertEqual(fetch_asset_topic(conn, "missing"), [])


@unittest.skipUnless(_HAS_FIXTURE, _FIXTURE_REASON)
class TestFindSameTopicGroups(unittest.TestCase):
    """T204 — 같은 (topic,subtopic) 자산 집계(구 find_topic_neighbor_groups 형상)."""

    def _fixture_group_keys(self):
        with open(_FIXTURE_PATH, encoding="utf-8") as fh:
            fx = json.load(fh)
        g = fx["find_topic_neighbor_groups_shape"][0]
        sub = g["subtopics"][0]
        asset = sub["assets"][0]
        return set(g.keys()), set(sub.keys()), set(asset.keys())

    def test_no_target_topic_returns_empty(self) -> None:
        from src.topic.asset_topic_query import find_same_topic_groups

        conn, _ = _mock_conn_seq(fetchone_val=None)  # 대상 자산 asset_topic 행 없음
        self.assertEqual(find_same_topic_groups(conn, "A"), [])

    def test_pair_match_shape_equals_contract(self) -> None:
        from src.topic.asset_topic_query import find_same_topic_groups

        target = {"topic_ko": "스포츠·레저", "subtopic_ko": "농구"}
        cand_rows = [
            {"asset_id": "019f-1", "topic_ko": "스포츠·레저", "subtopic_ko": "농구",
             "sub_count": 2, "topic_count": 2,
             "fs_path": "/d/019f-1__wikipedia_농구_5166.txt", "modality": "text",
             "already_linked": True},
            {"asset_id": "019f-2", "topic_ko": "스포츠·레저", "subtopic_ko": "농구",
             "sub_count": 2, "topic_count": 2,
             "fs_path": "/d/019f-2__Kim_Tae-sul_(농구).JPG", "modality": "image",
             "already_linked": False},
        ]
        conn, cur = _mock_conn_seq(fetchone_val=target, fetchall_val=cand_rows)
        out = find_same_topic_groups(conn, "TARGET")

        self.assertEqual(len(out), 1)
        gk, sk, ak = self._fixture_group_keys()
        self.assertEqual(set(out[0].keys()), gk)
        self.assertEqual(out[0]["topic_ko"], "스포츠·레저")
        self.assertEqual(out[0]["asset_count"], 2)
        sub = out[0]["subtopics"][0]
        self.assertEqual(set(sub.keys()), sk)
        self.assertEqual(sub["subtopic_ko"], "농구")
        self.assertEqual(sub["asset_count"], 2)
        asset0 = sub["assets"][0]
        self.assertEqual(set(asset0.keys()), ak)
        # file_name 은 fs_path basename(관례)·asset_id 는 str.
        self.assertTrue(asset0["file_name"].endswith(".txt") or asset0["file_name"].endswith(".JPG"))
        self.assertIsInstance(asset0["asset_id"], str)
        # subtopic 있는 대상 → 후보 쿼리에 subtopic 필터가 걸려야(같은 쌍 매칭).
        cand_sql = " ".join(cur.execute.call_args_list[1][0][0].split()).lower()
        self.assertIn("at.subtopic_ko = %s", cand_sql)

    def test_topic_only_match_when_target_subtopic_none(self) -> None:
        from src.topic.asset_topic_query import find_same_topic_groups

        target = {"topic_ko": "음식·요리", "subtopic_ko": None}
        cand_rows = [
            {"asset_id": "b1", "topic_ko": "음식·요리", "subtopic_ko": "제빵",
             "sub_count": 1, "topic_count": 2,
             "fs_path": "/d/b1__bread.jpg", "modality": "image", "already_linked": False},
            {"asset_id": "b2", "topic_ko": "음식·요리", "subtopic_ko": None,
             "sub_count": 1, "topic_count": 2,
             "fs_path": "/d/b2__food.txt", "modality": "text", "already_linked": True},
        ]
        conn, cur = _mock_conn_seq(fetchone_val=target, fetchall_val=cand_rows)
        out = find_same_topic_groups(conn, "TARGET")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["topic_ko"], "음식·요리")
        # 두 하위주제 버킷(제빵·None) 모두 등장 — topic 단독 매칭.
        subs = {s["subtopic_ko"] for s in out[0]["subtopics"]}
        self.assertEqual(subs, {"제빵", None})
        # 대상 subtopic 이 None → 후보 쿼리에 subtopic 필터가 없어야(topic 단독).
        cand_sql = " ".join(cur.execute.call_args_list[1][0][0].split())
        self.assertNotIn("at.subtopic_ko = %s", cand_sql)


class TestFindSameTopicGroupsSqlCut(unittest.TestCase):
    """SQL 에서 하위주제별 상위 n 건만 자른다(2026-10-01) — 계약 fixture 없이도 돈다."""

    def test_counts_come_from_sql_not_returned_rows(self) -> None:
        """SQL 이 8건만 돌려줘도 개수는 자르기 전 값이다(화면의 '더 있음' 근거)."""
        from src.topic.asset_topic_query import find_same_topic_groups

        target = {"topic_ko": "역사·문화유산", "subtopic_ko": "유적·유물"}
        cand_rows = [
            {"asset_id": f"a{i}", "topic_ko": "역사·문화유산", "subtopic_ko": "유적·유물",
             "sub_count": 10614, "topic_count": 10614,
             "fs_path": f"/d/a{i}__x.jpg", "modality": "image", "already_linked": False}
            for i in range(8)
        ]
        conn, _ = _mock_conn_seq(fetchone_val=target, fetchall_val=cand_rows)
        out = find_same_topic_groups(conn, "TARGET")
        self.assertEqual(out[0]["asset_count"], 10614)
        self.assertEqual(out[0]["subtopics"][0]["asset_count"], 10614)
        self.assertEqual(len(out[0]["subtopics"][0]["assets"]), 8)

    def test_sql_cuts_per_subtopic_and_binds_limit_last(self) -> None:
        """자르기는 SQL 에서 하고, n 은 마지막 바인딩이다(전 행 회수 회귀 방지)."""
        from src.topic.asset_topic_query import find_same_topic_groups

        conn, cur = _mock_conn_seq(fetchone_val={"topic_ko": "T", "subtopic_ko": "S"},
                                   fetchall_val=[])
        find_same_topic_groups(conn, "TARGET", max_assets_per_subtopic=8)
        sql, params = cur.execute.call_args_list[1][0]
        compact = " ".join(sql.split())
        self.assertIn("row_number() OVER (PARTITION BY NULLIF(at.subtopic_ko, '') "
                      "ORDER BY at.asset_id)", compact)
        self.assertIn("WHERE at.rn <= %s", compact)
        self.assertEqual(params, ("TARGET", "T", "S", "TARGET", "TARGET", 8))

    def test_zero_limit_keeps_bucket_with_no_assets(self) -> None:
        """n = 0 이면 버킷은 남고 자산만 빈다 — SQL 에서 자르지 않는다(종전처럼 전부 읽는다)."""
        from src.topic.asset_topic_query import find_same_topic_groups

        rows = [{"asset_id": "a1", "topic_ko": "T", "subtopic_ko": "S", "sub_count": 3,
                 "topic_count": 3, "fs_path": "/d/a1__x.txt", "modality": "text",
                 "already_linked": False}]
        conn, cur = _mock_conn_seq(fetchone_val={"topic_ko": "T", "subtopic_ko": "S"},
                                   fetchall_val=rows)
        out = find_same_topic_groups(conn, "TARGET", max_assets_per_subtopic=0)
        self.assertEqual(out[0]["subtopics"][0]["asset_count"], 3)
        self.assertEqual(out[0]["subtopics"][0]["assets"], [])
        sql, params = cur.execute.call_args_list[1][0]
        self.assertNotIn("at.rn <= %s", " ".join(sql.split()))
        self.assertEqual(params, ("TARGET", "T", "S", "TARGET", "TARGET"))

    def test_negative_limit_follows_python_slice_like_before(self) -> None:
        """n < 0 은 종전 파이썬 슬라이스 규칙(``[:-1]`` = 마지막 제외)을 그대로 따른다."""
        from src.topic.asset_topic_query import find_same_topic_groups

        rows = [{"asset_id": f"a{i}", "topic_ko": "T", "subtopic_ko": "S", "sub_count": 3,
                 "topic_count": 3, "fs_path": f"/d/a{i}__x.txt", "modality": "text",
                 "already_linked": False} for i in range(3)]
        conn, cur = _mock_conn_seq(fetchone_val={"topic_ko": "T", "subtopic_ko": "S"},
                                   fetchall_val=rows)
        out = find_same_topic_groups(conn, "TARGET", max_assets_per_subtopic=-1)
        assets = out[0]["subtopics"][0]["assets"]
        self.assertEqual([x["asset_id"] for x in assets], ["a0", "a1"])
        self.assertNotIn("at.rn <= %s", " ".join(cur.execute.call_args_list[1][0][0].split()))

    def test_topic_only_path_binds_without_subtopic(self) -> None:
        """대상에 하위주제가 없으면 같은 쌍 필터 없이 바인딩한다(topic 단독 매칭)."""
        from src.topic.asset_topic_query import find_same_topic_groups

        conn, cur = _mock_conn_seq(fetchone_val={"topic_ko": "T", "subtopic_ko": None},
                                   fetchall_val=[])
        find_same_topic_groups(conn, "TARGET", max_assets_per_subtopic=8)
        sql, params = cur.execute.call_args_list[1][0]
        compact = " ".join(sql.split())
        self.assertNotIn("at.subtopic_ko = %s", compact)
        self.assertIn("WHERE at.rn <= %s", compact)
        self.assertEqual(params, ("TARGET", "T", "TARGET", "TARGET", 8))

    def test_empty_and_null_subtopic_share_one_bucket(self) -> None:
        """''·NULL 하위주제는 한 버킷(None)이다 — SQL 열쇠와 파이썬 묶음이 같은 규칙을 쓴다."""
        from src.topic.asset_topic_query import find_same_topic_groups

        rows = [{"asset_id": aid, "topic_ko": "T", "subtopic_ko": sub, "sub_count": 2,
                 "topic_count": 2, "fs_path": f"/d/{aid}__x.txt", "modality": "text",
                 "already_linked": False} for aid, sub in (("a1", ""), ("a2", None))]
        conn, cur = _mock_conn_seq(fetchone_val={"topic_ko": "T", "subtopic_ko": None},
                                   fetchall_val=rows)
        out = find_same_topic_groups(conn, "TARGET")
        self.assertEqual([s["subtopic_ko"] for s in out[0]["subtopics"]], [None])
        self.assertEqual(len(out[0]["subtopics"][0]["assets"]), 2)
        compact = " ".join(cur.execute.call_args_list[1][0][0].split())
        self.assertIn("NULLIF(at.subtopic_ko, '') AS subtopic_ko", compact)
        self.assertIn("PARTITION BY NULLIF(at.subtopic_ko, '')", compact)

# ── 068 G4: 닫힌 subtopic 조회 + LLM 선택 (T301) ──────────────────────────────
# subtopic 도 topic 처럼 부모 topic 의 **닫힌 시드 목록**에서 LLM 이 고른다(058 열린 어휘 canonicalize
# 폐기·과병합/과코스닝 차단). 아래는 조회·선택·정본 en 조회 3개 신설 함수의 단위 검증(mock·DB/LLM 불요).
_SUB_JSON_OK = '{"subtopic_ko": "국내여행·지역탐방"}'


if __name__ == "__main__":
    unittest.main()
