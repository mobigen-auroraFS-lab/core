#!/usr/bin/env python3
"""수집 장부 → 검색 골든셋 빌더(099 T029) — 정답을 **수집 기록**에서 뽑는다.

왜 다시 만드나
--------------
2026-08-03 골든(`golden_words.v2.json`)은 정답을 **파일명 주제표식**
(`src/search/golden_guard.py:topic_of_filename`)에서 뽑았다. 현 코퍼스에서는 그 규약이
**96.8% 퇴화**해(spec 099 G0/T002 실측 — 16,958건 중 16,409건) 토픽/파일 비율이 1.00 에 가깝다.
자산마다 토픽이 따로면 "같은 토픽 = 정답군" 이라는 전제가 무너져 정답을 만들 수 없다.

대신 **수집 장부**(`DataFlatformData/corpus/_manifest.json`)를 쓴다. 장부는 수집 시점에
국가유산청 공개 API·공공기관 공식 유튜브 채널에서 받은 기록이라 **파이프라인 산출물이 아니다** —
우리 검색·분류 결과로 우리 검색을 채점하는 순환(circular)이 생기지 않는다. 2026-08-03 에 파일명을
정답원으로 고른 이유(사람 판단 0·LLM 0·검색 결과 미사용)를 그대로 만족한다.

무엇이 정답인가
--------------
  - **질의** = `items[].name`(예: `서울 숭례문`). 국가유산 이름·인물명이라 사람이 실제로 칠 말이다.
  - **정답** = 그 item 의 `text_file`(해설문) + `images[]`(사진·영상 파일) 을 DB 자산 id 로 해소한 것.
    해소 기준은 `asset.fs_path` 의 `<uuid>__` 접두를 벗긴 원본 파일명(G0 실측 매칭률 99.8%).
  - 파일이 하나도 없는 item(1,438개)은 **제외**한다. ⚠️ 이것을 no-match(결과 없어야 하는) 질의로
    쓰면 안 된다 — `skipped` 값은 대부분 "공식 채널 영상 없음" 이고, **그 이름의 자산이 코퍼스에
    없다는 뜻이 아니다**(예: `홍길동` 는 장부 `downloads` 에 영상이 있다).
  - 같은 이름의 item 이 여럿이면(국보 item + 그 영상 item 등 289그룹) **한 질의로 병합**한다.
    나누면 같은 질의를 두 번 재게 되고 각 질의의 정답이 반쪽이 된다.

출력 형식
--------
`scripts/measure_search_golden.py` 가 읽는 계약과 같다 — `{"version","provenance","queries":[
{"id","query","category","topics","relevant":[asset_id,...]}]}`. 하니스는 `relevant` 가 있는 질의만
채점하고 `expect_empty` 를 no-match 로 센다. 이 골든에는 `expect_empty` 질의가 **없다**(위 이유).
추적용으로 `kinds`·`keys`·`files` 를 함께 남긴다(하니스는 무시한다).

🔴 산출물은 **비공개 문서 레포**(`~/work/github/DataFlatformDocs/fixtures/search/`)에 둔다 —
실 자산 id 를 담기 때문이다. 공개 레포에 쓰면 pre-commit 훅이 막는다.

결정성(헌법 3조): 정렬만 쓰고 난수·LLM 이 없다. 같은 장부·같은 DB 면 2회 실행 동일 출력.
읽기 전용(헌법 6조): `SELECT` 만 한다.

실행
    conda activate AuroraFS
    GOLDEN_DIR=~/work/github/DataFlatformDocs/fixtures/search \
      python scripts/build_golden_manifest.py --env dev \
      --manifest ~/work/github/DataFlatformData/corpus/_manifest.json
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import statistics
import sys
from pathlib import Path
from typing import Any

# scripts/ 직접 실행 시 'src' 패키지를 찾도록 저장소 루트를 sys.path 에 둔다(src import 보다 먼저).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.search.golden_guard import topic_of_filename  # noqa: E402 — sys.path 부트스트랩 뒤

_LOG = logging.getLogger("meta_extract.build_golden_manifest")

# 골든 판본 표식. 코퍼스·장부가 바뀌어 재생성하면 날짜를 올린다(옛 점수와 섞이지 않게).
GOLDEN_VERSION = "v1-manifest-20260917"
GOLDEN_PROVENANCE = (
    "수집 장부(corpus/_manifest.json) 기반 — 질의=items[].name · 정답=text_file+images[] 를 "
    "asset.fs_path 의 `__` 뒤 원본 파일명으로 자산 id 해소. 출처=국가유산청 공개 API + 공공기관 "
    "공식 유튜브 채널이라 파이프라인 산출물이 아님(순환 아님). 사람 판단 0 · LLM 0 · 검색 결과 미사용. "
    "파일 없는 item 은 제외(= no-match 질의 아님). 같은 이름 item 은 한 질의로 병합. "
    "⚠️ 2026-08-03 골든(golden_words.v2, 386질의·질의당 정답 4.75)과 척도가 달라 점수를 직접 비교하지 말 것."
)
# 기본 출력 파일명. 폴더는 --out 또는 GOLDEN_DIR 로 받는다(비공개 레포 경로를 코드에 박지 않는다).
DEFAULT_OUT_NAME = "golden_manifest.json"


def asset_basename(fs_path: str) -> str:
    """DB 경로에서 **장부와 대조할 원본 파일명**을 뽑는다(순수).

    적재 시 파일명이 ``<uuid>__<원본명>`` 으로 바뀐다. 장부에는 원본명만 있으므로 접두를 벗긴다.
    ``__`` 는 **첫 번째 것만** 경계로 본다 — 원본 제목에도 ``__`` 가 들어갈 수 있다.

    Args:
        fs_path: ``asset.fs_path`` 값(절대 경로).

    Returns:
        원본 파일명. 접두가 없으면 경로의 파일명 그대로.
    """
    name = Path(fs_path).name
    return name.split("__", 1)[1] if "__" in name else name


def manifest_entries(
    manifest: dict[str, Any],
    include_downloads: bool = False,
) -> list[dict[str, Any]]:
    """장부 dict 를 **질의 후보 목록**으로 바꾼다(순수·결정적).

    규칙: 파일 없는 item 제외 · 같은 이름은 한 질의로 병합 · 파일 중복 제거 · 이름순 정렬 후 연번 id.

    Args:
        manifest: 수집 장부 dict(``items[]`` 와 ``downloads`` 를 읽는다).
        include_downloads: 켜면 ``downloads``(유튜브 내려받기 기록 2,777건)도 같은 이름의 질의에
            합친다. **기본은 꺼짐** — 099 T029 가 지정한 정답원은 ``items[]`` 다. 다만 끄면 실제
            적재된 자산 2,489건이 어느 질의의 정답도 아닌 상태가 되므로(실측), 더 넓은 골든이
            필요하면 켠다. 켜면 "공식 채널 영상 없음" 으로 비어 있던 item 도 질의가 될 수 있다.

    Returns:
        ``[{"id","query","kinds","keys","files"}]``. ``files`` 는 basename 목록(정렬).
    """
    merged: dict[str, dict[str, Any]] = {}

    def _slot(name: str) -> dict[str, Any]:
        """이름별 누적 칸을 얻는다(없으면 만든다).

        Args:
            name: 질의가 될 이름.

        Returns:
            그 이름의 누적 dict.
        """
        return merged.setdefault(name, {"query": name, "kinds": [], "keys": [], "files": set()})

    for item in manifest.get("items") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        files = [f for f in [item.get("text_file")] if f]
        files += [im for im in (item.get("images") or []) if im]
        if not files:
            continue  # 정답 파일이 없으면 채점 불가 — no-match 질의로 전용하지 않는다.
        slot = _slot(name)
        slot["kinds"].append(str(item.get("kind") or ""))
        slot["keys"].append(str(item.get("key") or ""))
        slot["files"].update(str(f) for f in files)

    if include_downloads:
        for key, row in (manifest.get("downloads") or {}).items():
            name = str(row.get("name") or "").strip()
            path = str(row.get("file") or "").strip()
            if not name or not path:
                continue
            slot = _slot(name)
            # file 은 `<part>/<파일명>` 상대경로다 — DB 대조 기준은 파일명이라 basename 만 쓴다.
            slot["files"].add(Path(path).name)
            slot["kinds"].append(f"다운로드:{row.get('part') or '미상'}")
            slot["keys"].append(str(key))

    entries: list[dict[str, Any]] = []
    for i, name in enumerate(sorted(merged), start=1):
        slot = merged[name]
        entries.append({
            "id": f"M{i:04d}",
            "query": name,
            "kinds": sorted(set(slot["kinds"])),
            "keys": sorted(set(slot["keys"])),
            "files": sorted(slot["files"]),
        })
    return entries


def resolve_queries(
    entries: list[dict[str, Any]],
    basename_index: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """질의 후보의 정답 파일을 **자산 id** 로 해소한다(순수·결정적).

    Args:
        entries: ``manifest_entries`` 결과.
        basename_index: 원본 파일명 → 자산 id 목록. 같은 파일명이 자산 둘일 수 있다
            (국보/보물 폴더에 같은 이름의 해설문 — 장부 실측 80건). **둘 다 정답**으로 넣는다.

    Returns:
        ``(queries, report)``. ``queries`` 는 하니스가 읽는 질의 목록(정답 0 인 질의는 뺀다 —
        재현율 분모가 0 이라 채점 불가). ``report`` 는 못 찾은 파일·뺀 질의·집계.
    """
    queries: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []
    matched = 0
    for entry in entries:
        relevant: set[str] = set()
        topics: set[str] = set()
        for f in entry["files"]:
            ids = basename_index.get(f)
            if not ids:
                unmatched.append({"id": entry["id"], "query": entry["query"], "file": f})
                continue
            matched += 1
            relevant.update(ids)
            topics.add(topic_of_filename(f))
        if not relevant:
            dropped.append({"id": entry["id"], "query": entry["query"]})
            continue
        queries.append({
            "id": entry["id"],
            "query": entry["query"],
            "category": "present",
            "topics": sorted(t for t in topics if t),
            "relevant": sorted(relevant),
            "kinds": entry["kinds"],
            "keys": entry["keys"],
            "files": entry["files"],
        })
    report = {
        "files_matched": matched,
        "files_unmatched": len(unmatched),
        "unmatched": unmatched,
        "dropped": dropped,
    }
    return queries, report


def explain_unmatched(
    unmatched: list[dict[str, str]],
    status_index: dict[str, list[str]],
) -> list[dict[str, str]]:
    """정답이 되지 못한 장부 파일에 **사유**를 붙인다(순수).

    사유는 둘뿐이다 — ① 적재는 됐으나 ``registered`` 가 아니어서 색인 대상이 아님(보류·실패),
    ② DB 에 아예 없음(인입 누락). 조용히 버리면 다음 재생성 때 같은 조사를 처음부터 다시 한다.

    Args:
        unmatched: ``resolve_queries`` 보고서의 ``unmatched`` 항목들.
        status_index: 파일명 → **등록이 아닌** 자산 상태 목록(``deferred``·``failed`` 등).

    Returns:
        원래 항목에 ``reason`` 을 더한 목록(입력 순서 유지).
    """
    out: list[dict[str, str]] = []
    for row in unmatched:
        sts = status_index.get(row["file"])
        reason = f"비등록({'·'.join(sorted(sts))})" if sts else "DB 미적재"
        out.append({**row, "reason": reason})
    return out


def corpus_only_basenames(
    entries: list[dict[str, Any]],
    basename_index: dict[str, list[str]],
) -> list[str]:
    """DB 에는 있는데 **장부에 없는** 파일명을 찾는다(순수) — 골든이 덮지 못하는 자산.

    Args:
        entries: ``manifest_entries`` 결과.
        basename_index: 원본 파일명 → 자산 id 목록.

    Returns:
        장부에 없는 파일명 목록(정렬). 사유 추적용 로그이지 실패 조건이 아니다.
    """
    known = {f for e in entries for f in e["files"]}
    return sorted(set(basename_index) - known)


def build_golden_doc(
    queries: list[dict[str, Any]],
    report: dict[str, Any],
    corpus_only: list[str],
    include_downloads: bool = False,
) -> dict[str, Any]:
    """골든 JSON 문서를 조립한다(순수).

    Args:
        queries: ``resolve_queries`` 가 낸 질의 목록.
        report: ``resolve_queries`` 가 낸 보고서(집계만 문서에 싣는다 — 상세 목록은 별도 파일).
        corpus_only: 장부에 없는 DB 파일명 목록(집계만 싣는다).
        include_downloads: 이 골든이 ``downloads`` 를 합쳐 만들어졌는지. 통계에 그대로 적는다 —
            판본이 다르면 질의 수·정답 수가 달라 **점수를 직접 비교할 수 없다**.

    Returns:
        ``measure_search_golden.py`` 가 읽는 모양의 dict.
    """
    sizes = [len(q["relevant"]) for q in queries] or [0]
    return {
        "version": GOLDEN_VERSION,
        "provenance": GOLDEN_PROVENANCE,
        "stats": {
            "include_downloads": bool(include_downloads),
            "queries": len(queries),
            "relevant_total": sum(sizes),
            "relevant_avg": round(sum(sizes) / len(sizes), 4),
            "relevant_median": float(statistics.median(sizes)),
            "relevant_min": min(sizes),
            "relevant_max": max(sizes),
            "files_matched": report["files_matched"],
            "files_unmatched": report["files_unmatched"],
            "queries_dropped": len(report["dropped"]),
            "corpus_only_files": len(corpus_only),
        },
        "queries": queries,
    }


def load_basename_index(conn) -> dict[str, list[str]]:
    """등록 자산의 **원본 파일명 → 자산 id** 색인을 만든다(읽기 전용).

    Args:
        conn: 열린 PostgreSQL 커넥션.

    Returns:
        파일명 → 자산 id 목록(정렬). 같은 파일명의 자산이 둘이면 둘 다 담는다.
    """
    index: dict[str, list[str]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT asset_id, fs_path FROM asset WHERE status='registered'")
        for asset_id, fs_path in cur.fetchall():
            index.setdefault(asset_basename(str(fs_path)), []).append(str(asset_id))
    return {k: sorted(v) for k, v in index.items()}


def load_status_index(conn) -> dict[str, list[str]]:
    """**등록이 아닌** 자산의 파일명 → 상태 색인을 만든다(읽기 전용) — 미매칭 사유 설명용.

    Args:
        conn: 열린 PostgreSQL 커넥션.

    Returns:
        파일명 → 상태 목록(중복 제거·정렬). 예: ``{"사진1.jpg": ["deferred"]}``.
    """
    index: dict[str, set[str]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT status, fs_path FROM asset WHERE status <> 'registered'")
        for status, fs_path in cur.fetchall():
            index.setdefault(asset_basename(str(fs_path)), set()).add(str(status))
    return {k: sorted(v) for k, v in index.items()}


def _resolve_out_dir(out: str | None) -> Path:
    """출력 폴더를 정한다 — 비공개 레포 경로를 코드에 박지 않기 위한 해소기.

    Args:
        out: ``--out`` 으로 받은 폴더 경로. ``None`` 이면 환경변수 ``GOLDEN_DIR`` 을 쓴다.

    Returns:
        존재하는 출력 폴더 경로.

    Raises:
        SystemExit: 어느 쪽도 주어지지 않았거나 폴더가 없을 때 — 무엇을 해야 하는지 알리고 멈춘다.
    """
    cand = out or os.environ.get("GOLDEN_DIR")
    if not cand:
        raise SystemExit(
            "출력 폴더가 없습니다 — 골든은 실 자산 id 를 담아 공개 레포에 두지 않습니다.\n"
            "  --out <폴더> 또는 GOLDEN_DIR=<폴더> 로 비공개 문서 레포 경로를 지정하십시오.\n"
            "  예: GOLDEN_DIR=~/work/github/DataFlatformDocs/fixtures/search"
        )
    path = Path(cand).expanduser()
    if not path.is_dir():
        raise SystemExit(f"출력 폴더가 없습니다: {path}")
    return path


def main() -> int:
    """수집 장부를 읽어 검색 골든셋과 사유 보고서를 만든다.

    Returns:
        0=성공.
    """
    parser = argparse.ArgumentParser(description="수집 장부 → 검색 골든셋 빌더(099 T029)")
    parser.add_argument(
        "--manifest", required=True,
        help="수집 장부 경로(corpus/_manifest.json). 코퍼스 위치는 머신마다 달라 필수 인자로 받는다.",
    )
    parser.add_argument(
        "--out", default=None,
        help="출력 폴더. 생략하면 환경변수 GOLDEN_DIR. 비공개 문서 레포의 fixtures/search 를 준다.",
    )
    parser.add_argument(
        "--env", default="dev",
        help="설정 환경 이름(dev/stage/prod 중 하나). DB 접속 정보 선택에 쓴다.",
    )
    parser.add_argument(
        "--name", default=DEFAULT_OUT_NAME,
        help=f"골든 파일 이름(기본 {DEFAULT_OUT_NAME}). 보고서는 같은 줄기에 .report.json 으로 쓴다.",
    )
    parser.add_argument(
        "--include-downloads", action="store_true",
        help="장부 downloads(유튜브 내려받기 기록)도 같은 이름의 질의에 합친다. 기본 꺼짐 — "
             "끄면 적재된 자산 약 2,489건이 어느 질의의 정답도 아닌 상태가 된다(실측).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_file():
        raise SystemExit(f"장부 파일이 없습니다: {manifest_path}")
    out_dir = _resolve_out_dir(args.out)

    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env.dev", override=False)
    from src.config.settings import init_settings

    init_settings(args.env)
    from src.database.postgres_util import PostgresUtil

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest_entries(manifest, include_downloads=args.include_downloads)
    _LOG.info("[장부] item %d개(+내려받기 %s) → 질의 후보 %d개(파일 있는 것만·같은 이름 병합)",
              len(manifest.get("items") or []),
              f"{len(manifest.get('downloads') or {})}건 포함" if args.include_downloads else "미포함",
              len(entries))

    db = PostgresUtil()
    with db, db.connection() as conn:
        index = load_basename_index(conn)
        status_index = load_status_index(conn)
    _LOG.info("[DB] 등록 자산 파일명 %d종 · 비등록 %d종", len(index), len(status_index))

    queries, report = resolve_queries(entries, index)
    report["unmatched"] = explain_unmatched(report["unmatched"], status_index)
    corpus_only = corpus_only_basenames(entries, index)
    doc = build_golden_doc(queries, report, corpus_only, include_downloads=args.include_downloads)

    _LOG.info("[해소] 질의 %d개 · 정답 연 %d건 · 파일 매칭 %d / 미매칭 %d · 정답0 질의 %d개 제외",
              doc["stats"]["queries"], doc["stats"]["relevant_total"],
              report["files_matched"], report["files_unmatched"], len(report["dropped"]))
    _LOG.info("[사유] 장부에만 있는 파일 %d건 · DB 에만 있는 파일 %d건(골든 미커버)",
              report["files_unmatched"], len(corpus_only))
    reasons = collections.Counter(row["reason"] for row in report["unmatched"])
    _LOG.info("[사유] 장부에만 있는 파일의 내역: %s",
              ", ".join(f"{k} {v}건" for k, v in sorted(reasons.items())) or "없음")
    for row in report["unmatched"][:10]:
        _LOG.info("  · 장부에만: %s (질의 %s · %s)", row["file"], row["query"], row["reason"])
    for name in corpus_only[:10]:
        _LOG.info("  · DB 에만: %s", name)

    out_path = out_dir / args.name
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    report_path = out_dir / (args.name.removesuffix(".json") + ".report.json")
    report_path.write_text(
        json.dumps(
            {
                "version": GOLDEN_VERSION,
                "manifest": str(manifest_path),
                "unmatched": report["unmatched"],
                "dropped": report["dropped"],
                "corpus_only": corpus_only,
            },
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    _LOG.info("DONE %s · 보고서 %s", out_path, report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
