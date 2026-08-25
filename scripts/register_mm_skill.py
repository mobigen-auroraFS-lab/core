"""분류 스킬 등록 CLI — **미리보기 → 등록** 2단계 게이트 (spec 085 §3 · T107).

무엇을 하는 도구인가: 사람이 손으로 쓴 분류 체계(= **스킬** JSON)를 DB(``mm_skill``)에 등록·개정한다.
등록 전에 **표본 자산 몇 건을 실제로 판정해 보여 주고**(``--preview N``), 사람이 그 결과를 확인한 뒤에야
등록(``--apply``)이 열린다.

왜 이런 절차인가(파일럿 발견 1 · `docs/분류스킬_파일럿_20260824.md`)
    파일럿에서 유일하게 의미 있던 오배정이 **정의문 문구** 탓이었다 — 정의문에 "영양 지식"이라고
    적어 두자 판정이 그 문구에 충실하게 따라갔다. 즉 **정의문이 곧 분류기**다. 그런데 등록은
    되돌리기 어렵다: 판정 행이 쌓이고 색인이 채워지고 화면에 축이 생긴다. 그래서 "이 문구가 실제로
    어떻게 나누는지"를 먼저 눈으로 보게 만든다.

🔴 게이트 방식(구현 결정 · 우회 불가·결정적)
    ``--preview`` 가 **스킴 내용에서 파생한 확인 코드**(sha256 앞 12자)를 출력하고, ``--apply`` 는
    ``--confirm <코드>`` 로 그 값을 요구한다. 코드가 내용의 지문이라 세 성질이 따라온다:
      ① 미리보기를 돌리지 않으면 코드를 알 수 없다(= 게이트).
      ② **미리보기 뒤 정의문을 고치면 코드가 달라진다** → 다시 확인해야 한다(가장 중요한 성질 —
         "확인한 그 스킴"과 "등록되는 스킴"이 어긋날 수 없다).
      ③ 파일 경로·시각·DB 상태와 무관해 언제 어디서 돌려도 같은 값이다(결정적 · 헌법 3조).
    확인 코드는 **등록 내용**(``skill_code``·이름·정책·라벨)만 본다 — ``version`` 숫자는 제외한다.
    영속 계층의 "변경 없음" 판정과 같은 기준이라(``persist._content``) 버전만 고친 no-op 등록에
    미리보기를 다시 강요하지 않는다.

정본은 **DB 등록 행**이다(spec §1). JSON 파일은 이 CLI 의 입력 수단일 뿐이며 **레포에 두지 않는다**
(사용자 소유 — 058 시드 공개 블로커 전례 회피).

실행
    conda activate AuroraFS
    python -m scripts.register_mm_skill <skill.json> --env dev --preview 100
    python -m scripts.register_mm_skill <skill.json> --env dev --apply --confirm <코드>

종료 코드: 0=성공 · 1=스킴 설정 오류(등록 전 거부) · 2=게이트 위반(미리보기 미확인·코드 불일치).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.mm_classify.judge import judge_asset_labels
from src.mm_classify.model import (
    NON_CLASSIFY_SKILL_CODES,
    UNASSIGNED_LABEL_CODE,
    ClassificationSkill,
    SkillConfigError,
    load_skill,
)
from src.mm_classify.persist import fetch_asset_materials, upsert_skill

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 확인 코드 길이(16진수 문자 수). 12자 = 48비트 — 사람이 콘솔에서 옮겨 적을 수 있을 만큼 짧고,
# 서로 다른 스킴이 우연히 같은 코드를 갖는 일은 실무 규모에서 사실상 없다. 이 값은 **보안 장치가
# 아니라 절차 장치**다(위조를 막는 것이 아니라 "미리보기를 봤다"를 표시한다).
CONFIRM_CODE_CHARS = 12

# 라벨마다 보여줄 예시 자산 수. 사람이 "이 라벨에 이런 게 들어오는구나"를 판단할 최소 표본이고,
# 더 늘리면 콘솔이 길어져 정작 분포·비율을 놓친다.
EXAMPLES_PER_LABEL = 3

# 예시 줄에 실을 요약 발췌 길이(글자). 한 줄을 넘기지 않는 선.
EXAMPLE_SUMMARY_CHARS = 60

# 예시 줄의 asset_id 표기 길이 — 앞 8자면 사람이 DB 에서 찾아보기에 충분하다(파일럿 표기 관례).
_ASSET_ID_DISPLAY_CHARS = 8


class PreviewGateError(RuntimeError):
    """미리보기 게이트 위반(확인 코드 부재·불일치) — 등록을 열지 않는다(spec SC-02).

    ``RuntimeError`` 하위형이며, 이 예외가 나면 **DB·설정을 건드리기 전 상태**다.
    """


def load_skill_file(path: Path | str) -> ClassificationSkill:
    """스킴 JSON 파일을 읽어 **검증된 스킬**로 만든다(설정 오류는 여기서 fail-fast).

    Args:
        path: 스킴 JSON 경로. 사용자 소유 파일이며 레포 자산이 아니다(spec §1).

    Returns:
        검증을 통과한 ``ClassificationSkill``.

    Raises:
        SkillConfigError: 필수 필드 누락·문법 위반·중복·상한 초과 등 설정 결함.
        OSError·json.JSONDecodeError: 파일을 읽지 못하거나 JSON 이 아닐 때(경로 오타 등).
    """
    with open(path, encoding="utf-8") as fh:
        return load_skill(json.load(fh))


def confirm_code(skill: ClassificationSkill) -> str:
    """스킬의 **등록 내용 지문**(확인 코드) — 순수·결정적.

    입력에 넣는 것과 넣지 않는 것에 뜻이 있다:
      - 넣는다: ``skill_code``(어느 행인가)·이름·정책·라벨 전문(정의문·경계) — **분류를 결정하는 것**.
      - 넣지 않는다: ``version``(숫자만 바꾼 no-op 등록에 재확인을 강요하지 않는다)·파일 경로·시각.
    문자열은 정렬된 키의 JSON 으로 정규화해 해싱하므로, 파일의 키 순서·들여쓰기·앞뒤 공백이 달라도
    같은 코드가 나온다(공백은 ``load_skill`` 이 이미 잘라 냈다).

    Args:
        skill: 검증된 스킬.

    Returns:
        16진수 소문자 ``CONFIRM_CODE_CHARS`` 자.
    """
    payload = {
        "skill_code": skill.skill_code,
        "skill": skill.name,
        "policy": {
            "selection": skill.policy.selection,
            "unassigned": skill.policy.unassigned,
            "max_labels": skill.policy.max_labels,
        },
        "labels": [
            {
                "code": lb.code,
                "name": lb.name,
                "definition": lb.definition,
                "not": lb.exclusion,
            }
            for lb in skill.labels
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:CONFIRM_CODE_CHARS]


def require_confirm(skill: ClassificationSkill, confirm: str) -> None:
    """확인 코드를 검사한다 — 틀리면 등록을 열지 않는다(게이트).

    콘솔 복사에서 흔한 잡음(앞뒤 공백·대문자)만 흡수한다. 값 자체를 느슨하게 보는 것이 아니라,
    "사람이 방금 본 코드를 옮겨 적었는가"를 확인하는 것이 목적이다.

    Args:
        skill: 등록하려는 스킬.
        confirm: 사용자가 ``--confirm`` 으로 준 값. 빈 문자열이면 미리보기를 보지 않은 것이다.

    Raises:
        PreviewGateError: 값이 없거나 현재 스킴의 코드와 다를 때. 사유에 **다시 미리보기하는 명령**을
            담는다(사람이 다음에 무엇을 할지 알아야 한다).
    """
    expected = confirm_code(skill)
    if confirm.strip().lower() != expected:
        raise PreviewGateError(
            "미리보기 확인 코드가 맞지 않는다 — 등록을 진행하지 않았다.\n"
            f"  받은 값: {confirm.strip() or '(없음)'}\n"
            "  이 스킴을 먼저 미리보기하고, 출력된 확인 코드를 그대로 넘긴다:\n"
            "    python -m scripts.register_mm_skill <파일> --preview 100\n"
            "  (미리보기 뒤 정의문을 고쳤다면 코드가 바뀐다 — 다시 미리보기해야 한다)"
        )


def sample_report(
    skill: ClassificationSkill,
    materials: list[dict[str, Any]],
    *,
    judge_fn: Any = None,
) -> dict[str, Any]:
    """표본을 판정해 **사람이 볼 요약**을 만든다(분포·해당없음 비율·실패 사유·예시).

    라벨 분포에는 **한 건도 안 나온 라벨까지 0건으로** 담는다 — 그 0이 곧 커버리지 갭 신호다
    (파일럿 발견 2: 라벨이 못 덮는 영역을 미부여가 정직하게 드러냈다).

    ``unassigned_ratio`` 의 분모는 **판정 성공 건수**다. 판정 실패는 "해당 없음"이 아니라 재대상이라
    분모에 섞으면 비율이 낙관적으로 왜곡된다.

    Args:
        skill: 판정에 쓸 스킬.
        materials: ``fetch_asset_materials`` 형상 목록(``asset_id``·``summary``·``keywords``).
            **순서를 그대로 쓴다**(asset_id 오름차순 = 결정적 표본).
        judge_fn: 판정 함수 주입 seam — ``judge_fn(skill, summary, keywords) -> SkillJudgement``.
            ``None``(기본)이면 운영 경로 ``judge_asset_labels``(LLM 단일 seam·temperature=0)를 쓴다.

    Returns:
        ``{n_sample, n_ok, n_failed, n_unassigned, unassigned_ratio, by_label,
        examples, failures}``.
        ``by_label`` 은 설정 라벨 순서 + 마지막에 미부여이며(multi 라 **합이 판정 수보다 클 수 있다**),
        ``failures`` 는 사유 문자열별 건수다.
    """
    judge = judge_fn if judge_fn is not None else judge_asset_labels

    # 0건 라벨까지 미리 깔아 둔다(설정 순서 + 마지막에 미부여) — 순서가 고정되고 "안 나온 라벨"이
    # 리포트에 드러난다. 예시 버킷은 **라벨마다 별 리스트**여야 하므로 dict.fromkeys 를 쓰지 않는다
    # (하나의 리스트를 공유하면 모든 라벨의 예시가 같아진다).
    by_label: dict[str, int] = dict.fromkeys(skill.label_codes, 0)
    by_label[UNASSIGNED_LABEL_CODE] = 0
    examples: dict[str, list[dict[str, str]]] = {code: [] for code in by_label}
    failures: dict[str, int] = {}
    n_ok = n_failed = n_unassigned = 0

    for material in materials:
        verdict = judge(skill, material.get("summary"), material.get("keywords"))
        if not verdict.ok:
            n_failed += 1
            reason = str(verdict.failure) if verdict.failure is not None else "unknown"
            failures[reason] = failures.get(reason, 0) + 1
            continue
        n_ok += 1
        if verdict.is_unassigned:
            n_unassigned += 1
        for code in verdict.label_codes:
            # 설정 밖 코드는 judge 가 이미 막지만(어휘 검증), 리포트가 KeyError 로 죽지 않게 방어한다.
            by_label[code] = by_label.get(code, 0) + 1
            bucket = examples.setdefault(code, [])
            if len(bucket) < EXAMPLES_PER_LABEL:
                bucket.append(
                    {
                        "asset_id": str(material.get("asset_id") or ""),
                        "summary": str(material.get("summary") or "")[:EXAMPLE_SUMMARY_CHARS],
                    }
                )
    return {
        "n_sample": len(materials),
        "n_ok": n_ok,
        "n_failed": n_failed,
        "n_unassigned": n_unassigned,
        # 표본이 0이거나 전부 실패면 비율을 낼 근거가 없다 → 0.0(0으로 나누지 않는다).
        "unassigned_ratio": (n_unassigned / n_ok) if n_ok else 0.0,
        "by_label": by_label,
        "examples": examples,
        "failures": failures,
    }


def _label_display(skill: ClassificationSkill) -> dict[str, str]:
    """라벨 코드 → 화면 표기(``이름(코드)``). 미부여는 스킬이 정한 이름을 쓴다.

    Args:
        skill: 대상 스킬.

    Returns:
        ``{label_code: 표기}``.
    """
    out = {lb.code: f"{lb.name}({lb.code})" for lb in skill.labels}
    out[UNASSIGNED_LABEL_CODE] = f"{skill.policy.unassigned}({UNASSIGNED_LABEL_CODE})"
    return out


def format_preview_lines(
    skill: ClassificationSkill,
    report: dict[str, Any],
    *,
    confirm: str,
    batch_enabled: bool,
) -> list[str]:
    """미리보기 출력 줄을 만든다(순수 — 직접 찍지 않는다).

    Args:
        skill: 미리본 스킬.
        report: ``sample_report`` 산출물.
        confirm: 이번 스킴의 확인 코드(등록 명령에 그대로 실어 안내한다).
        batch_enabled: 분류 배치 토글(``MM_CLASSIFY_ENABLED``). **거짓이면 경고 줄을 붙인다** —
            등록은 되지만 판정이 돌지 않아 패싯이 계속 비어 있게 되므로, 조용히 두면 나중에 원인을
            다시 찾게 된다.

    Returns:
        출력할 줄 목록.
    """
    display = _label_display(skill)
    ratio = report["unassigned_ratio"] * 100
    lines = [
        f"[미리보기] 스킬 '{skill.name}'({skill.skill_code}) v{skill.version} · "
        f"정책 {skill.policy.selection} · 라벨 {len(skill.labels)}개",
        f"  표본 {report['n_sample']}건 → 판정 성공 {report['n_ok']} · "
        f"실패 {report['n_failed']}(행 미기록·다음 배치 재대상)",
        f"  해당없음 {report['n_unassigned']}건 (성공 판정의 {ratio:.1f}% — "
        "라벨이 덮지 못한 영역의 크기다)",
        "── 라벨 분포(multi 는 라벨이 겹칠 수 있어 합이 판정 수보다 클 수 있다) ──",
    ]
    for code, count in report["by_label"].items():
        lines.append(f"  {display.get(code, code)}: {count}건")
        for example in report["examples"].get(code, []):
            aid = example["asset_id"][:_ASSET_ID_DISPLAY_CHARS]
            lines.append(f"      · {aid} {example['summary']}")
    if report["failures"]:
        lines.append("── 판정 실패 사유(설정·문안 문제 신호) ──")
        lines += [f"  {reason}: {n}" for reason, n in sorted(report["failures"].items())]
    if not batch_enabled:
        lines.append(
            "⚠️ MM_CLASSIFY_ENABLED 가 꺼져 있다 — 등록해도 분류 배치가 판정하지 않는다"
            "(등록 자체는 정상이며, 켜면 그때부터 대상 자산을 판정한다)."
        )
    lines += [
        f"확인 코드: {confirm}",
        "위 분포·예시가 의도한 분류라면 등록한다(정의문을 고치면 코드가 바뀐다):",
        f"  python -m scripts.register_mm_skill <파일> --apply --confirm {confirm}",
    ]
    return lines


def format_apply_lines(result: dict[str, Any]) -> list[str]:
    """등록 결과 출력 줄을 만든다(순수).

    Args:
        result: ``upsert_skill`` 반환 dict(``action``·``version``·``previous_version`` 등).

    Returns:
        출력할 줄 목록. 파일이 선언한 버전과 DB 확정 버전이 다르면 그 사실을 함께 알린다
        (정본은 DB 카운터이며, 개정마다 +1 이어야 백필 재선별이 성립한다).
    """
    action = result["action"]
    code = result["skill_code"]
    version = result["version"]
    if action == "registered":
        lines = [
            f"[APPLY] 신규 등록: {code} v{version} (status=active · skill_id={result['skill_id']})"
        ]
    elif action == "revised":
        lines = [
            f"[APPLY] 개정: {code} v{result['previous_version']} → v{version}",
            "  다음 분류 배치가 이 스킬의 구버전 판정 자산을 재선별해 백필한다(spec SC-03).",
        ]
    else:
        lines = [
            f"[APPLY] 변경 없음: {code} v{version} — 등록 내용이 같아 아무 것도 쓰지 않았다.",
            "  (버전을 올리지 않으므로 헛 백필이 돌지 않는다 · SC-05 멱등)",
        ]
    declared = result.get("declared_version")
    if declared is not None and declared != version:
        lines.append(
            f"  ⚠️ 파일 선언 version={declared} · DB 확정 version={version} — "
            "정본은 DB 카운터다(개정마다 +1 이어야 재선별이 성립한다). 파일을 맞춰 두면 헷갈림이 줄어든다."
        )
    return lines


def run_preview(
    db: Any,
    skill: ClassificationSkill,
    *,
    limit: int,
    judge_fn: Any = None,
    materials_fn: Any = None,
) -> dict[str, Any]:
    """표본을 조회·판정해 미리보기 요약을 만든다(**DB 는 읽기만**).

    표본은 ``asset_id`` 오름차순 앞 N건이다 — 파일럿과 같은 결정적 규칙이라 같은 코퍼스에서 같은
    표본이 뽑힌다(무작위 표본은 미리보기끼리 비교가 안 된다).

    Args:
        db: DB 핸들(``connection()`` 을 제공하는 ``PostgresUtil`` 형상).
        skill: 미리볼 스킬.
        limit: 표본 수(``--preview N``).
        judge_fn: 판정 주입 seam. ``None`` 이면 운영 LLM 경로.
        materials_fn: 표본 조회 주입 seam — ``materials_fn(conn, *, limit)``. ``None`` 이면
            ``fetch_asset_materials``(registered 자산·asset_id 오름차순).

    Returns:
        ``sample_report`` 산출물.
    """
    fetch = materials_fn if materials_fn is not None else fetch_asset_materials
    with db.connection() as conn:
        materials = fetch(conn, limit=limit)
    return sample_report(skill, materials, judge_fn=judge_fn)


def run_apply(
    db: Any,
    skill: ClassificationSkill,
    *,
    confirm: str,
    upsert_fn: Any = None,
) -> dict[str, Any]:
    """게이트를 통과하면 스킬을 등록·개정한다(**DB 에 쓴다** · 한 트랜잭션·커밋).

    게이트 검사를 **트랜잭션을 열기 전에** 한다 — 걸릴 요청이 커넥션을 잡을 이유가 없다.

    Args:
        db: DB 핸들(``transaction()`` 제공 — 정상 종료 시 커밋).
        skill: 등록할 스킬.
        confirm: 사용자가 준 확인 코드.
        upsert_fn: 영속 주입 seam — ``upsert_fn(conn, skill)``. ``None`` 이면 ``upsert_skill``.

    Returns:
        ``upsert_skill`` 반환 dict.

    Raises:
        PreviewGateError: 확인 코드가 없거나 틀릴 때(DB 를 열지 않는다).
    """
    require_confirm(skill, confirm)
    upsert = upsert_fn if upsert_fn is not None else upsert_skill
    with db.transaction() as conn:
        return upsert(conn, skill)


def _positive_int(raw: str) -> int:
    """``--preview N`` 값 파서 — 1 이상만 받는다.

    0·음수를 받으면 표본 0건 미리보기가 "아무 문제 없음"처럼 보이는 사고가 난다(빈 분포).

    Args:
        raw: 명령행 문자열.

    Returns:
        1 이상 정수.

    Raises:
        argparse.ArgumentTypeError: 정수가 아니거나 1 미만일 때(argparse 가 사용법과 함께 종료).
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
        해석된 네임스페이스. ``--preview``/``--apply`` 는 상호배타이며 **하나는 반드시** 있어야 한다
        (기본 모드를 두지 않는 이유: 무엇이 기본인지 헷갈리면 게이트의 뜻이 흐려진다).
    """
    p = argparse.ArgumentParser(
        description="분류 스킬 등록 — 표본 미리보기 확인 후 등록(spec 085 §3 게이트)"
    )
    p.add_argument("skill_file", help="스킴 JSON 경로(사용자 소유 파일 · 레포 자산 아님)")
    p.add_argument("--env", choices=["dev", "prod"], default="dev")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preview",
        type=_positive_int,
        metavar="N",
        help="표본 N건을 실제로 판정해 라벨 분포·예시·해당없음 비율과 확인 코드를 출력(DB 읽기만).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="등록·개정(--confirm 로 미리보기 확인 코드를 함께 줘야 한다).",
    )
    p.add_argument(
        "--confirm",
        default="",
        help="--apply 전용 — --preview 가 출력한 확인 코드. 스킴을 고치면 코드가 바뀐다.",
    )
    return p.parse_args(argv)


def _init_env(env: str) -> None:
    """``.env.<env>`` 를 로드하고 설정을 확정한다(seed_topic_registry 관례 동형).

    Args:
        env: ``dev`` 또는 ``prod``.
    """
    from dotenv import load_dotenv

    from src.config.settings import init_settings

    dotenv_path = _REPO_ROOT / f".env.{env}"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)
    init_settings(env)


def _batch_enabled() -> bool:
    """분류 배치 토글(``MM_CLASSIFY_ENABLED``) 을 읽는다.

    Returns:
        설정값. 설정 미초기화(단위 테스트 등)면 **기본값과 같은 True** 로 본다 — 경고를 띄우려고
        만든 조회이므로, 못 읽었을 때 없는 경고를 만들지 않는 쪽이 맞다.
    """
    from src.config.settings import get_current_settings

    try:
        return bool(get_current_settings().mm_classify.enabled)
    except RuntimeError:
        return True


def main(argv: list[str] | None = None) -> int:
    """스킬을 미리보고 등록한다.

    실행 순서에 뜻이 있다: **스킴 검증 → 게이트 검사 → 환경·DB**. 게이트에 걸릴 요청이 설정을
    요구하거나 커넥션을 잡을 이유가 없고, 설정 결함은 DB 와 무관하게 먼저 드러나야 한다.

    Args:
        argv: 인자 목록. ``None`` 이면 실제 명령행을 읽는다(테스트가 주입한다).

    Returns:
        0=성공 · 1=스킴 설정 오류 · 2=게이트 위반(미리보기 미확인·코드 불일치).
    """
    args = _parse_args(argv)
    try:
        skill = load_skill_file(Path(args.skill_file))
    except SkillConfigError as e:
        print(f"🔴 스킴 설정 오류 — 등록하지 않았다.\n  {e}")
        return 1

    # 예약 코드 가드(084 F05) — ``mm_skill`` 테이블은 타입 어휘와 저장소를 공유하지만 **엔진은
    # 공유하지 않는다**(spec 084 §10 표). 이 CLI 로 예약 코드를 쓰면 어휘 행이 자산 분류표로 덮여
    # 개체 판정의 타입 정의가 사라진다. 미리보기든 등록이든 **DB 를 열기 전에** 끊는다.
    if skill.skill_code in NON_CLASSIFY_SKILL_CODES:
        print(
            f"🔴 예약된 skill_code 다: {skill.skill_code} — 분류 스킬로 등록할 수 없다.\n"
            "  이 코드는 084 멀티모달 메타 **타입 어휘** 전용이며(자산이 아니라 개체를 나눈다),\n"
            "  등록 경로도 따로 있다: python -m scripts.register_mm_meta_types --apply"
        )
        return 1

    if args.apply:
        # 게이트를 가장 먼저 — 여기서 걸리면 DB·설정을 건드리지 않는다.
        try:
            require_confirm(skill, args.confirm)
        except PreviewGateError as e:
            print(f"🔴 {e}")
            return 2

    _init_env(args.env)
    from src.database.postgres_util import PostgresUtil

    db = PostgresUtil()
    with db:
        if args.apply:
            print("\n".join(format_apply_lines(run_apply(db, skill, confirm=args.confirm))))
        else:
            report = run_preview(db, skill, limit=args.preview)
            print(
                "\n".join(
                    format_preview_lines(
                        skill,
                        report,
                        confirm=confirm_code(skill),
                        batch_enabled=_batch_enabled(),
                    )
                )
            )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
