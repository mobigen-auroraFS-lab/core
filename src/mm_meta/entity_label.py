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

**언제 다시 판정하나(증분 · spec 104)** — 판정할 때 재료의 지문(SHA-256)을 라벨 행에 함께 남기고,
다음 배치는 지문·스킬 판·문안 판이 모두 그대로인 (개체, 스킬)을 건너뛴다(``select_label_work``).
사서가 표지가 바뀐 책과 스티커 없는 책에만 분류 스티커를 다시 붙이는 것과 같다.
🔴 상한은 **걸러낸 뒤에** 건다 — SQL 에서 「구성원 많은 순 위에서 N개」로 자르면 상위만 매번 다시
판정되고 나머지는 영영 판정되지 않는다. 지문 칸이 생기기 전의 행은 지문이 NULL 이라 첫 실행에서
노출 개체 전부를 한 번 다시 판정한다(지금 재료로 소급해 채우지 않는다 — 어떤 재료로 판정했는지 모른다).
설계 배경: ``specs/104-entity-label-incremental`` ·
``docs/decisions/2026-10-07-entity-label-incremental.md``

🔴 시험 전제(spec 087 §3) — 합격선 A4(라벨 정확도 ≥85%)·A5(쪼개짐 0) 미달이면 폐기한다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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

# 재판정 사유 — (개체, 스킬) 짝 하나에 하나. 배치 리포트·계획 보기가 사유별로 센다.
REASON_NEW = "new"  # 행 없음 — 미판정이거나 지난 판정이 실패했다(실패는 행을 남기지 않는다)
REASON_MIXED = "mixed"  # 같은 짝의 행들 값이 섞임(부분 실패·옛 코드 혼입) — 대표값을 믿을 수 없다
REASON_HASH_NULL = "hash_null"  # 지문 없는 옛 판정 — 어떤 재료로 판정했는지 모른다
REASON_HASH_CHANGED = "hash_changed"  # 재료가 바뀜(설명문 재생성·구성 요약 변화)
REASON_SKILL_VERSION = "skill_version"  # 스킬 정의 개정(DB 판 카운터가 올라감)
REASON_PROMPT_VERSION = "prompt_version"  # 판정 문안 개정

# 집계 키 목록 — ``select_label_work`` 가 ``pairs_<사유>`` 칸을 이 목록으로 미리 0 으로 채운다(리포트·
# ``--plan`` 의 칸 순서도 이 순서). 🔴 **우선순위의 정본은 ``_pair_reason`` 의 if 순서**다 — 여기는 읽기
# 편하게 같은 순서로 적어 둘 뿐이라 이 목록의 순서를 바꿔도 판정은 바뀌지 않는다. 사유를 새로 만들면
# 두 곳에 함께 넣는다(여기서 빠지면 집계에서 KeyError — 단위 테스트가 사유 상수를 다 덮는지 본다).
_REASONS: tuple[str, ...] = (
    REASON_NEW,
    REASON_MIXED,
    REASON_HASH_NULL,
    REASON_HASH_CHANGED,
    REASON_SKILL_VERSION,
    REASON_PROMPT_VERSION,
)


class EntityLabelError(RuntimeError):
    """개체 라벨 저장 계약 위반(개체 부재·빈 라벨 등). 쓰기 시도 전에 오른다."""


@dataclass(frozen=True)
class LabelState:
    """저장된 (개체, 스킬) 판정 하나의 요약 — ``fetch_label_state`` 가 만들고 선별이 읽는다.

    판정 1회는 라벨 행 1..N개(multi)로 저장되고 그 행들은 같은 지문·판을 가져야 한다. 값이 섞였으면
    ``mixed`` 가 참이고, 이때 나머지 칸은 대표값(최댓값)일 뿐이라 비교에 쓰지 않는다.
    """

    material_hash: str | None  # 판정 재료 지문. None = 지문 없는 옛 판정(NULL 행이 하나라도 있음)
    skill_version: int | None  # 판정 당시 DB 스킬 판
    prompt_version: str | None  # 판정 당시 문안 판
    mixed: bool = False  # 같은 짝 안에서 지문·스킬 판·문안 판 중 하나라도 값이 둘 이상인가


@dataclass(frozen=True)
class LabelWork:
    """이번 배치에서 판정할 개체 하나 — 그 개체에서 **판정이 필요한 스킬만** 싣는다.

    ``select_label_work`` 가 만든다. 재료 조립은 개체당 한 번이라 상한도 개체 단위로 건다.
    ``reasons[i]`` 는 ``skill_codes[i]`` 의 재판정 사유(``REASON_*``)다 — 같은 길이·같은 순서.
    """

    entity_type: str
    entity_uid: str
    members: int  # 구성 자산 수(정렬 키)
    material: str  # 판정 재료 — 후보에서 그대로 받는다(지문과 어긋나지 않게 다시 조립하지 않는다)
    material_hash: str  # 그 재료에서 선별 함수가 직접 계산한 지문 — 저장 때 라벨 행에 함께 쓴다
    skill_codes: tuple[str, ...]  # 판정할 스킬 코드(오름차순)
    reasons: tuple[str, ...]  # 스킬별 재판정 사유


# 저장할 지문의 형식 — ``label_material_hash`` 가 내는 소문자 16진 64자. 대문자·짧은 값이 들어가면
# 다음 배치의 비교가 영영 「다름」이 되고(매번 다시 판정), CHAR(64) 는 짧은 값을 공백으로 채운다.
_MATERIAL_HASH_RX = re.compile(r"[0-9a-f]{64}")


def label_material_hash(material: str) -> str:
    """판정 재료의 지문(SHA-256 16진 64자)을 만든다 — 지문이 같으면 다시 판정하지 않는다.

    개체 임베딩의 ``material_hash`` 와 **같은 규칙**(UTF-8 바이트의 SHA-256 · DB ``CHAR(64)`` 와 같은
    폭)이다. 그 함수를 import 하지 않고 같은 한 줄을 두는 이유: 임베딩 모듈은 최상단에서 임베딩
    라이브러리(torch)를 불러오는데, 이 모듈은 패키지가 즉시 import 하므로 ``import src.mm_meta`` 가
    수 초로 무거워진다. 두 값이 같다는 것은 단위 테스트가 지킨다.

    Args:
        material: ``build_entity_material`` 이 만든 판정 재료. 한 글자만 달라도 지문이 바뀐다.

    Returns:
        16진 소문자 64자.
    """
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
# material_hash 칸은 v307 이후에만 있다 — 이 SQL 은 마이그레이션이 먼저 반영된 DB 를 전제한다.
_INSERT_SQL = """
INSERT INTO entity_mm_skill_label
    (entity_type, entity_uid, skill_code, label_code, skill_version, prompt_version, decided_by,
     material_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
    material_hash: str | None = None,
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
        material_hash: 판정에 쓴 재료의 지문(``label_material_hash``). 이번에 넣는 모든 라벨 행에
            같은 값을 쓴다 — 다음 배치가 이 값으로 「재료 그대로」를 판단한다. ``None``(기본)이면
            NULL 로 남아 다음 배치에서 다시 판정된다(지문을 모르는 옛 호출부 호환). 그 밖의 값은
            **소문자 16진 64자만** 받는다 — 판정 성공 여부와 무관하게 맨 먼저 검사한다.

    Returns:
        기록한 라벨 행 수(실패 판정이면 0).

    Raises:
        EntityLabelError: ``material_hash`` 형식이 틀렸을 때(SQL 을 치기 전) · 개체가 ``node`` 에
            없을 때(FK 를 걸 수 없어 여기서 막는다) · 성공 판정인데 라벨이 없을 때 ·
            ``unassigned`` 가 다른 라벨과 함께 왔을 때.
    """
    # 지문 형식은 인자 계약이라 판정 결과를 보기 전에 막는다. ``fullmatch`` 인 이유: ``^…$`` 는 끝
    # 줄바꿈 하나를 통과시켜 65자가 CHAR(64) 에서 잘리거나 오류가 난다.
    if material_hash is not None and (
        not isinstance(material_hash, str) or _MATERIAL_HASH_RX.fullmatch(material_hash) is None
    ):
        raise EntityLabelError(
            f"지문은 소문자 16진 64자여야 한다(label_material_hash): {material_hash!r} "
            f"({entity_type}/{entity_uid} · {skill.skill_code})"
        )
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
                        material_hash,
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
        limit: 한 번에 가져올 상한. ``None`` 이면 전량. 🔴 상한을 여기(SQL) 걸면 구성원 많은 상위만
            매번 다시 판정되고 나머지는 굶는다 — 증분 배치는 ``limit=None`` 으로 전부 읽고 상한은
            ``select_label_work`` 에서 건다.

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


# (개체, 스킬)별 한 행. 판정 1회의 행들은 값이 같아야 하므로 **종류 수**로 혼합을 잡는다.
#   지문: COUNT(DISTINCT) 는 NULL 을 건너뛰어 「NULL + 값」 혼합을 못 잡으므로 COALESCE 로 NULL 도
#   한 종류로 센다('' 는 64자 지문과 겹치지 않는다). MAX 도 NULL 을 건너뛰므로 NULL 존재는
#   BOOL_OR 로 따로 본다. 스킬 판·문안 판은 NOT NULL 칸이라 그대로 센다.
_STATE_SQL = """
SELECT entity_type, entity_uid, skill_code,
       COUNT(DISTINCT COALESCE(material_hash, '')) AS hash_kinds,
       BOOL_OR(material_hash IS NULL)              AS hash_has_null,
       MAX(material_hash)                          AS hash_max,
       COUNT(DISTINCT skill_version)               AS sv_kinds,
       MAX(skill_version)                          AS sv_max,
       COUNT(DISTINCT prompt_version)              AS pv_kinds,
       MAX(prompt_version)                         AS pv_max
  FROM entity_mm_skill_label
 GROUP BY entity_type, entity_uid, skill_code
"""


def fetch_label_state(conn: Connection[Any]) -> dict[tuple[str, str, str], LabelState]:
    """저장된 개체 갈래를 (개체, 스킬)별로 요약해 읽는다(조회 전용 · 표 전체를 한 번 읽는다).

    배치 시작에 한 번 부르고, 그 결과를 ``select_label_work`` 에 넘겨 「다시 판정할 짝」을 고른다.

    Args:
        conn: DB 커넥션.

    Returns:
        ``{(entity_type, entity_uid, skill_code): LabelState}``. 행이 없는 짝은 키가 없다(= 미판정).
        지문 NULL 행이 하나라도 있으면 ``material_hash`` 는 ``None`` 이고, 같은 짝 안에 지문·판
        값이 둘 이상이면 ``mixed`` 가 참이다.
    """
    out: dict[tuple[str, str, str], LabelState] = {}
    with conn.cursor() as cur:
        cur.execute(_STATE_SQL)
        for etype, euid, code, hash_kinds, has_null, h, sv_kinds, sv, pv_kinds, pv in (
            cur.fetchall()
        ):
            out[(str(etype), str(euid), str(code))] = LabelState(
                # NULL 이 하나라도 섞였으면 MAX 가 준 값을 믿지 않는다 — 그 짝은 다시 판정한다.
                material_hash=None if has_null or h is None else str(h),
                skill_version=None if sv is None else int(sv),
                prompt_version=None if pv is None else str(pv),
                mixed=int(hash_kinds) > 1 or int(sv_kinds) > 1 or int(pv_kinds) > 1,
            )
    return out


def _pair_reason(
    st: LabelState | None,
    *,
    material_hash: str,
    skill_version: int,
    prompt_version: str,
) -> str | None:
    """(개체, 스킬) 짝 하나의 재판정 사유를 정한다(순수 · 아래 if 순서의 첫 해당 사유 하나).

    🔴 **이 if 순서가 우선순위의 정본**이다(``_REASONS`` 는 집계 키 목록일 뿐). 한 짝은 사유 하나로만
    센다 — 지문과 스킬 판이 함께 바뀌어도 앞선 「지문」 하나로 세어 집계가 이중으로 늘지 않는다.
    혼합이 지문 NULL 보다 앞인 이유: 혼합이면 대표값(지문·판) 자체를 믿을 수 없어 뒤 비교가 무의미하다.

    Args:
        st: 저장 상태. ``None`` 이면 행이 없다(미판정 · 지난 판정 실패).
        material_hash: 지금 재료의 지문.
        skill_version: 지금 DB 스킬 판.
        prompt_version: 지금 판정 문안 판.

    Returns:
        ``REASON_*`` 하나. 모두 그대로면 ``None``(판정하지 않는다).
    """
    if st is None:
        return REASON_NEW
    if st.mixed:
        return REASON_MIXED
    if st.material_hash is None:
        return REASON_HASH_NULL
    if st.material_hash != material_hash:
        return REASON_HASH_CHANGED
    if st.skill_version != skill_version:
        return REASON_SKILL_VERSION
    if st.prompt_version != prompt_version:
        return REASON_PROMPT_VERSION
    return None


def _work_order(work: LabelWork) -> tuple[int, int, str, str]:
    """작업 정렬 키 — 미판정이 있는 개체 → 구성원 수 내림차순 → ``entity_uid`` → ``entity_type``.

    미판정을 앞에 두는 이유: 갈래가 아예 없는 개체는 화면 갈래 필터에서 통째로 빠지므로, 이미
    갈래가 있는 개체를 고쳐 쓰는 것보다 급하다. 마지막 두 키는 동점을 끊는 결정적 tiebreaker 다.

    Args:
        work: 정렬할 작업.

    Returns:
        오름차순 정렬용 튜플.
    """
    new_first = 0 if REASON_NEW in work.reasons else 1
    return (new_first, -work.members, work.entity_uid, work.entity_type)


def select_label_work(
    candidates: Sequence[Mapping[str, Any]],
    state: Mapping[tuple[str, str, str], LabelState],
    skills: Sequence[Any],
    *,
    prompt_version: str,
    limit: int | None = None,
) -> tuple[list[LabelWork], dict[str, int]]:
    """이번 배치에서 판정할 개체·스킬을 고른다(순수 · DB·LLM 없음 · 입력 순서와 무관하게 결정적).

    (개체, 스킬) 짝마다 저장 상태와 지금 재료·판을 비교해 사유를 정하고, 판정할 스킬이 하나라도
    있는 개체를 작업 1건으로 묶어 정렬한다(``_work_order``). 상한은 **걸러 정렬한 뒤에** 건다 —
    먼저 자르면 이미 판정된 상위 개체가 자리를 차지해 나머지가 영영 판정되지 않는다.

    🔴 지문은 후보의 ``material`` 에서 **이 함수가 직접 계산한다**(``label_material_hash``). 후보에
    ``material_hash`` 칸이 있어도 읽지 않는다(무시) — 호출부가 다른 재료의 지문을 실어 보내면 「A 재료로
    판정하고 B 지문을 저장」해 다음 배치가 거짓으로 건너뛰기 때문이다. 다르면 오류를 내는 대신 무시를
    고른 이유: 계산이 싸고(SHA-256 한 번), 지문의 출처를 한 곳으로 줄이는 쪽이 더 단순하다.

    Args:
        candidates: 노출 개체 전부(상한 없이 읽은 것). 각 행 ``{entity_type, entity_uid, members,
            material}`` — 개체당 1행이어야 한다. ``material_hash`` 칸은 있어도 무시한다(위).
        state: ``fetch_label_state`` 결과. 지금 돌리지 않는 스킬의 키는 무시된다.
        skills: 활성 스킬 — 각 항목의 ``skill_code``·``version``(DB 판 카운터)을 쓴다.
        prompt_version: 지금 판정 문안 판. 저장된 판과 다르면 다시 판정한다.
        limit: 이번 판에서 판정할 **개체 수** 상한. ``None`` 이면 전부 · 0 이하면 빈 목록.

    Returns:
        ``(작업 목록, 집계)``. 집계 키: ``visible``(후보 개체 수) · ``skipped_unchanged``(판정할
        스킬이 없는 개체 수) · ``need``(판정이 필요한 개체 수 · 상한 전) · ``selected``(상한 뒤) ·
        ``pairs_<사유>``(사유별 (개체, 스킬) 짝 수 · 상한 전 밀린 일 전체 기준).

    Raises:
        ValueError: 같은 개체(타입·키)가 후보에 두 번 있을 때 — 같은 개체를 두 번 판정하게 되고
            어느 재료가 남는지가 입력 순서에 달려 결정성이 깨진다.
    """
    # 스킬 순서를 코드로 고정한다 — 입력 순서가 흔들려도 skill_codes·reasons 가 같게(헌법 3조).
    ordered = sorted(skills, key=lambda s: str(s.skill_code))
    stats: dict[str, int] = {"visible": len(candidates), "skipped_unchanged": 0}
    stats.update({f"pairs_{r}": 0 for r in _REASONS})

    seen: set[tuple[str, str]] = set()
    works: list[LabelWork] = []
    for cand in candidates:
        etype, euid = str(cand["entity_type"]), str(cand["entity_uid"])
        if (etype, euid) in seen:
            raise ValueError(f"후보에 같은 개체가 두 번 있다: {etype}/{euid}")
        seen.add((etype, euid))
        material = str(cand["material"])
        mhash = label_material_hash(material)  # 후보의 지문 칸은 믿지 않는다(docstring 🔴)

        codes: list[str] = []
        reasons: list[str] = []
        for skill in ordered:
            code = str(skill.skill_code)
            reason = _pair_reason(
                state.get((etype, euid, code)),
                material_hash=mhash,
                skill_version=int(skill.version),
                prompt_version=prompt_version,
            )
            if reason is None:
                continue
            codes.append(code)
            reasons.append(reason)
            stats[f"pairs_{reason}"] += 1

        if not codes:
            stats["skipped_unchanged"] += 1
            continue
        works.append(
            LabelWork(
                entity_type=etype,
                entity_uid=euid,
                members=int(cand["members"]),
                material=material,
                material_hash=mhash,
                skill_codes=tuple(codes),
                reasons=tuple(reasons),
            )
        )

    works.sort(key=_work_order)
    stats["need"] = len(works)
    if limit is not None:
        # 0 이하는 「이번 판은 판정하지 않음」 — 음수 슬라이스가 뒤에서 자르는 사고를 막는다.
        works = works[: max(0, limit)]
    stats["selected"] = len(works)
    return works, stats


__all__ = [
    "DEFAULT_MEMBER_SUMMARIES",
    "REASON_HASH_CHANGED",
    "REASON_HASH_NULL",
    "REASON_MIXED",
    "REASON_NEW",
    "REASON_PROMPT_VERSION",
    "REASON_SKILL_VERSION",
    "EntityLabelError",
    "LabelState",
    "LabelWork",
    "build_entity_material",
    "fetch_label_state",
    "fetch_label_targets",
    "label_material_hash",
    "replace_entity_labels",
    "select_label_work",
]
