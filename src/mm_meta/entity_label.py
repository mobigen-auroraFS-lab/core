"""개체(멀티모달 메타)에 분류 스킬 라벨을 붙인다 — 묶음의 상위 계층 (spec 087 T013·T014).

무엇을 하나: 「아이유」라는 **대상**에 `음악` 이라는 갈래를 붙인다. 지금 그 라벨은 **자산**에
붙어 있어서, 갈래로 좁히면 한 대상이 갈래마다 쪼개진다 — 실측 `경주시` 자료 6건 =
갈래없음 5 + 음료 1 + 디저트 1 → 목록 카드는 5건인데 상세로 들어가면 6건이 나왔다(같은 결함 7개).
라벨을 개체에 붙이면 갈래가 묶음의 상위 계층이 되고 그 쪼개짐이 사라진다.

🔴 **자산 라벨을 대체하지 않는다.** 둘은 답하는 질문이 다르다:
    · 자산 라벨(`asset_mm_skill_label`) = **"무엇을 받을까"** — 「한식 자료 90건 통째 zip」.
      그 90건 중 묶음에 든 것은 일부여서, 나머지를 데이터셋으로 받으려면 파일 라벨이 필요하다.
    · 개체 라벨(이 모듈) = **"무엇을 볼까"** — 「음악 갈래 → 아이유」 탐색 계층.

판정 엔진은 **085 를 그대로 쓴다**(`src.mm_classify.judge`) — 정의문·정책·`unassigned` 규약·
버전 스탬프가 모두 같다. 이 모듈이 하는 일은 ①**판정 재료를 개체 단위로 조립**하고
②**개체 키로 저장**하는 것뿐이다. 엔진을 복제하면 두 판정이 갈린다(085 의 `_reject_reserved`
주석과 같은 판단).

⚠️ FK 를 걸 수 없다: 참조 대상 `node(entity_type, entity_uid)` 의 유니크가 부분 인덱스라
PostgreSQL 이 FK 대상으로 받지 않는다. 그래서 **개체 존재 검증을 이 모듈이 한다**(v304 SQL 헤더).

🔴 시험 전제(spec 087 §3) — 합격선 A4(라벨 정확도 ≥85%)·A5(쪼개짐 0) 미달이면 폐기한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import Connection

from src.mm_classify.model import UNASSIGNED_LABEL_CODE, ClassificationSkill
from src.mm_classify.persist import DECIDED_BY_LLM

# 개체 판정 재료에 실을 구성 자산 요약 개수.
#
# 🔴 **0 이 기본이다**(2026-08-27 사용자 지적으로 확정). 구성 자산 요약을 넣으면 한 파일의 특성이
#   대상 전체를 물들일 수 있다고 판단했는데, 실측에서는 그 반대였다 — 「김밥」에 `음악·노랫말` 이
#   붙은 것을 내가 오분류로 봤으나 실제로 **더 자두의 곡 '김밥'** 자산이 구성원에 있었다(판정이
#   옳았다). 그래도 기본을 0 으로 두는 이유는 **설명문이 이미 구성 자산 전체의 종합**이라
#   중복 재료이기 때문이다. A/B 로 재고(T016) 이득이 확인되면 값을 올린다.
DEFAULT_MEMBER_SUMMARIES = 0


class EntityLabelError(RuntimeError):
    """개체 라벨 저장 계약 위반(개체 부재·빈 라벨 등). 쓰기 시도 전에 오른다."""


def build_entity_material(
    *,
    name: str,
    entity_type: str,
    description: str | None = None,
    member_summaries: Sequence[str] = (),
    max_member_summaries: int = DEFAULT_MEMBER_SUMMARIES,
) -> str:
    """개체 하나의 **판정 재료**를 조립한다(순수 · LLM·DB 호출 없음).

    085 의 프롬프트 조립기는 ``summary`` 한 덩어리를 받으므로, 개체의 여러 재료를 한 문장으로
    합쳐 넘긴다. 순서에 뜻이 있다 — **이름·타입 → 설명문 → 구성 자료**. 앞쪽이 대상의 정체이고
    뒤쪽은 보조라서, 프롬프트가 잘려도 정체가 먼저 남는다.

    Args:
        name: 개체 대표 표기(예: ``아이유``). 비어 있으면 예외.
        entity_type: 개체 타입(예: ``인물``). 비어 있으면 예외 — 같은 이름이라도 타입이 다르면
            다른 대상이다(`김밥` 이 음식과 작품으로 갈린 실례).
        description: 생성된 개체 설명문. 구성 자산 전체를 종합한 문장이라 **가장 좋은 재료**다.
            없으면(아직 생성 안 된 개체) 생략하고 나머지로 조립한다.
        member_summaries: 구성 자산 요약 목록. 기본은 **쓰지 않는다**(위 상수 주석).
        max_member_summaries: 실을 개수 상한. 0 이면 구성 자산 요약을 넣지 않는다.

    Returns:
        판정 재료 문자열.

    Raises:
        EntityLabelError: 이름이나 타입이 비었을 때.
    """
    label = (name or "").strip()
    kind = (entity_type or "").strip()
    if not label or not kind:
        raise EntityLabelError(
            f"개체 이름·타입이 있어야 재료를 만든다: name={name!r} entity_type={entity_type!r}"
        )
    parts = [f"{label}({kind})."]
    desc = (description or "").strip()
    if desc:
        parts.append(desc)
    if max_member_summaries > 0:
        picked = [s.strip() for s in member_summaries if s and s.strip()][:max_member_summaries]
        if picked:
            parts.append("구성 자료: " + " / ".join(picked))
    return " ".join(parts)


_DELETE_SQL = """
DELETE FROM entity_mm_skill_label
 WHERE entity_type = %s AND entity_uid = %s AND skill_code = %s
"""

# upsert(ON CONFLICT) 가 아니라 순수 INSERT 다 — 앞의 DELETE 가 자리를 비웠으므로 충돌이 없고,
# DO NOTHING 을 쓰면 "라벨이 같으면 버전을 안 쓴다" 는 사고가 들어올 여지가 생긴다(085 동형).
_INSERT_SQL = """
INSERT INTO entity_mm_skill_label
    (entity_type, entity_uid, skill_code, label_code, skill_version, prompt_version, decided_by)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

_ENTITY_EXISTS_SQL = """
SELECT 1 FROM node
 WHERE node_kind = 'entity' AND entity_type = %s AND entity_uid = %s
"""


def replace_entity_labels(
    conn: Connection[Any],
    *,
    entity_type: str,
    entity_uid: str,
    skill: ClassificationSkill,
    skill_version: int,
    judgement: Any,
    prompt_version: str,
    decided_by: str = DECIDED_BY_LLM,
) -> int:
    """개체 하나 × 스킬 하나의 라벨 행을 **전체 교체**한다 — DB 에 쓴다(한 트랜잭션).

    085 ``replace_asset_labels`` 와 같은 계약이다(키만 자산 → 개체):
      - 성공 판정이면 그 개체·그 스킬의 기존 행을 모두 지우고 새 라벨 행 1..N개를 넣는다(multi).
      - "해당없음"도 ``unassigned`` **단독 1행**으로 남긴다 — 판정했다는 사실이 곧 이력이다.
      - 🔴 **판정 실패(`ok=False`)면 아무 것도 쓰지 않고 0** 을 돌려준다. 행 부재 = 재대상이며,
        실패를 "판정 완료"로 굳히면 그 개체는 영구히 재판정되지 않는다.

    ``conn.transaction()`` 으로 감싸는 이유: DELETE 만 반영되고 INSERT 가 실패하면 그 개체는
    라벨을 잃는다. 전부 아니면 전무여야 한다. 호출부가 이미 트랜잭션 안이면 psycopg3 가
    SAVEPOINT 로 중첩 처리한다.

    Args:
        conn: DB 커넥션.
        entity_type: 개체 타입(자연키 절반).
        entity_uid: 개체 표기 키(자연키 절반).
        skill: 판정에 쓴 스킬(라벨 이름 → 코드 대응을 이 객체에서 얻는다).
        skill_version: 판정 당시 **DB 스킬 버전**(파일 선언값이 아니다 — 재선별 기준이 DB 카운터).
        judgement: 085 ``judge`` 가 낸 ``SkillJudgement``. ``ok=False`` 면 쓰기 0.
        prompt_version: 판정에 쓴 문안 버전. 개체 판정은 재료 조립이 다르므로 호출부가 **명시**한다
            (기본값을 두면 자산 판정과 같은 값으로 찍혀 두 판정을 구분할 수 없다).
        decided_by: 판정 경로 어휘. 기본 ``llm``.

    Returns:
        기록한 라벨 행 수(실패 판정이면 0).

    Raises:
        EntityLabelError: 개체가 ``node`` 에 없을 때(FK 를 걸 수 없어 여기서 막는다) ·
            성공 판정인데 라벨이 없을 때 · ``unassigned`` 가 다른 라벨과 함께 왔을 때.
    """
    if not getattr(judgement, "ok", False):
        return 0

    names = tuple(getattr(judgement, "label_names", ()) or ())
    if not names:
        raise EntityLabelError(
            "성공 판정인데 라벨이 없다 — 해당없음은 unassigned 로 명시돼야 한다"
            f"({entity_type}/{entity_uid} · {skill.skill_code})"
        )
    unassigned = skill.policy.unassigned
    if unassigned in names and len(names) > 1:
        raise EntityLabelError(
            f"미부여와 다른 라벨이 함께 왔다: {names} ({entity_type}/{entity_uid})"
        )

    by_name = {lb.name: lb.code for lb in skill.labels}
    codes: list[str] = []
    for nm in names:
        if nm == unassigned:
            codes.append(UNASSIGNED_LABEL_CODE)
            continue
        code = by_name.get(nm)
        if code is None:
            raise EntityLabelError(
                f"스킬 어휘 밖 라벨이다: {nm!r} ({skill.skill_code} · {entity_type}/{entity_uid})"
            )
        codes.append(code)

    with conn.cursor() as cur:
        # 개체 존재 검증 — FK 를 걸 수 없으므로 앱이 대신한다(v304 SQL 헤더). 없는 개체에 라벨을
        # 붙이면 조회 조인에서 조용히 사라져, "판정했는데 화면에 없다"를 코드에서 찾게 된다.
        cur.execute(_ENTITY_EXISTS_SQL, (entity_type, entity_uid))
        if cur.fetchone() is None:
            raise EntityLabelError(
                f"개체가 없다: {entity_type}/{entity_uid} — node(entity) 에 먼저 있어야 한다"
            )

        with conn.transaction():
            cur.execute(_DELETE_SQL, (entity_type, entity_uid, skill.skill_code))
            for code in codes:
                cur.execute(
                    _INSERT_SQL,
                    (
                        entity_type,
                        entity_uid,
                        skill.skill_code,
                        code,
                        skill_version,
                        prompt_version,
                        decided_by,
                    ),
                )
    return len(codes)


_TARGET_SQL = """
SELECT n.entity_type, n.entity_uid,
       COALESCE(n.canonical->>'name', n.entity_uid) AS name,
       n.canonical->>'description'                  AS description,
       COUNT(DISTINCT ge.src_node)                   AS members
  FROM node n
  JOIN graph_edge ge    ON ge.dst_node = n.node_id
  JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id
 WHERE n.node_kind = 'entity'
   AND rk.kind_code = 'mm_member'
   AND ge.status = ANY(%(statuses)s)
 GROUP BY n.entity_type, n.entity_uid, n.canonical
HAVING COUNT(DISTINCT ge.src_node) >= %(minsize)s
 ORDER BY COUNT(DISTINCT ge.src_node) DESC, n.entity_uid
"""


def fetch_label_targets(
    conn: Connection[Any],
    *,
    min_members: int,
    statuses: Sequence[str],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """라벨을 붙일 개체 목록을 읽는다(조회 전용 · 결정적 정렬).

    🔴 **노출 임계를 통과한 개체만** 대상이다(``min_members``). 개체 전체(1,065개)를 판정하는 것은
    시험 단계에서 과하고, 화면에 뜨지 않는 1건짜리 개체에 갈래를 붙여도 쓰이지 않는다.
    묶음이 커져 임계를 넘으면 그때 대상이 된다(다음 배치가 집는다).

    Args:
        conn: DB 커넥션.
        min_members: 최소 구성 자산 수(화면 노출 임계와 같은 값을 넘긴다).
        statuses: 셈에 넣을 엣지 상태 목록(화면과 같은 기준이어야 건수가 맞는다).
        limit: 한 번에 가져올 상한. ``None`` 이면 전량.

    Returns:
        ``[{entity_type, entity_uid, name, description, members}]`` — 구성 자산 수 내림차순.
    """
    sql = _TARGET_SQL
    params: dict[str, Any] = {"minsize": min_members, "statuses": list(statuses)}
    if limit is not None:
        sql = sql + "LIMIT %(limit)s\n"
        params["limit"] = limit
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [
            {
                "entity_type": str(r[0]),
                "entity_uid": str(r[1]),
                "name": str(r[2]),
                "description": (r[3] or None),
                "members": int(r[4]),
            }
            for r in cur.fetchall()
        ]


__all__ = [
    "DEFAULT_MEMBER_SUMMARIES",
    "EntityLabelError",
    "build_entity_material",
    "fetch_label_targets",
    "replace_entity_labels",
]
