"""025 — 골든셋 스키마(순수) + 코퍼스-골든 정합 가드(실DB, 게이트 ``RUN_OS_E2E``).

두 세대의 골든이 공존한다(둘 다 실 자산 id 를 담아 비공개 문서 레포 소유 · 부재 시 skip):

- **레거시** `golden_os.json` — 구조 불변식만 검사한다(`GoldenFixtureSchemaTest`).
- **정본** `golden_manifest_full.json`(099 T029) — 정합 가드가 읽는다(`GoldenCoverageGuardTest`).

정합 가드의 기준이 099 T031 에서 바뀌었다. 종전에는 **파일명 주제표식**(`topic_of_filename`)으로
토픽을 뽑아 비교했으나 현 코퍼스에서 96.8% 퇴화해(토픽/자산 0.982) 켜면 무조건 빨간불이었다.
지금은 **자산 id 커버리지**(`uncovered_assets`)로 본다 — "적재됐는데 어느 골든 질의의 정답도 아닌
자산"이 허용치를 넘으면 실패해 골든 갱신을 요구한다(FR-004 의 뜻은 그대로).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

_FX = Path(__file__).resolve().parent / "fixtures" / "search"
_RUN = os.getenv("RUN_OS_E2E") == "1"


# golden_os.json 은 **실 코퍼스 asset_id 230개**를 담고 있어 이 레포(공개)에 두지 않는다 —
# 비공개 문서 레포가 소유하고 측정 시에만 가져온다. 그래서 부재 시 skip 한다.
_GOLDEN_OS = _FX / "golden_os.json"
# 정합 가드가 읽는 **정본 골든**(099 T029 · 수집 장부 기반 4,128질의). 같은 이유로 이 레포에 없다.
_GOLDEN_MANIFEST = _FX / "golden_manifest_full.json"

# 미커버 허용치 — **0 을 요구하지 않는다**. 2026-09-17 실측 미커버는 38건(전부 수집 장부에 기록이
# 없는 위키백과 `.txt` 수집분)이라 0 을 걸면 이 가드는 켜는 즉시 빨간불이고, 빨간불이 상수면
# 아무도 보지 않는다. 이 가드가 잡아야 할 사고는 "골든을 갱신하지 않고 새 자산 묶음을 통째로
# 적재"(수백~수천 건)다 — 종목 하나가 통째로 들어와도(최소 141건: 명승) 허용치를 넘는다.
# 통과하더라도 아래에서 건수를 **항상 인쇄**하므로 38 → 60 같은 잠행 증가도 눈에 보인다.
_MAX_UNCOVERED_ASSETS = 100
# 실패·보고 문구에 찍을 표본 개수(전량 인쇄 금지 — 수천 건이 한 줄로 쏟아진다).
_SAMPLE_N = 5


@unittest.skipUnless(_GOLDEN_OS.is_file(), f"golden_os.json 없음(비공개 문서 레포 소유): {_GOLDEN_OS}")
class GoldenFixtureSchemaTest(unittest.TestCase):
    """golden_os.json 의 구조 불변식 — 측정 하니스가 기대하는 계약(fixture 있을 때만)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = json.loads(_GOLDEN_OS.read_text(encoding="utf-8"))

    def test_query_categories_and_fields(self) -> None:
        qs = self.golden["queries"]
        self.assertGreaterEqual(len(qs), 59)
        for q in qs:
            self.assertIn(q["category"], {"present", "absent", "edge"})
            if q["category"] == "absent":
                self.assertTrue(q.get("expect_empty"), f"{q['id']}: absent 는 expect_empty")
                self.assertNotIn("relevant", q)
            else:
                self.assertIsInstance(q.get("relevant"), list, f"{q['id']}: relevant 필요")
                self.assertTrue(q.get("topics"), f"{q['id']}: 토픽 태그 필요(정합 가드용)")

    def test_no_match_queries_included(self) -> None:
        # 골든 최초로 no-match 질의 포함(차단율 계기판) — 24종 유지.
        absent = [q for q in self.golden["queries"] if q["category"] == "absent"]
        self.assertGreaterEqual(len(absent), 24)

    def test_provenance_fixture_exists(self) -> None:
        prov = json.loads((_FX / "golden_os.judgments.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(prov["verdicts"]), 1392)


# 게이트가 둘인 이유: 실 DB 뿐 아니라 **골든 파일도 있어야** 돈다. 파일 가드를 빼면
# `RUN_OS_E2E=1` 만으로 실행돼 `FileNotFoundError` 로 죽는다 — 부재는 정상 상태이므로
# 실패가 아니라 skip 이어야 한다. 위 클래스와 **같은 모양의 가드**를 붙여 둔다(한쪽만 붙이면 갈린다).
# 다만 읽는 파일은 다르다 — 스키마 검사는 레거시 골든, 정합 가드는 정본 골든(099 T031).
@unittest.skipUnless(_RUN and _GOLDEN_MANIFEST.is_file(),
                     f"RUN_OS_E2E=1 + 실 DB + 골든 파일 필요: {_GOLDEN_MANIFEST}")
class GoldenCoverageGuardTest(unittest.TestCase):
    """FR-004(실DB): 적재 자산 ↔ 골든 정답 커버리지 — 미커버가 허용치를 넘으면 실패한다.

    099 T031 로 단위를 **토픽 → 자산 id** 로 바꿨다. 옛 기준(파일명 주제표식)은 현 코퍼스에서
    자산마다 고유 토픽이 나와(토픽/자산 0.982) 정답군을 만들지 못했다.
    """

    def test_registered_assets_covered_by_golden(self) -> None:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env.dev", override=False)
        from src.config.settings import init_settings

        init_settings("dev")
        from src.database.postgres_util import PostgresUtil
        from src.search.golden_guard import uncovered_assets

        golden = json.loads(_GOLDEN_MANIFEST.read_text(encoding="utf-8"))
        golden_ids: set[str] = set()
        for q in golden["queries"]:
            golden_ids.update(str(a) for a in q.get("relevant", []))
        # 골든이 비었는데 "미커버 0" 으로 통과하는 거짓 green 을 막는다(fixture 파손 감지).
        self.assertTrue(golden_ids, f"골든 정답이 비었다 — fixture 확인: {_GOLDEN_MANIFEST}")

        db = PostgresUtil()
        with db, db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT asset_id FROM asset WHERE status='registered'")
            registered = {str(r[0]) for r in cur.fetchall()}

        missing = uncovered_assets(registered, golden_ids)
        sample = ", ".join(missing[:_SAMPLE_N]) + (" …" if len(missing) > _SAMPLE_N else "")
        # 통과해도 항상 남긴다 — 추세(38 → 60 …)가 보여야 허용치가 살아 있는 장치가 된다.
        print(
            f"[골든 커버리지] 적재 {len(registered)} · 골든 정답 {len(golden_ids)} · "
            f"미커버 {len(missing)}/허용 {_MAX_UNCOVERED_ASSETS}"
            + (f" · 예: {sample}" if missing else "")
        )
        self.assertLessEqual(
            len(missing), _MAX_UNCOVERED_ASSETS,
            f"골든 미커버 자산 {len(missing)}건(허용 {_MAX_UNCOVERED_ASSETS}) — 예: {sample}. "
            f"신규 데이터가 적재됐다면 골든을 재생성하라(scripts/build_golden_manifest.py · FR-004)",
        )


if __name__ == "__main__":
    unittest.main()
