"""멀티모달 메타 **수동 선등록** CLI — 사람이 묶음을 먼저 정의한다 (spec 084 §6-1 · T013).

무엇을 하는 도구인가: "이순신"·"아이유" 같은 **메타(개체)** 를 사람이 직접 등록한다. 소속 자산이
0건인 **빈 메타도 등록된다** — 먼저 정의해 두면 다음 소속 배치(``run_mm_meta_binding``)가 판정
결과를 그 메타에 붙인다. 등록해 두는 것이 곧 "이 묶음은 만들어도 좋다"는 승인이다.

왜 이 경로가 필요한가(발굴 모드 · spec §1-1)
    기본 모드는 ``propose`` 다 — 배치는 판정 결과가 **등록된 메타의 표기/별칭과 일치할 때만**
    소속을 만들고, 미등록 개체는 후보 리포트로만 보고한다. 그래서 이 CLI 없이는 묶음이 하나도
    생기지 않는다(잡동사니 메타를 원천 차단하려고 일부러 그렇게 잠갔다).
    비유하면 도서관 서가 이름표다 — 사서(사람)가 "여기는 이순신 칸"이라고 이름표를 붙여 두면,
    사서 보조(배치)가 새로 들어온 책을 그 칸에 꽂는다. 이름표 없는 칸은 만들지 않는다.

등록 규칙 셋(자세한 계약은 ``src/mm_meta/persist.register_mm_meta``)
    - **대표 표기는 1회 고정**이다. 이미 있는 메타(배치가 먼저 발굴한 것 포함)에 다른 표기로
      등록하면 표기는 그대로 남고 ``source`` 만 ``user`` 로 승격된다 — 그 사실을 출력에 적는다.
    - 같은 것으로 볼 다른 표기는 ``--alias`` 로 넣는다("아이유"에 ``--alias 이지은``). 별칭은
      **그 메타에만** 있는 선언이고 전역 별칭 사전이 아니다.
    - 표기 키(공백·전각·대소문자를 눌러 만든 값)가 다르면 **다른 메타**다. 'IU' 를 ``--name`` 으로
      주면 '아이유'와 별개 메타가 되므로, 같은 것으로 보려면 ``--alias`` 를 쓴다.

실행
    conda activate AuroraFS
    # ① 무엇이 등록될지 먼저 본다(DB 를 열지 않는다 · 기본 동작)
    python -m scripts.register_mm_meta --type 인물 --name 아이유 --alias 이지은
    # ② 실제 등록
    python -m scripts.register_mm_meta --type 인물 --name 아이유 --alias 이지은 --apply
    # ③ 지금 무엇이 있나(등록분만 보려면 --source user)
    python -m scripts.register_mm_meta --list --source user

종료 코드: 0=성공 · 1=입력 오류(닫힌 어휘 밖 타입·빈 표기·모드 인자 누락 — DB 를 열기 전에 끝난다).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.mm_meta.persist import (
    LIST_MM_META_DEFAULT_LIMIT,
    MM_META_SOURCE_AUTO,
    MM_META_SOURCE_USER,
    MmMetaPersistError,
    list_mm_meta,
    normalize_registration,
    register_mm_meta,
)
from src.mm_meta.rules import ENTITY_TYPE_ORDER

_REPO_ROOT = Path(__file__).resolve().parents[1]


def run_register(db: Any, plan: dict[str, Any], *, register_fn: Any = None) -> dict[str, Any]:
    """등록을 실행한다(**DB 에 쓴다** · 한 트랜잭션·커밋).

    Args:
        db: DB 핸들(``transaction()`` 제공 — 정상 종료 시 커밋).
        plan: ``normalize_registration`` 결과(검사·정규화가 끝난 요청).
        register_fn: 영속 주입 seam — ``register_fn(conn, entity_type, name, aliases=)``.
            ``None``(기본)이면 운영 경로 ``src.mm_meta.persist.register_mm_meta``.

    Returns:
        ``register_mm_meta`` 반환 dict(``action``·``name``·``aliases`` 등).
    """
    register = register_fn if register_fn is not None else register_mm_meta
    with db.transaction() as conn:
        return register(conn, plan["entity_type"], plan["name"], aliases=plan["aliases"])


def run_list(
    db: Any, *, source: str | None, limit: int, list_fn: Any = None
) -> list[dict[str, Any]]:
    """등록·발굴된 메타 목록을 조회한다(**DB 는 읽기만**).

    Args:
        db: DB 핸들(``connection()`` 제공).
        source: ``user``·``auto`` 필터. ``None`` 이면 둘 다.
        limit: 최대 행수.
        list_fn: 조회 주입 seam — ``list_fn(conn, source=…, limit=…)``. ``None`` 이면
            ``src.mm_meta.persist.list_mm_meta``.

    Returns:
        메타 행 목록(타입·표기 오름차순).
    """
    fetch = list_fn if list_fn is not None else list_mm_meta
    with db.connection() as conn:
        return fetch(conn, source=source, limit=limit)


def format_plan_lines(plan: dict[str, Any]) -> list[str]:
    """dry-run 출력 줄을 만든다(순수 — DB 를 보지 않는다).

    보여 주는 것은 **정규화 결과**다: 표기 키가 무엇이 되는지, 별칭이 몇 개로 접혔는지. 표기 키는
    같은 메타를 가르는 축이라 사람이 확인해야 하는 값이다("아이유"와 "IU"가 다른 메타인 이유).

    Args:
        plan: ``normalize_registration`` 결과.

    Returns:
        출력할 줄 목록.
    """
    aliases = ", ".join(plan["aliases"]) if plan["aliases"] else "(없음)"
    lines = [
        f"[dry-run] 등록 예정 — {plan['entity_type']} · 표기 '{plan['name']}' "
        f"(표기 키 '{plan['entity_uid']}')",
        f"  별칭: {aliases}",
        "  같은 표기 키의 메타가 이미 있으면 대표 표기는 그대로 두고 별칭·출처(user)만 갱신한다.",
        "  실제로 등록하려면 같은 명령에 --apply 를 붙인다:",
        f"  python -m scripts.register_mm_meta --type {plan['entity_type']} "
        f"--name {plan['name']}"
        + "".join(f" --alias {a}" for a in plan["aliases"])
        + " --apply",
    ]
    return lines


def format_result_lines(result: dict[str, Any]) -> list[str]:
    """등록 결과 출력 줄을 만든다(순수).

    Args:
        result: ``register_mm_meta`` 반환 dict.

    Returns:
        출력할 줄 목록. 표기가 유지됐거나(``name_kept``) 변경이 없었으면 그 사실을 함께 알린다 —
        조용히 넘기면 "등록했는데 왜 이름이 안 바뀌나"를 다시 조사하게 된다.
    """
    action = result["action"]
    head = {
        "registered": "신규 등록",
        "updated": "갱신(출처 user 승격·별칭 병합)",
        "unchanged": "변경 없음 — 등록 내용이 같아 아무 것도 쓰지 않았다",
    }.get(action, action)
    lines = [
        f"[APPLY] {head}: {result['entity_type']} · '{result['name']}' "
        f"(표기 키 {result['entity_uid']} · source={result['source']})"
    ]
    if result.get("aliases"):
        lines.append(f"  별칭: {', '.join(result['aliases'])}")
    if result.get("added_aliases"):
        lines.append(f"  이번에 추가된 별칭: {', '.join(result['added_aliases'])}")
    if result.get("name_kept"):
        lines.append(
            f"  ⚠️ 요청 표기 '{result['requested_name']}' 대신 기존 대표 표기 "
            f"'{result['name']}' 를 그대로 유지했다(표기 1회 고정 · spec §4). "
            "다른 표기로도 매칭시키려면 --alias 로 넣는다."
        )
    if action != "unchanged":
        lines.append(
            "  소속 0건은 정상이다 — 다음 소속 배치(run_mm_meta_binding)가 판정 결과를 이 메타에 "
            "붙인다(propose 모드에서는 등록된 메타만 채워진다)."
        )
    return lines


def format_list_lines(rows: list[dict[str, Any]], *, source: str | None) -> list[str]:
    """목록 출력 줄을 만든다(순수).

    Args:
        rows: ``list_mm_meta`` 결과.
        source: 적용된 필터(``None`` 이면 전체) — 0건일 때 "필터 때문인가"를 알 수 있게 함께 적는다.

    Returns:
        출력할 줄 목록.
    """
    scope = source or "전체(user+auto)"
    if not rows:
        return [
            f"등록된 멀티모달 메타 0건 (필터: {scope})",
            "  등록하려면: python -m scripts.register_mm_meta --type <타입> --name <표기> --apply",
        ]
    lines = [f"멀티모달 메타 {len(rows)}건 (필터: {scope})"]
    for row in rows:
        alias = f" · 별칭 {', '.join(row['aliases'])}" if row.get("aliases") else ""
        lines.append(
            f"  [{row['source']}] {row['entity_type']} · {row['name']} "
            f"(키 {row['entity_uid']}){alias}"
        )
    lines.append("  소속 건수·구성 자산은 메타 카드(GET /mm-meta/{type}/{uid})에서 본다.")
    return lines


def _positive_int(raw: str) -> int:
    """``--limit`` 값 파서 — 1 이상만 받는다.

    Args:
        raw: 명령행 문자열.

    Returns:
        1 이상 정수.

    Raises:
        argparse.ArgumentTypeError: 정수가 아니거나 1 미만일 때(0을 받으면 "0건"이 필터 결과인지
            상한 탓인지 구분할 수 없다).
    """
    try:
        value = int(raw)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"정수여야 한다: {raw!r}") from e
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 이상이어야 한다: {value}")
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령행을 해석한다.

    Args:
        argv: 인자 목록. ``None`` 이면 실제 명령행을 읽는다(테스트가 주입한다).

    Returns:
        해석된 네임스페이스. ``--name``(등록)과 ``--list``(조회)는 **상호배타이며 하나는 반드시**
        있어야 한다 — 기본 모드를 두면 "인자를 덜 준 실행"이 조용히 무언가를 하게 된다.
    """
    p = argparse.ArgumentParser(
        description="멀티모달 메타 수동 선등록·조회 (spec 084 §6-1 · 기본은 dry-run)"
    )
    p.add_argument("--env", choices=["dev", "prod"], default="dev",
                   help="설정 프로파일(기본 dev). .env.<env> 를 읽어 초기화한다")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--name", help="등록할 메타의 대표 표기(예: 아이유). --type 과 함께 쓴다.")
    mode.add_argument("--list", action="store_true",
                      help="등록·발굴된 메타 목록을 조회한다(읽기만).")
    p.add_argument("--type", choices=list(ENTITY_TYPE_ORDER),
                   help="개체 타입(닫힌 5종). 같은 표기라도 타입이 다르면 별개 메타다(동음이의 분리).")
    p.add_argument("--alias", action="append", default=[],
                   help="같은 메타로 볼 다른 표기(여러 번 지정 가능). 이 메타에만 적용된다.")
    p.add_argument("--apply", action="store_true",
                   help="실제 등록(생략 시 dry-run — 정규화 결과만 보여 주고 DB 를 열지 않는다).")
    p.add_argument("--source", choices=[MM_META_SOURCE_USER, MM_META_SOURCE_AUTO],
                   help="--list 필터: user=수동 등록분 · auto=배치 발굴분(생략 시 둘 다).")
    p.add_argument("--limit", type=_positive_int, default=LIST_MM_META_DEFAULT_LIMIT,
                   help=f"--list 최대 행수(기본 {LIST_MM_META_DEFAULT_LIMIT}).")
    return p.parse_args(argv)


def _init_env(env: str) -> None:
    """``.env.<env>`` 를 로드하고 설정을 확정한다(``register_mm_skill`` 관례 동형).

    Args:
        env: ``dev`` 또는 ``prod``.
    """
    from dotenv import load_dotenv

    from src.config.settings import init_settings

    dotenv_path = _REPO_ROOT / f".env.{env}"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)
    init_settings(env)


def main(argv: list[str] | None = None) -> int:
    """메타를 등록하거나 목록을 조회한다.

    실행 순서에 뜻이 있다: **입력 검사 → (dry-run 이면 여기서 끝) → 환경·DB**. 어휘 밖 타입 같은
    요청이 설정을 요구하거나 커넥션을 잡을 이유가 없고, dry-run 이 DB 를 열면 "확인만 해 보기"가
    DB 접속 가능한 자리에서만 되는 일이 된다.

    Args:
        argv: 인자 목록. ``None`` 이면 실제 명령행을 읽는다(테스트가 주입한다).

    Returns:
        0=성공 · 1=입력 오류(닫힌 어휘 밖 타입·빈 표기·``--type`` 누락).
    """
    args = _parse_args(argv)

    plan: dict[str, Any] | None = None
    if not args.list:
        if not args.type:
            print("🔴 --name 과 함께 --type 을 준다(닫힌 5종: "
                  f"{'·'.join(ENTITY_TYPE_ORDER)}). 등록하지 않았다.")
            return 1
        try:
            plan = normalize_registration(args.type, args.name, args.alias)
        except MmMetaPersistError as e:
            print(f"🔴 등록 요청이 계약을 벗어났다 — 등록하지 않았다.\n  {e}")
            return 1
        if not args.apply:
            print("\n".join(format_plan_lines(plan)))
            return 0

    _init_env(args.env)
    from src.database.postgres_util import PostgresUtil

    db = PostgresUtil()
    with db:
        if args.list:
            rows = run_list(db, source=args.source, limit=args.limit)
            print("\n".join(format_list_lines(rows, source=args.source)))
        else:
            assert plan is not None  # 위 분기에서 확정됐다(등록 모드)
            print("\n".join(format_result_lines(run_register(db, plan))))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
