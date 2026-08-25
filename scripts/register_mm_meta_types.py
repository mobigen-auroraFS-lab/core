"""멀티모달 메타 **타입 어휘 등록** CLI — 코드 상수 → DB 등록 행 (spec 084 §10 · F05).

무엇을 하는 도구인가: 개체 타입 5종(인물·장소·조직·작품·사건)의 **정의문**을 ``mm_skill`` 테이블에
``skill_code='mm_meta_type'`` 행 하나로 등록·개정한다. 등록 뒤에는 그 행이 정본이 되어 개체 판정
프롬프트에 실린다(``src.mm_meta.persist.fetch_meta_type_vocab`` → ``judge.build_entity_prompt``).

🔴 **085 판정 엔진을 쓰지 않는다 — 저장소만 공유한다**(spec §10 표). 헷갈리기 쉬운 지점이라 못 박는다:

    | | 085 분류 스킬 | 이 행(타입 어휘) |
    |---|---|---|
    | 분류 대상 | **파일**(자산) | **개체**(메타) |
    | 결과 저장 | ``asset_mm_skill_label`` 행 | ``node.entity_type`` 컬럼 |
    | 판정 시점 | 별도 분류 배치 | **개체 추출과 동시**(LLM 이 "제주도/장소"를 한 번에 답한다) |

    개체 판정은 자산 요약이라는 **문맥**이 있어야 동음이의('파리' 곤충/도시)를 가를 수 있어 분류
    배치로 떼어낼 수 없다. 그래서 이 행은 **정의문을 개체 판정 프롬프트에 실어 보내는 어휘 저장소**
    일 뿐이고, 분류 배치는 이 코드를 건너뛴다(``model.NON_CLASSIFY_SKILL_CODES`` 가드 ·
    ``persist.fetch_active_skills``). 같은 이유로 ``register_mm_skill.py`` 의 표본 미리보기 게이트
    (자산 N건을 실제로 분류해 보여 주는 것)를 여기 두지 않는다 — 대상이 자산이 아니라 성립하지 않는다.

왜 CLI 를 따로 두나(``register_mm_skill.py`` 재사용 대신)
    ① **등록 내용이 사용자 파일이 아니라 레포 상수**다. 정의문 v2 는 2026-08-25 파일럿에서 흔들림
       6종(조선·통일신라·국립중앙박물관·전주시청·상주시청·우체국)을 전부 통일시킨 **측정 결과**라
       사람이 옮겨 적을 값이 아니다. 손으로 옮기면 코드 폴백과 DB 행이 갈리고, 그 차이는 판정
       결과로만 드러나 찾기 어렵다. 그래서 이 도구는 입력 파일을 받지 않고 프리셋을 그대로 올린다.
    ② **미리보기 게이트의 의미가 다르다**(위 표 — 표본 자산 판정이 성립하지 않는다). 대신 기본 동작을
       **dry-run** 으로 두어 "무엇이 등록될지"를 DB 없이 먼저 보여 준다.

실행
    conda activate AuroraFS
    python -m scripts.register_mm_meta_types              # ① 무엇이 등록될지(기본 · DB 미접속)
    python -m scripts.register_mm_meta_types --apply      # ② 실제 등록·개정
    python -m scripts.register_mm_meta_types --show       # ③ 지금 DB 에 무엇이 있나(읽기만)

종료 코드: 0=성공 · 1=프리셋 설정 오류(등록 전 거부).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.mm_classify.model import (
    MM_META_TYPE_SKILL_CODE,
    ClassificationSkill,
    SkillConfigError,
    load_skill,
)
from src.mm_classify.persist import upsert_skill
from src.mm_meta.persist import fetch_meta_type_vocab
from src.mm_meta.rules import ENTITY_TYPE_DEFS, EntityTypeDef

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 등록 행의 표시 이름(``mm_skill.name``) — 관리 화면·목록에 그대로 나가므로 무엇의 어휘인지 읽히게 둔다.
SKILL_NAME_KO = "멀티모달 메타 타입"

# 선택 정책 = **single**. 개체 하나는 타입 하나다(085 분류의 multi 기본과 다르다 — 대상이 자산이
# 아니라 개체이고, 저장 유니크 키가 ``(entity_type, entity_uid)`` 라 한 개체가 두 타입을 가지면
# 같은 이름이 두 묶음으로 갈라진다). 이 값은 **어휘 선언의 일부**이며 개체 판정 프롬프트는 이미
# "type 은 하나"를 계약으로 못 박고 있다(``judge`` 출력 계약).
SELECTION_POLICY = "single"

# 미부여 라벨명 — 085 스킬 선언의 **필수 필드**라 채우는 값이다. 개체 판정은 "타입 없음"을 라벨이
# 아니라 ``type: null`` 로 표현하므로(``judge._entity_from_entry``) 이 값은 **프롬프트로 나가지
# 않는다**. 비워 둘 수 없어 관례 표기를 쓴다.
UNASSIGNED_LABEL_KO = "해당없음"

# 최초 등록 버전. 개정 시 **DB 카운터가 +1** 되며(``upsert_skill``) 파일 선언값은 그대로여도 된다 —
# 정본은 DB 값이다. 이 어휘의 버전은 "몇 번 고쳤나"의 이력이고, 재판정 방아쇠는 문안 판
# (``judge.PROMPT_VERSION``)이다(둘의 역할이 다르다 · 아래 개정 안내 참조).
INITIAL_VERSION = 1


def build_type_skill(
    type_defs: tuple[EntityTypeDef, ...] = ENTITY_TYPE_DEFS,
) -> ClassificationSkill:
    """타입 정의문 프리셋을 **검증된 등록 선언**으로 조립한다(순수 · 파일 I/O 없음).

    검증기를 085 것(``load_skill``)으로 쓰는 이유: 저장소가 같으니 **저장 모양의 계약도 같아야**
    한다. 이 함수가 만든 선언은 ``skill_declaration`` → JSONB → ``fetch_meta_type_vocab`` 으로
    되읽혀야 하며(왕복), 한쪽만 다른 규칙을 쓰면 "등록은 됐는데 배치는 폴백으로 도는" 조용한 어긋남이
    생긴다.

    Args:
        type_defs: 등록할 정의문 목록. 기본은 코드 프리셋(v2 확정 문안) — **사람이 문안을 손으로
            옮겨 적지 않게** 하려는 것이 이 기본값의 목적이다. 테스트가 다른 값을 넣을 수 있다.

    Returns:
        검증을 통과한 ``ClassificationSkill``(``skill_code='mm_meta_type'``).

    Raises:
        SkillConfigError: 프리셋이 스킬 선언 규칙을 어길 때(라벨명 중복·정의문 누락·코드 문법 등).
            정상 프리셋에서는 나지 않으며, 상수를 잘못 고쳤을 때 **등록 전에** 드러난다.
    """
    return load_skill(
        {
            "skill": SKILL_NAME_KO,
            "skill_code": MM_META_TYPE_SKILL_CODE,
            "version": INITIAL_VERSION,
            "policy": {"selection": SELECTION_POLICY, "unassigned": UNASSIGNED_LABEL_KO},
            "labels": [
                {
                    "code": d.code,
                    "name": d.name,
                    "definition": d.definition,
                    "not": d.exclusion,
                }
                for d in type_defs
            ],
        }
    )


def format_plan_lines(skill: ClassificationSkill) -> list[str]:
    """dry-run 출력 줄을 만든다(순수 — DB 를 보지 않는다).

    보여 주는 것은 **프롬프트에 실릴 문장 그대로**다. 정의문이 곧 분류 기준이므로(085 파일럿 발견 1),
    사람이 확인해야 하는 값은 라벨 이름이 아니라 그 뒤의 정의·경계 문장이다.

    Args:
        skill: 등록 예정 스킬(``build_type_skill`` 결과).

    Returns:
        출력할 줄 목록.
    """
    lines = [
        f"[dry-run] 타입 어휘 등록 예정 — {skill.name}({skill.skill_code}) "
        f"· 정책 {skill.policy.selection} · 타입 {len(skill.labels)}종",
        "  아래 문장이 개체 판정 프롬프트의 '타입 정의' 블록으로 그대로 나간다:",
    ]
    lines += [f'    - "{lb.name}": {lb.definition}. 아닌 것: {lb.exclusion}' for lb in skill.labels]
    lines += [
        "  ⚠️ 이 행은 어휘 저장소일 뿐이다 — 085 분류 배치는 이 코드를 건너뛴다(자산을 이 라벨로"
        " 분류하지 않는다).",
        "  실제로 등록하려면 같은 명령에 --apply 를 붙인다:",
        "    python -m scripts.register_mm_meta_types --apply",
    ]
    return lines


def format_apply_lines(result: dict[str, Any]) -> list[str]:
    """등록 결과 출력 줄을 만든다(순수).

    Args:
        result: ``upsert_skill`` 반환 dict(``action``·``version``·``previous_version`` 등).

    Returns:
        출력할 줄 목록. **개정(revised)** 이면 재판정 안내를 함께 낸다 — 정의문을 고치면 기존 판정은
        낡은 문안의 산물인데, 문안 판 상수(``judge.PROMPT_VERSION``)는 그대로라 스탬프만 보고는
        드러나지 않는다. 사람이 있는 이 자리에서 알리지 않으면 아무도 모른다.
    """
    action = result["action"]
    code = result["skill_code"]
    version = result["version"]
    if action == "registered":
        lines = [
            f"[APPLY] 신규 등록: {code} v{version} (status=active · skill_id={result['skill_id']})",
            "  다음 소속 배치부터 이 정의문이 개체 판정 프롬프트에 실린다.",
        ]
    elif action == "revised":
        lines = [
            f"[APPLY] 개정: {code} v{result['previous_version']} → v{version}",
            "  🔴 정의문이 바뀌었으므로 기존 판정은 낡았다 — **재판정은 자동으로 돌지 않는다**.",
            "     (재판정 방아쇠는 문안 판 judge.PROMPT_VERSION 이고, 이 어휘 버전과는 별개다.)",
            "     기존 자산을 다시 판정하려면 소속 배치를 재판정 모드로 사람이 실행한다.",
        ]
    else:
        lines = [
            f"[APPLY] 변경 없음: {code} v{version} — 등록 내용이 같아 아무 것도 쓰지 않았다.",
            "  (버전을 올리지 않으므로 헛 재판정이 돌지 않는다)",
        ]
    return lines


def format_show_lines(defs: tuple[EntityTypeDef, ...]) -> list[str]:
    """현재 유효한 어휘 출력 줄을 만든다(순수).

    Args:
        defs: ``fetch_meta_type_vocab`` 결과(등록 행 또는 코드 폴백).

    Returns:
        출력할 줄 목록. 폴백인지 등록분인지는 로더가 로그로 알리므로 여기서는 값만 보여 준다.
    """
    lines = [f"현재 판정에 쓰이는 타입 어휘 {len(defs)}종:"]
    lines += [f'  - "{d.name}"({d.code}): {d.definition}. 아닌 것: {d.exclusion}' for d in defs]
    return lines


def run_apply(db: Any, skill: ClassificationSkill, *, upsert_fn: Any = None) -> dict[str, Any]:
    """어휘 행을 등록·개정한다(**DB 에 쓴다** · 한 트랜잭션·커밋).

    Args:
        db: DB 핸들(``transaction()`` 제공 — 정상 종료 시 커밋).
        skill: 등록할 어휘 선언.
        upsert_fn: 영속 주입 seam — ``upsert_fn(conn, skill)``. ``None``(기본)이면 085 저장 계층의
            ``upsert_skill``(같은 테이블·같은 멱등 규칙을 쓴다).

    Returns:
        ``upsert_skill`` 반환 dict.
    """
    upsert = upsert_fn if upsert_fn is not None else upsert_skill
    with db.transaction() as conn:
        return upsert(conn, skill)


def run_show(db: Any, *, fetch_fn: Any = None) -> tuple[EntityTypeDef, ...]:
    """지금 판정에 쓰이는 어휘를 읽는다(**DB 는 읽기만**).

    Args:
        db: DB 핸들(``connection()`` 제공).
        fetch_fn: 조회 주입 seam — ``fetch_fn(conn)``. ``None`` 이면 ``fetch_meta_type_vocab``
            (등록 행이 없으면 코드 프리셋으로 폴백하며 그 사실을 로그로 알린다).

    Returns:
        정의문 목록.
    """
    fetch = fetch_fn if fetch_fn is not None else fetch_meta_type_vocab
    with db.connection() as conn:
        return fetch(conn)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령행을 해석한다.

    Args:
        argv: 인자 목록. ``None`` 이면 실제 명령행을 읽는다(테스트가 주입한다).

    Returns:
        해석된 네임스페이스. ``--apply`` 와 ``--show`` 는 상호배타이며, 둘 다 없으면 **dry-run**
        이다(기본이 안전한 쪽 — 인자를 덜 준 실행이 DB 를 바꾸지 않는다).
    """
    p = argparse.ArgumentParser(
        description="멀티모달 메타 타입 어휘 등록 (spec 084 §10 · 기본은 dry-run)"
    )
    p.add_argument("--env", choices=["dev", "prod"], default="dev",
                   help="설정 프로파일(기본 dev). .env.<env> 를 읽어 초기화한다")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="실제 등록·개정(생략 시 dry-run — DB 를 열지 않는다).")
    mode.add_argument("--show", action="store_true",
                      help="지금 판정에 쓰이는 어휘를 DB 에서 읽어 보여 준다(읽기만).")
    return p.parse_args(argv)


def _init_env(env: str) -> None:
    """``.env.<env>`` 를 로드하고 설정을 확정한다(``register_mm_meta`` 관례 동형).

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
    """타입 어휘를 보여 주거나 등록한다.

    실행 순서에 뜻이 있다: **프리셋 검증 → (dry-run 이면 여기서 끝) → 환경·DB**. 상수를 잘못 고친
    실행이 설정을 요구하거나 커넥션을 잡을 이유가 없고, dry-run 이 DB 를 열면 "무엇이 등록될지 보기"가
    DB 접속 가능한 자리에서만 되는 일이 된다.

    Args:
        argv: 인자 목록. ``None`` 이면 실제 명령행을 읽는다(테스트가 주입한다).

    Returns:
        0=성공 · 1=프리셋 설정 오류(등록하지 않았다).
    """
    args = _parse_args(argv)

    skill: ClassificationSkill | None = None
    if not args.show:
        try:
            skill = build_type_skill()
        except SkillConfigError as e:
            print(f"🔴 타입 어휘 프리셋이 등록 규칙을 어긴다 — 등록하지 않았다.\n  {e}")
            return 1
        if not args.apply:
            print("\n".join(format_plan_lines(skill)))
            return 0

    _init_env(args.env)
    from src.database.postgres_util import PostgresUtil

    db = PostgresUtil()
    with db:
        if args.show:
            print("\n".join(format_show_lines(run_show(db))))
        else:
            assert skill is not None  # 위 분기에서 확정됐다(등록 모드)
            print("\n".join(format_apply_lines(run_apply(db, skill))))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
