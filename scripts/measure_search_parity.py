"""검색 랭킹 불변(parity) 하니스 — 코드 변경 **전/후**의 결과 id·순위가 같은지 증명한다. 읽기 전용.

왜 필요한가: 083(태그 패싯)처럼 "기존 검색은 건드리지 않는다"고 주장하는 변경은 **주장만으로는
믿을 수 없다**. 골든 질의 전량을 변경 전에 한 번, 변경 후에 한 번 돌려 **결과 자산 id 와 그 순서**가
글자 그대로 같은지 비교하면 주장이 사실인지 판정된다(083 spec SC-04). 사진을 두 장 찍어 겹쳐 보는
것과 같다.

기존 도구가 없어 신작한다(2026-08-21 3레포 실측 — parity 스냅샷 json 만 잔존·생성 도구 부재).

사용법(두 모드):

```bash
# ① 캡처 — 변경 전 상태에서 한 번, 변경 후 상태에서 또 한 번(실 OpenSearch 필요)
conda run -n AuroraFS python scripts/measure_search_parity.py \
    --capture /tmp/parity_before.json --golden <골든.json>

# ② 비교 — 두 스냅의 질의별·버킷별 id 순서 완전 일치 검사(순수 계산·OS 불필요)
conda run -n AuroraFS python scripts/measure_search_parity.py \
    --compare /tmp/parity_before.json /tmp/parity_after.json
```

⚠️ **골든 경로는 반드시 인자로 준다**(기본값 없음). 골든 파일은 실 자산 id 를 담아 **이 공개 레포에
두지 않는다**(비공개 문서 레포 소유 · 측정 JSON 깃 제외 결정 2026-08-05). 지원 형식 두 가지:
``[{"query": ...}, ...]`` 와 ``{"queries": [{"query": ...}, ...]}``.

종료 코드: 비교에서 **차이가 하나라도 있으면 1**(CI·게이트에서 그대로 쓸 수 있게), 없으면 0.

설계 배경: ``specs/083-search-tag-facet`` T111 · SC-04
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 검색 응답에서 결과 버킷이 아닌 키 — 비교 대상에서 뺀다. 지연·게이트 메타는 실행마다 달라지므로
# 넣으면 코드가 그대로여도 매번 "차이 있음"이 된다.
_NON_BUCKET_KEYS = frozenset({"meta"})


def load_queries(golden: Any) -> list[str]:
    """골든 파일 내용에서 질의 문자열 목록을 뽑는다(순서 보존·중복 제거).

    두 형식을 모두 받는다 — ``[{"query": ...}, ...]``(golden_ko) 와
    ``{"queries": [{"query": ...}, ...]}``(golden_os). 같은 질의가 두 번 들어 있으면 한 번만 재고,
    빈 질의는 버린다.

    Args:
        golden: ``json.load`` 결과(리스트 또는 ``queries`` 키를 가진 dict).

    Returns:
        질의 문자열 목록(골든에 나온 순서).

    Raises:
        ValueError: 두 형식 어느 쪽도 아닐 때. 조용히 빈 목록을 돌려주면 "질의 0건 → 차이 0건"이
            되어 parity 를 통과한 것처럼 보인다(가짜 초록).
    """
    if isinstance(golden, dict):
        items = golden.get("queries")
        if not isinstance(items, list):
            raise ValueError("골든 형식 오류: dict 형식은 'queries' 리스트가 있어야 한다")
    elif isinstance(golden, list):
        items = golden
    else:
        raise ValueError(f"골든 형식 오류: 리스트 또는 dict 여야 한다(받은 것: {type(golden).__name__})")

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        q = item.get("query")
        if not isinstance(q, str) or not q.strip():
            continue
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def bucket_ids(result: dict[str, Any]) -> dict[str, list[str]]:
    """검색 결과에서 **비교 대상만** 남긴다 — 버킷별 자산 id 순서.

    점수·요약·메타는 일부러 버린다. 점수는 부동소수 표현이 환경마다 흔들릴 수 있고 메타(지연 등)는
    실행마다 달라, 넣으면 "코드는 같은데 차이 있음"이 되어 판정이 무의미해진다. 랭킹 불변의 정의는
    **"누가 몇 번째로 나오나"** 이므로 id 순서만 남기면 충분하다.

    Args:
        result: ``search_hybrid`` 반환 dict — 실제 계약은 ``{"query", "results": {버킷: [행, ...]},
            "meta"}`` 로 버킷이 **``results`` 아래에 중첩**돼 있다(``search_service.py`` 반환부 실측).
            최상위에서 버킷을 찾으면 전부 건너뛰어 빈 스냅이 된다 — 2026-08-24 실캡처 253질의
            0건으로 실증된 함정이라 모양이 다르면 조용히 넘기지 않고 즉시 실패한다.

    Returns:
        ``{버킷명: [자산 id, ...]}``. ``meta`` 는 제외한다. id 는 문자열로 정규화한다
        (UUID 객체·문자열 혼재로 스냅이 갈라지지 않게 — 조회행 UUID→str 관례).

    Raises:
        ValueError: ``results`` 키가 없거나 dict 가 아닐 때(계약 밖 모양 — 빈 스냅 위조 방지).
    """
    buckets = result.get("results") if isinstance(result, dict) else None
    if not isinstance(buckets, dict):
        raise ValueError("검색 응답 형식 오류: 'results' dict 가 필요하다(버킷은 그 아래에 중첩)")
    out: dict[str, list[str]] = {}
    for bucket, rows in buckets.items():
        if bucket in _NON_BUCKET_KEYS or not isinstance(rows, list):
            continue
        out[bucket] = [str(r.get("id")) for r in rows if isinstance(r, dict)]
    return out


def capture_snapshot(
    queries: list[str],
    *,
    limit_per_bucket: int,
    search_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """질의 전량을 검색해 비교용 스냅을 만든다.

    Args:
        queries: 골든에서 뽑은 질의 목록.
        limit_per_bucket: 버킷당 결과 상한. 전/후 스냅이 **같은 값**이어야 비교가 성립하므로
            스냅에 함께 기록해 둔다(다른 값으로 찍은 스냅을 비교하면 차이가 코드 탓인지
            설정 탓인지 구분할 수 없다).
        search_fn: 검색 호출 seam. ``search_fn(query, limit_per_bucket=…)`` 형태로 불리며
            ``search_hybrid`` 를 그대로 넣는다(테스트는 가짜를 주입해 실 OS 없이 계약을 본다).

    Returns:
        ``{"limit_per_bucket": int, "query_count": int, "results": {질의: {버킷: [id, ...]}}}``.
    """
    results: dict[str, dict[str, list[str]]] = {}
    for q in queries:
        results[q] = bucket_ids(search_fn(q, limit_per_bucket=limit_per_bucket))
    return {"limit_per_bucket": limit_per_bucket, "query_count": len(results), "results": results}


def _results_of(snapshot: Any, side: str) -> dict[str, dict[str, list[str]]]:
    """스냅에서 ``results`` 를 꺼내며 모양을 검증한다.

    Args:
        snapshot: ``json.load`` 로 읽은 스냅.
        side: 오류 메시지에 쓸 쪽 이름(``"before"``/``"after"``) — 어느 파일이 잘못됐는지 알려준다.

    Returns:
        ``{질의: {버킷: [id, ...]}}``.

    Raises:
        ValueError: ``results`` 가 없거나 dict 가 아닐 때(빈 스냅을 통과시키면 가짜 초록이 난다).
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("results"), dict):
        raise ValueError(f"스냅 형식 오류({side}): 'results' dict 가 필요하다")
    return snapshot["results"]


def _bucket_diffs(before: Any, after: Any) -> list[str]:
    """한 질의의 버킷들을 비교해 차이 설명 목록을 만든다.

    Args:
        before: 변경 전 그 질의의 ``{버킷: [id, ...]}``.
        after: 변경 후 같은 것.

    Returns:
        차이 설명 목록(비어 있으면 완전 일치). 버킷명 오름차순으로 결정적이다.
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    reasons: list[str] = []
    for bucket in sorted(set(before) | set(after)):
        if bucket not in after:
            reasons.append(f"버킷 {bucket} 이 after 에 없음")
            continue
        if bucket not in before:
            reasons.append(f"버킷 {bucket} 이 before 에 없음")
            continue
        a, b = list(before[bucket]), list(after[bucket])
        if a == b:
            continue
        # 첫 어긋난 자리를 함께 알려 준다 — "몇 번째부터 달라졌나"가 원인 추적의 출발점이다.
        # 길이가 다를 수 있으니 strict=False(짧은 쪽까지만 짝지어 보고, 남은 길이 차이는 위 건수로 드러난다).
        first = next(
            (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y),
            min(len(a), len(b)),
        )
        detail = f"before {len(a)}건·after {len(b)}건·첫 차이 #{first}"
        if first < len(a) and first < len(b):
            detail += f"({a[first]} → {b[first]})"
        reasons.append(f"버킷 {bucket} 불일치({detail})")
    return reasons


def compare_snapshots(before: Any, after: Any) -> list[str]:
    """두 스냅의 질의별·버킷별 id 순서가 **완전히 같은지** 검사한다(SC-04 판정).

    Args:
        before: 변경 전 스냅.
        after: 변경 후 스냅.

    Returns:
        차이 설명 줄 목록 — **질의 하나당 최대 한 줄**. 비어 있으면 랭킹 불변이 증명된 것이다.
        순서는 질의 이름 오름차순으로 결정적이다(같은 입력 → 같은 보고).

    Raises:
        ValueError: 어느 한쪽 스냅의 모양이 깨졌을 때(``_results_of``).
    """
    a = _results_of(before, "before")
    b = _results_of(after, "after")
    diffs: list[str] = []
    for query in sorted(set(a) | set(b)):
        if query not in b:
            diffs.append(f"{query}: after 스냅에 없는 질의(골든이 서로 다르다)")
            continue
        if query not in a:
            diffs.append(f"{query}: before 스냅에 없는 질의(골든이 서로 다르다)")
            continue
        reasons = _bucket_diffs(a[query], b[query])
        if reasons:
            diffs.append(f"{query}: " + " · ".join(reasons))
    return diffs


def _run_capture(golden_path: Path, out_path: Path, *, limit_per_bucket: int, env: str) -> int:
    """골든 질의 전량을 실제로 검색해 스냅 파일을 만든다(실 OpenSearch 필요).

    Args:
        golden_path: 골든 json 경로(실 자산 id 포함 — 레포 밖 비공개 파일).
        out_path: 스냅을 쓸 경로.
        limit_per_bucket: 버킷당 결과 상한(전/후 동일해야 한다).
        env: 설정 프로파일 이름(``.env.<env>`` 를 읽는다).

    Returns:
        0(성공). 검색·설정 예외는 감싸지 않고 그대로 올린다 — 실패를 빈 스냅으로 감추면
        "차이 0" 이라는 거짓 통과가 만들어진다.
    """
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / f".env.{env}", override=False)
    from src.config.settings import init_settings

    init_settings(env)
    from src.search.search_service import search_hybrid

    queries = load_queries(json.loads(golden_path.read_text(encoding="utf-8")))
    print(f"## 캡처 시작 — 질의 {len(queries)}건 · limit_per_bucket={limit_per_bucket}")

    def _search(query: str, *, limit_per_bucket: int) -> dict[str, Any]:
        """search_hybrid 를 parity 캡처 계약에 맞춰 부른다.

        Args:
            query: 질의 문자열.
            limit_per_bucket: 버킷당 결과 상한.

        Returns:
            검색 응답 dict.
        """
        return search_hybrid(query, limit_per_bucket=limit_per_bucket)

    snapshot = capture_snapshot(queries, limit_per_bucket=limit_per_bucket, search_fn=_search)
    snapshot["golden_file"] = golden_path.name  # 경로가 아니라 파일명만(비공개 경로 노출 방지)
    total = sum(len(ids) for buckets in snapshot["results"].values() for ids in buckets.values())
    if total == 0:
        # 253질의 전부 0건이면 검색이 죽었거나 응답 모양이 어긋난 것이다 — 그런 스냅 두 장을
        # 비교하면 "차이 0 = 통과"라는 거짓 초록이 나오므로 파일을 쓰지 않고 실패한다.
        print("❌ 캡처 실패 — 전 질의 결과 0건. OS/설정/응답 모양을 확인하라(빈 스냅은 저장하지 않는다).")
        return 1
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"## 캡처 완료 — {out_path} (질의 {snapshot['query_count']}건 · 결과행 {total}개)")
    return 0


def _run_compare(a_path: Path, b_path: Path) -> int:
    """두 스냅을 비교해 결과를 출력한다.

    Args:
        a_path: 변경 전 스냅 경로.
        b_path: 변경 후 스냅 경로.

    Returns:
        0=완전 일치(랭킹 불변 증명), 1=차이 있음.
    """
    a = json.loads(a_path.read_text(encoding="utf-8"))
    b = json.loads(b_path.read_text(encoding="utf-8"))
    if a.get("limit_per_bucket") != b.get("limit_per_bucket"):
        print(
            f"⚠️ limit_per_bucket 이 다르다({a.get('limit_per_bucket')} vs "
            f"{b.get('limit_per_bucket')}) — 차이가 코드 탓인지 설정 탓인지 구분할 수 없다."
        )
    diffs = compare_snapshots(a, b)
    if not diffs:
        n = len(_results_of(a, "before"))
        print(f"✅ 랭킹 불변 — 질의 {n}건의 결과 id·순위가 완전히 같다(SC-04).")
        return 0
    print(f"❌ 랭킹 변화 {len(diffs)}건 / 질의 {len(set(_results_of(a, 'before')) | set(_results_of(b, 'after')))}건")
    for line in diffs:
        print(f"  {line}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """캡처 또는 비교를 실행한다.

    Args:
        argv: 명령행 인자. ``None`` 이면 실제 인자를 읽는다(테스트 주입용).

    Returns:
        0=성공/일치, 1=차이 있음.
    """
    ap = argparse.ArgumentParser(description="검색 랭킹 불변(parity) 하니스 — 083 SC-04")
    ap.add_argument("--capture", metavar="OUT", help="스냅을 만들 경로(실 OpenSearch 필요)")
    ap.add_argument(
        "--golden", metavar="PATH",
        help="골든 질의 json 경로(--capture 에 필수 · 기본값 없음 — 실 자산 id 파일은 레포 밖에 있다)",
    )
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="두 스냅을 비교(순수 계산)")
    ap.add_argument(
        "--limit-per-bucket", type=int, default=50,
        help="버킷당 결과 상한(기본 50 · 전/후 스냅이 같아야 비교가 성립한다)",
    )
    ap.add_argument("--env", default="dev", help="설정 프로파일(.env.<env>)")
    args = ap.parse_args(argv)

    if args.capture and args.compare:
        ap.error("--capture 와 --compare 는 함께 쓸 수 없다")
    if args.compare:
        return _run_compare(Path(args.compare[0]), Path(args.compare[1]))
    if args.capture:
        if not args.golden:
            ap.error("--capture 에는 --golden <경로> 가 필요하다(골든은 레포에 없다)")
        golden_path = Path(args.golden)
        if not golden_path.is_file():
            ap.error(f"골든 파일을 찾지 못했다: {golden_path}")
        return _run_capture(
            golden_path, Path(args.capture),
            limit_per_bucket=args.limit_per_bucket, env=args.env,
        )
    ap.error("--capture <파일> 또는 --compare <A> <B> 중 하나를 지정해야 한다")
    return 1  # ap.error 가 SystemExit 를 던지므로 도달하지 않는다(정적 검사용).


if __name__ == "__main__":
    sys.exit(main())
