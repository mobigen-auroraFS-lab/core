"""085 분류 스킬 — **영속·대상 선별**(``mm_skill`` 레지스트리 + ``asset_mm_skill_label`` 판정 행).

무엇을 하는 모듈인가: 사람이 등록한 스킬을 DB 에 올리고(등록·개정), 배치가 낸 판정을 자산별 행으로
남기고, 다음에 판정해야 할 자산을 골라 준다. 순수 로직(``model``·``prompt``·``judge``)과 달리 여기는
**DB 에 쓰는 층**이다(커넥션은 호출부가 준다 — 이 모듈은 풀을 열지 않는다).

핵심 계약 세 가지 — 전부 "스키마가 못 막는 것"이라 코드가 지킨다(spec §2 · migration-reviewer 권고
2026-08-24). 단위 테스트(`tests/test_mm_classify_persist.py`)가 이 세 가지를 봉인한다.

    ① **행 존재 = 판정 이력.** 행이 있으면 "판정했다", 없으면 "아직/실패했다"(재대상)다. 그래서
       판정 실패(``SkillJudgement.ok is False``)는 **행을 만들지 않는다** — 실패를 기록하면 LLM 장애가
       "판정 완료"로 굳어 그 자산은 영구히 재시도되지 않는다.

    ② **미부여(해당없음) 단독성.** ``unassigned`` 행은 같은 (자산, 스킬) 의 다른 라벨 행과 공존할 수
       없다("어디에도 해당 없음"과 "이 라벨에 해당함"은 동시에 참일 수 없다). PK 가
       (asset_id, skill_code, label_code) 라서 **DB 는 두 행을 정상으로 받는다** → 앱이 막는다.

    ③ **라벨이 같아도 버전은 갱신한다.** 재판정은 자산×스킬 단위 **한 트랜잭션 DELETE→INSERT 전체
       교체**다. ``ON CONFLICT … DO NOTHING`` 식으로 "같은 라벨이 이미 있으니 넘어간다"를 하면
       ``skill_version`` 이 옛 값에 머물고, 대상 선별 조건(version<현행)에 **영구히** 걸려 매 배치가
       같은 자산을 다시 판정한다(무한 백필). 비유하면 검침원이 계량기를 읽고 날짜를 안 적어 매달 같은
       집을 다시 방문하는 것이다.

한 가지 더 — **정본은 DB 등록 행**이다(spec §1). JSON 파일은 등록 CLI 의 입력 수단일 뿐이라, 배치는
파일이 아니라 행에서 스킬을 복원한다(``fetch_active_skills`` → ``skill_from_row``). 그래서 저장 모양
(``skill_declaration``)은 ``load_skill`` 로 **왕복 가능해야** 한다(테스트가 왕복을 봉인한다).

설계 배경: `specs/085-classification-skill` §2(저장·앱 불변식)·§5(대상 선별) · v302 DDL
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row

from src.database.ids import uuid7_str
from src.domain.status_vocab import MmSkillStatus
from src.mm_classify.model import (
    NON_CLASSIFY_SKILL_CODES,
    UNASSIGNED_LABEL_CODE,
    ClassificationSkill,
    load_skill,
)
from src.mm_classify.prompt import PROMPT_VERSION

# ``asset_mm_skill_label.decided_by`` 기본값 — 배치 LLM 판정. DDL 기본값과 같은 값이며(v302),
# 코드에서 명시해 넣는다(행을 만든 경로가 SQL 기본값에 숨지 않게). 확장 여지: 'manual'.
DECIDED_BY_LLM = "llm"


class SkillPersistError(ValueError):
    """앱 불변식을 깨는 영속 요청(미부여 공존·빈 라벨·중복 라벨·skill_code 부재).

    ``ValueError`` 하위형이라 호출부가 일반 값 오류로 잡아도 동작한다(``SkillConfigError`` 선례).
    이 예외가 나면 **아무 것도 쓰지 않은 상태**다 — 검사는 항상 쓰기보다 먼저 한다.
    """


def skill_declaration(skill: ClassificationSkill) -> dict[str, Any]:
    """스킬 객체 → **저장·입력 형식의 선언 dict**(순수 · ``load_skill`` 로 왕복 가능).

    ``mm_skill.policy``/``labels`` JSONB 에 담는 모양이 이 함수의 산출물이다. 왕복이 계약인 이유:
    정본은 DB 행이므로 배치는 행에서 스킬을 복원한다(``skill_from_row``) — 되돌아가지 않으면 등록한
    스킬과 배치가 쓰는 스킬이 갈린다.

    두 가지를 의도적으로 한다:
      - 파이썬 필드명 ``exclusion`` 을 **JSON 키 ``not``** 으로 되돌린다(입력 형식과 동일).
      - 생략 가능한 정책 값(``selection``·``max_labels``)을 **실체화**해 담는다 — 그래야 "선언이
        같은가" 비교(``upsert_skill``)가 파일의 표기 차이(생략/명시)에 흔들리지 않는다.

    Args:
        skill: 검증을 통과한 스킬(``load_skill`` 결과).

    Returns:
        ``{skill, skill_code, version, policy, labels}`` — ``load_skill`` 의 필수 키 전부.
    """
    return {
        "skill": skill.name,
        "skill_code": skill.skill_code,
        "version": skill.version,
        "policy": {
            "selection": skill.policy.selection,
            "unassigned": skill.policy.unassigned,
            "max_labels": skill.policy.max_labels,
        },
        "labels": [
            {
                "code": label.code,
                "name": label.name,
                "definition": label.definition,
                "not": label.exclusion,
            }
            for label in skill.labels
        ],
    }


def skill_from_row(row: dict[str, Any]) -> ClassificationSkill:
    """``mm_skill`` 조회 행 → 검증된 스킬 객체(순수 · 정본은 등록 행).

    행의 ``policy``/``labels`` 는 JSONB 라 드라이버가 dict/list 로 준다. **다시 검증을 통과시키는**
    이유: 손 SQL·구버전 행이 섞여 들어와도 조용히 쓰지 않고 그 자리에서 드러나게 하려는 것이다.

    Args:
        row: ``fetch_active_skills`` 형상의 행(``skill_code``·``name``·``version``·
            ``policy``·``labels``).

    Returns:
        복원된 ``ClassificationSkill``. ``version`` 은 **행의 값**이다(파일이 아니라 DB 카운터가
        백필 재선별의 기준이다).

    Raises:
        SkillConfigError: 행의 선언이 검증을 통과하지 못할 때.
    """
    return load_skill(
        {
            "skill": str(row["name"]),
            "skill_code": str(row["skill_code"]),
            "version": int(row["version"]),
            "policy": row["policy"],
            "labels": row["labels"],
        }
    )


def _content(name: str, policy: Any, labels: Any) -> tuple[str, Any, Any]:
    """"선언이 같은가" 비교용 내용 3종(이름·정책·라벨) — **version 은 넣지 않는다**.

    version 을 비교에 넣으면 파일의 버전 숫자만 고쳐도 개정으로 판정돼 전 자산 재분류가 돈다.
    분류를 실제로 바꾸는 것은 이름·정책·라벨(특히 정의문)이다(파일럿 발견 1).

    Args:
        name: 스킬 표시명.
        policy: 정책 dict(선언 형식).
        labels: 라벨 목록(선언 형식).

    Returns:
        비교용 튜플.
    """
    return (name, policy, labels)


def upsert_skill(conn: Any, skill: ClassificationSkill) -> dict[str, Any]:
    """스킬을 등록하거나 개정한다 — **DB 에 쓴다**(커밋은 호출부 몫).

    세 갈래로 갈린다(개정 여부 판단 기준은 자연키 ``skill_code``):
      - **없던 코드** → INSERT. ``skill_id`` 는 앱 발급 UUIDv7(헌법 6조 · PG17 에 ``uuidv7()`` 없음),
        ``version`` 은 파일이 선언한 값, ``status`` 는 ``active``.
      - **있고 선언이 다름** → UPDATE. ``version = 기존 + 1``·``updated_at = now()``. 버전이 오르는
        순간이 배치의 백필 방아쇠다(대상 선별이 version<현행 행을 다시 집는다 · SC-03).
      - **있고 선언이 같음** → **아무 것도 쓰지 않는다**(``unchanged``). 같은 파일을 두 번 apply 한
        것만으로 version 이 올라 전량 재분류(비용)가 돌면 안 된다(SC-05 멱등).

    ⚠️ 파일의 ``version`` 은 **선언**이고, 개정 후 정본은 **DB 카운터**다. 개정마다 반드시 1씩 올라야
    "version<현행" 재선별이 성립하므로, 파일 값이 뒤처져 있어도 DB 값을 기준으로 올린다. 두 값이
    다르면 반환 dict 의 ``declared_version``/``version`` 으로 호출부가 사람에게 알린다.

    ⚠️ ``status`` 는 건드리지 않는다 — 중단(``disabled``)한 스킬이 개정만으로 되살아나면 안 된다
    (활성화는 별도 결정이며 현재는 수동 SQL 이다 · spec 비범위).

    동시성: 사람이 돌리는 CLI 전용 경로라 읽고-쓰기(SELECT→INSERT/UPDATE) 사이 경합을 낙관한다.
    만약 두 등록이 겹치면 ``skill_code`` UNIQUE 제약이 INSERT 를 실패시켜 **조용한 중복 대신 오류**로
    드러난다.

    Args:
        conn: DB 커넥션(호출부 트랜잭션 안에서 돈다).
        skill: 등록할 스킬. ``skill_code`` 가 반드시 있어야 한다(자연키).

    Returns:
        ``{action, skill_id, skill_code, version, previous_version, declared_version}``.
        ``action`` 은 ``registered``·``revised``·``unchanged`` 중 하나이고, ``previous_version`` 은
        신규 등록이면 ``None`` 이다.

    Raises:
        SkillPersistError: ``skill_code`` 가 없을 때(어느 행인지 정할 수 없다). 이때 **쓰기 시도조차
            하지 않는다**.
    """
    if skill.skill_code is None:
        raise SkillPersistError(
            "skill_code 가 없다 — 스킬 JSON 의 필수 키다(등록 입력의 자기완결성 · spec 구현확정 G1)"
        )
    declaration = skill_declaration(skill)
    code = skill.skill_code
    policy_json = json.dumps(declaration["policy"], ensure_ascii=False)
    labels_json = json.dumps(declaration["labels"], ensure_ascii=False)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT skill_id, skill_code, name, version, policy, labels, status
            FROM mm_skill WHERE skill_code = %s
            """,
            (code,),
        )
        row = cur.fetchone()

        if row is None:
            skill_id = uuid7_str()
            cur.execute(
                """
                INSERT INTO mm_skill
                    (skill_id, skill_code, name, version, policy, labels, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    skill_id,
                    code,
                    skill.name,
                    skill.version,
                    policy_json,
                    labels_json,
                    MmSkillStatus.ACTIVE.value,
                ),
            )
            return {
                "action": "registered",
                "skill_id": skill_id,
                "skill_code": code,
                "version": skill.version,
                "previous_version": None,
                "declared_version": skill.version,
            }

        skill_id = str(row["skill_id"])
        current_version = int(row["version"])
        same = _content(str(row["name"]), row["policy"], row["labels"]) == _content(
            skill.name, declaration["policy"], declaration["labels"]
        )
        if same:
            # 멱등 경로 — 쓰기 0. 버전을 올리지 않으므로 배치도 움직이지 않는다.
            return {
                "action": "unchanged",
                "skill_id": skill_id,
                "skill_code": code,
                "version": current_version,
                "previous_version": current_version,
                "declared_version": skill.version,
            }

        next_version = current_version + 1
        cur.execute(
            """
            UPDATE mm_skill
               SET name = %s, version = %s, policy = %s::jsonb, labels = %s::jsonb,
                   updated_at = now()
             WHERE skill_code = %s
            """,
            (skill.name, next_version, policy_json, labels_json, code),
        )
        return {
            "action": "revised",
            "skill_id": skill_id,
            "skill_code": code,
            "version": next_version,
            "previous_version": current_version,
            "declared_version": skill.version,
        }


# 판정 행 교체 SQL — 자산×스킬 스코프. **전체 교체**라 라벨이 같아도 버전이 새로 적힌다(불변식 ③).
_DELETE_LABELS_SQL = """
DELETE FROM asset_mm_skill_label
 WHERE asset_id = %s AND skill_code = %s
"""
# upsert(ON CONFLICT) 가 아니라 순수 INSERT 다 — 앞의 DELETE 가 자리를 비웠으므로 충돌이 없고,
# DO NOTHING/DO UPDATE 를 쓰면 "라벨 같으면 버전 안 씀" 같은 사고가 다시 들어올 여지가 생긴다.
_INSERT_LABEL_SQL = """
INSERT INTO asset_mm_skill_label
    (asset_id, skill_code, label_code, skill_version, prompt_version, decided_by)
VALUES (%s, %s, %s, %s, %s, %s)
"""


def replace_asset_labels(
    conn: Any,
    *,
    asset_id: str,
    skill_code: str,
    skill_version: int,
    judgement: Any,
    prompt_version: str = PROMPT_VERSION,
    decided_by: str = DECIDED_BY_LLM,
) -> int:
    """자산 하나 × 스킬 하나의 판정 행을 **전체 교체**한다 — DB 에 쓴다(한 트랜잭션).

    성공 판정이면 그 자산·그 스킬의 기존 행을 모두 지우고 새 라벨 행 1..N개를 넣는다(multi 이므로
    여러 행). "해당없음"도 ``unassigned`` 코드 **단독 1행**으로 기록한다 — 판정했다는 사실 자체가
    이력이기 때문이다(spec §2).

    ``conn.transaction()`` 으로 감싸는 이유: DELETE 만 반영되고 INSERT 가 실패하면 그 자산은 라벨을
    잃는다. 전부 아니면 전무여야 한다. 호출부가 이미 트랜잭션 안이면 psycopg3 가 SAVEPOINT 로 중첩
    처리하므로 어느 쪽에서 불러도 원자성이 성립한다.

    Args:
        conn: DB 커넥션.
        asset_id: 판정 대상 자산.
        skill_code: 판정에 쓴 스킬의 자연키.
        skill_version: 판정 당시 **DB 스킬 버전**(파일 선언값이 아니다 — 재선별 기준이 DB 카운터다).
        judgement: ``judge`` 가 낸 ``SkillJudgement``. ``ok=False`` 면 **아무 것도 쓰지 않고 0** 을
            돌려준다(행 부재 = 재대상 · 불변식 ①).
        prompt_version: 판정에 쓴 프롬프트 문안 버전. 기본값은 현행 ``PROMPT_VERSION`` 이며,
            과거 문안으로 만든 판정을 이관할 때만 명시한다(이력 완전성 목적 — 재선별 기준은 아니다).
        decided_by: 판정 경로 어휘. 기본 ``llm``(배치 판정). 사람이 손으로 고친 행을 넣는다면
            ``manual`` 처럼 다른 값을 준다(현재 그 경로는 없다).

    Returns:
        삽입한 행 수. 실패 판정이면 ``0``.

    Raises:
        SkillPersistError: 성공 판정인데 라벨이 없거나, ``unassigned`` 가 다른 라벨과 함께 왔거나
            (불변식 ②), 같은 라벨이 중복일 때(PK 충돌 예정). 세 경우 모두 **쓰기 전에** 막는다.
    """
    if not judgement.ok:
        # 실패는 흔적을 남기지 않는다 — 다음 배치가 이 자산을 자연히 다시 집는다.
        return 0

    codes = list(judgement.label_codes)
    if not codes:
        raise SkillPersistError(
            f"성공 판정인데 라벨이 없다(asset={asset_id} skill={skill_code}) — judge 계약 위반"
        )
    if len(set(codes)) != len(codes):
        raise SkillPersistError(f"라벨 코드 중복: {codes} — PK(asset,skill,label) 충돌")
    if UNASSIGNED_LABEL_CODE in codes and len(codes) > 1:
        raise SkillPersistError(
            f"미부여({UNASSIGNED_LABEL_CODE})가 다른 라벨과 함께 왔다: {codes} — "
            "'어디에도 해당 없음'과 '이 라벨에 해당함'은 동시에 참일 수 없다(앱 불변식 · spec §2)"
        )

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_DELETE_LABELS_SQL, (asset_id, skill_code))
            for code in codes:
                cur.execute(
                    _INSERT_LABEL_SQL,
                    (asset_id, skill_code, code, skill_version, prompt_version, decided_by),
                )
    return len(codes)


def fetch_active_skills(conn: Any) -> list[dict[str, Any]]:
    """배치가 돌릴 **활성 스킬** 목록(조회 전용·결정적 정렬).

    ``disabled`` 스킬은 판정 이력을 남긴 채 배치에서만 빠진다(행은 보존 · v302 주석).

    🔴 **예약 코드는 제외한다**(``NON_CLASSIFY_SKILL_CODES`` — 현재 084 타입 어휘 ``mm_meta_type``
    하나). 그 행은 ``mm_skill`` 을 **저장소로만** 빌려 쓰는 어휘이고 분류 대상이 자산이 아니라
    개체다(spec 084 §10). 걸러 내지 않으면 어휘를 등록하는 순간 배치가 전 자산을 그 라벨로 판정한다.
    걸러 내는 자리를 SQL 이 아니라 파이썬에 둔 이유 셋: ①대상이 한 줌(스킬 몇 행)이라 질의 최적화
    이득이 없다 ②모의 커넥션 단위 테스트가 "그 행이 배치로 나가지 않는다"를 **행동으로** 봉인할 수
    있다(SQL 문자열 검사보다 강하다) ③기존 질의·바인딩을 건드리지 않아 회귀 0 이다.
    하위 조회(``fetch_asset_label_rows``)에 같은 가드를 두지 않은 것은, 라벨 행이 생기는 유일한 경로가
    이 배치이고 그 배치가 여기서 이미 걸러지기 때문이다(행이 없으므로 조인 결과도 비어 있다).

    Args:
        conn: DB 커넥션.

    Returns:
        ``[{skill_id(str), skill_code, name, version(int), policy, labels, status}]`` —
        ``skill_code`` 오름차순. ``skill_id`` 는 문자열로 정규화한다(조회행 id → str 관례).
        각 행은 ``skill_from_row`` 로 스킬 객체가 된다. 예약 코드 행은 포함되지 않는다.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT skill_id, skill_code, name, version, policy, labels, status
            FROM mm_skill
            WHERE status = %s
            ORDER BY skill_code
            """,
            (MmSkillStatus.ACTIVE.value,),
        )
        rows = cur.fetchall()
    return [
        {
            "skill_id": str(r["skill_id"]),
            "skill_code": str(r["skill_code"]),
            "name": str(r["name"]),
            "version": int(r["version"]),
            "policy": r["policy"],
            "labels": r["labels"],
            "status": str(r["status"]),
        }
        for r in rows
        if str(r["skill_code"]) not in NON_CLASSIFY_SKILL_CODES
    ]


# 대상 선별 — registered 자산 중 "이 스킬의 현행 버전 판정이 없는" 자산.
#   조건을 ``NOT EXISTS (… skill_version >= 현행)`` 으로 쓴 이유 두 가지:
#     ① multi 는 자산당 스킬당 1..N행이라 LEFT JOIN 은 같은 자산을 N번 돌려준다(중복 판정 호출).
#     ② "행 없음 OR 행의 version<현행" 과 **논리적으로 같다** — 저장이 전체 교체라 한 자산·한 스킬의
#        행들은 언제나 같은 버전이며, 현행 버전 행이 하나라도 있으면 그 자산은 판정이 끝난 것이다.
#   의료 제외 조건은 **없다**(라벨은 닫힌 어휘라 PHI 무관 · 주제 분류와 동일하게 도메인 균일 —
#   2026-07-23 도메인 제외 전면 제거 결정 · 헌법 4조).
_PENDING_ASSETS_SQL = """
SELECT a.asset_id
FROM asset a
WHERE a.status = 'registered'
  AND NOT EXISTS (
      SELECT 1 FROM asset_mm_skill_label l
      WHERE l.asset_id = a.asset_id
        AND l.skill_code = %s
        AND l.skill_version >= %s
  )
ORDER BY a.asset_id
"""


def fetch_pending_asset_ids(
    conn: Any,
    *,
    skill_code: str,
    skill_version: int,
    limit: int | None = None,
) -> list[str]:
    """이 스킬로 판정해야 할 자산 id 목록(조회 전용 · spec §5 대상 선별).

    두 부류를 함께 집는다 — **판정 행이 없는 자산**(신규 수집·이전 판정 실패)과 **구버전 판정
    자산**(스킬 개정 후 백필 대상). 실패가 행을 남기지 않는 규율(불변식 ①)이 이 선별과 한 쌍이다.

    Args:
        conn: DB 커넥션.
        skill_code: 대상 스킬의 자연키.
        skill_version: 그 스킬의 **현행 DB 버전**. 이 버전 이상으로 판정된 자산은 제외된다.
        limit: 한 번에 가져올 상한. ``None``(기본)이면 전량 — 배치가 나눠 돌 때만 준다.

    Returns:
        ``asset_id`` 문자열 목록(오름차순·결정적). 문자열 정규화는 조회행 id 관례를 따른다.
    """
    sql = _PENDING_ASSETS_SQL
    params: list[Any] = [skill_code, skill_version]
    if limit is not None:
        sql = sql + "LIMIT %s\n"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [str(r[0]) for r in cur.fetchall()]


# 자산 하나의 판정 라벨 행 — **OS ``mm_skill_labels`` 부분 업데이트 값의 원천**(spec §5).
#   왜 "방금 쓴 라벨"로 충분하지 않은가: OS 는 필드 하나에 모든 스킬의 라벨을 함께 담고 부분 갱신은
#   그 필드를 통째로 덮어쓴다 → 스킬 A 판정 후 A 의 라벨만 실어 보내면 같은 자산의 스킬 B 라벨이
#   색인에서 사라진다. 그래서 판정 뒤 그 자산의 **전 스킬 행**을 다시 읽어 키를 만든다.
#   활성 스킬로 조인해 거르는 이유: 중단(disabled)한 스킬의 라벨은 패싯 축에 노출하지 않는다
#   (spec §6) — 행은 이력으로 남기고 색인에서만 빠진다.
_ASSET_LABEL_ROWS_SQL = """
SELECT l.skill_code, l.label_code
FROM asset_mm_skill_label l
JOIN mm_skill s ON s.skill_code = l.skill_code
WHERE l.asset_id = %s
  AND s.status = %s
ORDER BY l.skill_code, l.label_code
"""


def fetch_asset_label_rows(conn: Any, asset_id: str) -> list[dict[str, str]]:
    """자산 하나의 **활성 스킬 판정 라벨** 행(조회 전용·결정적 정렬).

    산출물을 ``src.search.opensearch_sync.mm_skill_label_keys`` 에 넣으면 색인 값이 된다(미부여
    라벨 제외는 그 함수가 담당한다 — 노출 정책을 한 곳에만 둔다).

    Args:
        conn: DB 커넥션.
        asset_id: 대상 자산.

    Returns:
        ``[{skill_code, label_code}]`` — ``skill_code`` → ``label_code`` 오름차순. 판정이 없거나
        전부 비활성 스킬이면 **빈 목록**이며, 호출부는 그것을 그대로 색인에 실어 옛 라벨을 지운다.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ASSET_LABEL_ROWS_SQL, (asset_id, MmSkillStatus.ACTIVE.value))
        rows = cur.fetchall()
    return [
        {"skill_code": str(r["skill_code"]), "label_code": str(r["label_code"])} for r in rows
    ]


# 판정 재료(요약·키워드) 조회 — 프롬프트 입력. ``asset_metadata`` 는 **LEFT JOIN** 이다:
#   INNER JOIN 이면 메타가 없는 자산은 재료를 못 받아 판정도 안 되고 행도 안 생겨 대상 선별에
#   **영구히** 남는다(무한 배치). 빈 재료로라도 판정해 행을 남기고 끝내는 편이 안전하다.
#   요약은 프롬프트에서 다시 자르므로(``SUMMARY_MAX_CHARS``) 여기서는 원문을 그대로 읽는다.
_MATERIALS_SQL = """
SELECT a.asset_id,
       m.ext_meta->>'summary' AS summary,
       m.ext_meta->'keywords' AS keywords
FROM asset a
LEFT JOIN asset_metadata m ON m.asset_id = a.asset_id
WHERE a.status = 'registered'
"""


def fetch_asset_materials(
    conn: Any,
    *,
    asset_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """판정에 쓸 자산 재료(요약·키워드)를 읽는다(조회 전용·결정적 정렬).

    두 가지로 쓴다 — 등록 CLI 의 **표본 미리보기**(``asset_ids`` 없이 ``limit=N`` → asset_id 오름차순
    앞 N건 · 파일럿 방식 준용)와 배치의 **지정 자산 재료 조회**(``asset_ids`` 지정).

    Args:
        conn: DB 커넥션.
        asset_ids: 읽을 자산 id 목록. ``None``(기본)이면 registered 자산 전체에서 앞에서부터 읽는다.
        limit: 가져올 상한. ``None`` 이면 제한 없음. 표본 미리보기는 이 값으로 표본 수를 정한다.

    Returns:
        ``[{asset_id(str), summary(str), keywords(list[str])}]`` — asset_id 오름차순. 메타가 없거나
        비어 있으면 ``summary=""``·``keywords=[]`` 로 정규화한다(``None`` 이 프롬프트로 새지 않게).
    """
    sql = _MATERIALS_SQL
    params: list[Any] = []
    if asset_ids is not None:
        sql = sql + "  AND a.asset_id = ANY(%s)\n"
        params.append([str(a) for a in asset_ids])
    sql = sql + "ORDER BY a.asset_id\n"
    if limit is not None:
        sql = sql + "LIMIT %s\n"
        params.append(limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [
        {
            "asset_id": str(r["asset_id"]),
            "summary": str(r["summary"] or ""),
            "keywords": [str(k) for k in (r["keywords"] or []) if k is not None],
        }
        for r in rows
    ]


__all__ = [
    "DECIDED_BY_LLM",
    "SkillPersistError",
    "fetch_active_skills",
    "fetch_asset_label_rows",
    "fetch_asset_materials",
    "fetch_pending_asset_ids",
    "replace_asset_labels",
    "skill_declaration",
    "skill_from_row",
    "upsert_skill",
]
