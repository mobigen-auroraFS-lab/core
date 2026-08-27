"""084 멀티모달 메타 — **영속**(카탈로그 행·메타 노드·소속 엣지·판정 이력).

무엇을 하는 모듈인가: 판정(``judge``)과 규칙(``rules``)을 통과한 개체 목록을 DB 에 남긴다. 순수
로직과 달리 여기는 **쓰는 층**이다(커넥션은 호출부가 준다 — 이 모듈은 풀을 열지 않는다).

마이그레이션은 0 이다(헌법 6조). 쓰는 곳은 전부 기존 테이블이다:

    ``relation_kind``  — ``mm_member`` 종류 **데이터 행** 1개(스키마 아님).
    ``node``           — ``node_kind='entity'`` 메타 노드(부분 유니크 ``uq_node_entity``).
    ``graph_edge``     — 자산 노드 → 메타 노드, ``status='proposed'``·``confidence`` NULL.
    ``asset_lineage``  — 판정 이력 ``entity.judged.v1``(백필 재선별의 근거).

이 층이 지키는 계약 다섯 가지 — 전부 **스키마가 막아 주지 못하는 것**이라 코드가 지킨다.
단위 테스트(`tests/test_mm_meta_persist.py`)가 이 다섯을 봉인한다.

    ① **대표 표기 1회 고정**(spec §4). 같은 (타입, 표기 키) 노드가 이미 있으면 표기를 덮지 않는다.
       "최빈 원표기"는 전량 배치에서만 계산되는 값이라 증분 배치에서는 입력 순서에 따라 결과가
       달라진다(2026-08-21 정정). 비유하면 상호 등록이다 — 먼저 등록된 간판을 나중 손님이 바꾸지
       못한다. 표기를 사람이 정하고 싶으면 수동 선등록(T013)으로 먼저 만든다.

    ② **자산 스코프 교체**(spec §6). 재판정은 그 자산의 기존 ``mm_member`` 엣지를 모두 지우고 다시
       넣는다. 지우는 범위는 **그 자산 · 그 종류**로 좁힌다 — 종류를 안 좁히면 자산↔자산 관계 엣지가
       함께 사라진다.

    ③ **멱등**. 같은 판정을 두 번 영속해도 엣지 총수는 늘지 않는다(②의 결과다 — 교체가 곧 멱등).

    ④ **버전 스탬프**. ``reason`` 에 ``kw=<원문 키워드>|pv=<프롬프트 판>|rv=<규칙 판>`` 을 고정 형식
       으로 남긴다. 배치의 재선별이 이 값을 읽어 "옛 규칙으로 만든 엣지"를 찾으므로(구현 확정 3),
       형식이 흔들리면 백필이 멈춘다. 만들기·읽기를 ``format_member_reason``/``parse_member_reason``
       한 쌍에 모아 두는 이유가 그것이다.

    ⑤ **침묵 금지**. ``mm_member`` 카탈로그 행이 없거나 비활성이면 **예외**다. 조용히 0건을 쓰면
       "묶음이 왜 안 생기나"를 매번 다시 조사한다(닫힌 taxonomy 시드 누락으로 관계가 0건이던 선례).

호출 순서(배치 · 파이프 ``run_mm_meta_binding`` · G3):

    0. ``fetch_meta_type_vocab(conn)`` — 타입 **정의문**(F05 · spec §10). 배치 시작에 한 번 읽어
       ``judge_asset_entities(type_defs=)`` 로 넘기고, 스탬프는 같은 값을
       ``judge.prompt_version_for`` 로 정한다. 등록 행이 없으면 코드 프리셋으로 폴백한다(로그로 알림).
    1. ``ensure_mm_member_kind(conn)`` — 배치 시작에 한 번.
    2. ``fetch_official_name_index(conn)`` — 공식 표기 색인((타입, 몸통 표기 키) → 공식 표기)을
       ``apply_rules(official_index=)`` 로 넘긴다(구현 확정 1·2). 🔴 **배치 시작에 한 번만** —
       자산마다 만들면 자산 수 × 개체 수가 된다(2026-08-24 결함 ② 수정).
    3. ``fetch_registered_alias_index(conn)`` — 등록 메타 별칭 색인. **배치 시작에 한 번만** 읽는다
       (판정 건마다 DB 를 찾으면 자산 수 × 개체 수만큼 질의가 늘어난다).
    4. 자산마다 ``judge`` → ``apply_rules`` → ``resolve_registered_aliases`` → ``upsert_entity_edges``.
       🔴 **판정 실패(``ok=False``)면 4번을 부르지 않는다** — 이력을 남기지 않는 것이 재대상 규율이다
       (부르면 "판정 완료·개체 0"으로 굳어 그 자산은 영구히 다시 판정되지 않는다 · spec §2).
       🔴 3·4 의 별칭 치환을 빼먹으면 "이지은"이 "아이유" 메타에 붙지 않고 **별개 메타**가 된다
       (spec §6-1 이 성립하지 않는다 — 테스트 ``test_without_index_alias_makes_separate_meta`` 가
       그 대조군이다).

수동 선등록(spec §6-1)은 배치와 **별 경로**다: 사람이 ``register_mm_meta`` (또는 등록 CLI
``scripts/register_mm_meta.py``)로 메타를 먼저 만들고, 발굴 모드 ``propose`` 에서는 배치가 그
등록분에만 자산을 붙인다. 즉 등록이 곧 후보 승인이다.

이름에 대해: 물리 계층은 ``node_kind='entity'``·``entity_*`` 그대로이고 계보 activity 도
``entity.judged.v1`` 이다(도메인·화면 이름만 "멀티모달 메타" · ADR 2026-08-24).

설계 배경: `specs/084-entity-bundle` spec §4(영속 계약)·§6(배치·이력) · plan 확인점 1~3
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from src.database.ids import uuid7_str
from src.database.lineage_persist import record_lineage
from src.domain.status_vocab import GraphEdgeStatus, MmSkillStatus
from src.domain.text_norm import normalize_text_key

# 타입 어휘 행(F05)은 085 저장 계층의 **검증기**를 그대로 빌려 쓴다(``skill_from_row`` → ``load_skill``).
# 저장소(``mm_skill``)를 공유하니 검증도 공유하는 것이 맞다 — 사본을 만들면 등록이 통과시킨 행을
# 읽기가 거부하는 어긋남이 생긴다. 🔴 빌려 쓰는 것은 **저장·검증뿐, 판정 엔진이 아니다**(spec §10 표).
from src.mm_classify.model import SkillConfigError
from src.mm_classify.persist import DefinitionTable, skill_from_row, upsert_definition_row

# 재료 상한(150자)의 **정본은 문안 쪽**이다 — 실제로 프롬프트를 자르는 층이 계약을 소유해야 한다.
# 여기서 다시 정의하면 두 값이 갈릴 수 있고(조회는 150, 문안은 120 같은 상태) 그때 프롬프트가
# 조용히 짧아진다. 방향 의존(persist → describe)은 안전하다: describe 는 DB·persist 를 모른다.
from src.mm_meta.describe import MEMBER_SUMMARY_MAX_CHARS
from src.mm_meta.judge import PROMPT_VERSION
from src.mm_meta.rules import (
    ENTITY_TYPE_DEFS,
    ENTITY_TYPE_ORDER,
    ENTITY_TYPES,
    RULE_VERSION,
    EntityTypeDef,
    ExtractedEntity,
    build_official_name_index,
)
from src.relations.graph_persist import ensure_asset_node
from src.relations.relation_type_catalog import (
    ensure_relation_kind_for_llm_proposal,
    fetch_relation_kind,
)
from src.relations.schema import MM_MEMBER_KIND_CODE

# 카탈로그 행의 표시 이름·설명. 화면·검토 목록에 이 문자열이 그대로 나가므로 **무엇에서 무엇으로
# 가는 엣지인지**와 **비대칭**임을 문장에 담는다(대칭으로 오해하면 조회를 양방향으로 짜게 된다).
MM_MEMBER_KIND_NAME_KO = "멀티모달 메타 소속"
MM_MEMBER_KIND_DESCRIPTION = (
    "멀티모달 메타 소속(자산→개체·비대칭). 084 배치가 만드는 자산→메타 엣지이며, "
    "자산 간 관계가 아니라서 LLM 관계 프롬프트 카탈로그에서는 제외된다."
)

# 판정 이력 activity — 관계의 ``relations.proposed.v1`` 선례와 같은 표기 규약(``<대상>.<사건>.<판>``).
# 🔴 이 문자열이 **백필 대상 선별의 축**이다(이력 없음 = 재대상). 바꾸면 과거 판정이 전부 "이력 없음"이
#    되어 전량 재판정이 돈다 — 바꿀 이유가 생기면 그것은 재판정 결정과 함께여야 한다.
LINEAGE_ACTIVITY = "entity.judged.v1"
# 이력의 수행 주체(누가 했나). 배치 러너 이름과 같게 둔다 — 계보만 보고 어느 실행 경로가 만든
# 판정인지 알 수 있어야 한다(관계는 ``llm_propose`` 를 쓴다).
LINEAGE_AGENT = "mm_meta_binding"

# reason 스탬프 구분자·키. 형식은 ``kw=<키워드>|pv=<문안 판>|rv=<규칙 판>`` 고정이다.
_STAMP_SEP = "|"
_STAMP_KEYS = ("kw", "pv", "rv")

# 메타의 출처 표식(``canonical.source``) — **등록(사람)과 발굴(배치)을 가른다**(spec §6-1).
#   화면이 "사용자가 정의한 묶음"과 "자동으로 찾은 묶음"을 다르게 신뢰 표시할 수 있고, 배치가
#   "사람 소유 필드(name·aliases)는 건드리지 않는다"를 판단하는 근거이기도 하다.
#   ``auto`` 는 **부재의 뜻**이다 — 배치가 만든 노드에는 이 키가 없고(``ensure_entity_node``),
#   읽는 쪽이 없으면 ``auto`` 로 본다(``mm_meta_bundle`` 도 같은 규칙).
MM_META_SOURCE_USER = "user"
MM_META_SOURCE_AUTO = "auto"

# 소속 엣지를 **셀 때 보는 상태**. 두 곳이 이 값을 쓴다 — ①공식 표기 색인의 묶음 크기
# (``fetch_official_name_index``) ②설명 대상·재료 조회(spec §8-1). 초기에는 전건 ``proposed`` 라
# 이 집합에 proposed 가 없으면 기능이 통째로 무동작이다(081 번들 active-0 무동작의 재발 방지 · spec §7).
# 🔴 카드 조회(``graph_query`` 의 기본 상태)와 **같은 집합**이어야 한다 — 다르면 카드가 "확인된
#    2건"이라 적는데 설명·병합 판단은 3건 기준이 되고, 사용자는 어느 쪽을 믿을지 모른다.
#    두 상수를 import 로 잇지 않는 이유는 ``persist``(쓰는 층) → ``graph_query``(읽는 층) 방향
#    의존을 새로 만들지 않기 위해서다 — 대신 테스트가 두 값을 묶어 둔다
#    (`tests/test_mm_meta_persist.TestVisibleStatusesDrift`).
MM_META_VISIBLE_STATUSES = (GraphEdgeStatus.ACTIVE.value, GraphEdgeStatus.PROPOSED.value)

# 폴백·경고를 알리는 통로(레포 관례: 모듈 이름 로거). 배치 로그에 한 줄로 남는다.
_LOG = logging.getLogger(__name__)


class MmMetaPersistError(ValueError):
    """영속 계약을 깨는 요청(어휘 밖 타입·빈 표기·중복 개체·카탈로그 미등록·어휘 행 불일치).

    ``ValueError`` 하위형이라 호출부가 일반 값 오류로 잡아도 동작한다(``SkillPersistError`` 선례).
    이 예외가 나면 **아무 것도 쓰지 않은 상태**다 — 검사는 항상 쓰기보다 먼저 한다.
    """


# ── 타입 어휘(F05 · spec §10) ───────────────────────────────────────────────────
# 개체 타입 5종의 **정의문**을 담은 등록 행을 읽는다. 저장소는 085 의 ``mm_skill`` 을 빌려 쓰지만
# (마이그레이션 0) 판정 엔진은 공유하지 않는다 — 이 어휘는 자산이 아니라 **개체**를 나누고, 판정은
# 개체 추출과 같은 LLM 호출에서 난다(문맥이 있어야 '파리' 곤충/도시를 가른다).
#   ⚠️ 바인딩 순서: (skill_code, status). 자연키로 좁히므로 행은 0 또는 1개다.
#   ⚠️ ``status`` 필터가 곧 **되돌리기 스위치**다 — 행을 지우지 않고 ``disabled`` 로 내리면 코드
#      프리셋(검증된 옛 문안)으로 돌아간다.
# 타입 어휘 조회 — 정본 테이블은 ``mm_meta_type_vocab``(spec 086 · v303).
#
# 왜 별칭(``types AS labels``·``vocab_code AS skill_code``)을 붙이나: **정의문 문서의 모양**은 085 와
# 같으므로 검증기(``skill_from_row`` → ``load_skill``)를 계속 공유한다 — 같은 모양을 두 벌 검증하면
# 언젠가 한쪽만 고쳐진다. 갈라진 것은 "무엇을 판정하는가"(자산 vs 개체)이고, "정의문이 올바른가"는
# 여전히 같은 질문이다. 컬럼 이름을 도메인 언어로 바꾼 이유는 v303 SQL 헤더에 있다.
_META_TYPE_VOCAB_SQL = """
SELECT vocab_id AS skill_id, vocab_code AS skill_code, name, version, policy,
       types AS labels, status
FROM mm_meta_type_vocab
WHERE vocab_code = %s AND status = %s
"""

# 어휘 행의 자연키 — 지금은 한 벌뿐이다. 084 F04(다도메인 개체 공간 분리)에서 도메인별 어휘가
# 필요해지면 이 값이 인자가 된다(테이블에 UNIQUE 를 미리 둔 이유).
DEFAULT_VOCAB_CODE = "default"

# 어휘 테이블 서술 — 등록/개정/멱등 3방향 규칙을 085 저장 계층과 **공유**한다.
_VOCAB_TABLE = DefinitionTable(
    table="mm_meta_type_vocab",
    id_col="vocab_id",
    code_col="vocab_code",
    defs_col="types",
)


def upsert_meta_type_vocab(conn: Any, skill: Any) -> dict[str, Any]:
    """개체 타입 어휘를 등록·개정한다(정본 테이블 ``mm_meta_type_vocab`` · spec 086).

    ``mm_classify`` 의 3방향 규칙을 그대로 쓴다 — 없으면 INSERT · 선언이 다르면 ``version+1`` ·
    같으면 쓰기 0(멱등). 버전이 오르는 순간이 개체 재판정의 방아쇠다(084 §10).

    ⚠️ 넘어오는 ``skill.skill_code`` 는 **어휘 자연키**로 쓰인다. 등록 CLI 가 이 값을
    ``DEFAULT_VOCAB_CODE`` 로 맞춰 준다 — 옛 예약 코드(``mm_meta_type``)를 그대로 쓰면 v303 이후엔
    존재하지 않는 어휘 코드로 새 행을 만들어 버린다.

    Args:
        conn: DB 커넥션(호출부 트랜잭션 안에서 돈다).
        skill: 검증을 통과한 어휘 선언(085 ``ClassificationSkill`` 모양 — 문서 모양이 같다).

    Returns:
        ``upsert_definition_row`` 반환 dict(``action``·``version``·``previous_version`` 등).
        키 이름은 085 계약을 따른다(``skill_id``·``skill_code``) — CLI 출력이 이미 그 이름을 쓴다.
    """
    return upsert_definition_row(conn, skill, spec=_VOCAB_TABLE)


def fetch_meta_type_vocab(conn: Connection[Any]) -> tuple[EntityTypeDef, ...]:
    """개체 타입 **정의문**을 읽는다(등록 행이 정본 · 없으면 코드 프리셋 폴백 · spec §10).

    무엇에 쓰나: 판정 프롬프트에 실을 "이 뜻으로만 판정하라" 블록의 재료다. 배치가 **시작에 한 번**
    읽어 ``judge.build_entity_prompt(type_defs=)``·``judge_asset_entities(type_defs=)`` 로 넘긴다
    (자산마다 읽으면 자산 수만큼 질의가 는다 — 색인 두 벌과 같은 규율).

    왜 DB 인가: 타입이 이름뿐이면 경계를 LLM 상식이 정해 같은 개체가 자산마다 다른 타입으로
    갈렸다. 정의문을 붙이자 그 흔들림이 거의 전부 통일됐고(2026-08-25 파일럿 · 수치는 spec §10),
    그 문구를 **코드 배포 없이** 고칠 수 있어야 어휘를 다듬어 갈 수 있다.

    세 갈래로 갈린다:
      - **등록 행 있음(active)** → 그 정의문을 순서 그대로 돌려준다. 검증은 085 저장 계층의
        ``skill_from_row``(=``load_skill``)를 빌려 쓴다 — 저장소를 공유하니 검증도 공유한다(라벨명
        중복·정의문 누락·코드 문법은 그쪽이 이미 막는다).
      - **행 없음·비활성** → 코드 프리셋(``rules.ENTITY_TYPE_DEFS``)으로 **폴백**하고 경고를 남긴다.
        부트스트랩·새 배포·되돌리기에서 배치가 죽는 것보다 검증된 옛 문안으로 도는 편이 낫다. 조용히
        폴백하지 않는 이유: "등록했는데 왜 그대로인가"를 다시 조사하게 되기 때문이다.
      - **어휘가 비었거나 이름이 겹침** → **예외**(fail-fast). 빈 어휘로 판정하면 전 자산이 개체 0
        이 되고 원인이 안 보인다. 이름이 겹치면 어느 정의문이 프롬프트에 실릴지가 우연이 된다.
        저장 유니크 키가 ``(entity_type, entity_uid)`` 라 그 사고가 노드·엣지로 굳는다.
        🔴 **"코드 프리셋과 정확히 같아야 한다"는 규칙은 없앴다**(spec 087 T003 · 2026-08-27).
        그 규칙은 코드 상수가 판정 어휘의 정본이던 시절의 것이고, 지금은 판정·저장이 이 어휘를
        주입받으므로(T001·T002) **어휘를 늘리는 것이 정상 운영**이다. 늘리려고 코드를 배포해야
        했던 것이 그 규칙의 부작용이었다.

    Args:
        conn: DB 커넥션(읽기 전용 — 이 함수는 쓰지 않는다).

    Returns:
        ``EntityTypeDef`` 튜플. 등록 행이면 **행에 적힌 순서**(프롬프트 나열 순서가 된다), 폴백이면
        코드 프리셋 순서(``ENTITY_TYPE_ORDER``와 같다).

    Raises:
        MmMetaPersistError: 등록 행에 타입이 없거나 이름이 겹치거나, 행 모양이 085 스킬 검증을
            통과하지 못할 때(정의문 누락·라벨명 중복 등). 폴백으로 얼버무리지 않는다 — 등록해 둔
            어휘가 조용히 무시되면 그 배치의 판정 전체가 의도와 다른 문안으로 돈다.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_META_TYPE_VOCAB_SQL, (DEFAULT_VOCAB_CODE, MmSkillStatus.ACTIVE.value))
        row = cur.fetchone()

    if row is None:
        _LOG.warning(
            "타입 어휘 등록 행이 없다(mm_meta_type_vocab · vocab_code=%s · active) — "
            "코드 프리셋 %d종으로 판정한다. 등록: python -m scripts.register_mm_meta_types --apply",
            DEFAULT_VOCAB_CODE,
            len(ENTITY_TYPE_DEFS),
        )
        return ENTITY_TYPE_DEFS

    try:
        skill = skill_from_row(dict(row))
    except SkillConfigError as e:
        # 085 검증기의 사유를 그대로 안고 mm_meta 계약 예외로 바꿔 준다 — 배치는 예외 종류 하나만
        # 알면 되고(``MmMetaPersistError``), 원인 문장은 ``from e`` 로 보존된다.
        raise MmMetaPersistError(
            "타입 어휘 등록 행이 검증을 통과하지 못했다"
            f"(mm_meta_type_vocab · vocab_code={DEFAULT_VOCAB_CODE}): {e}"
        ) from e

    defs = tuple(
        EntityTypeDef(
            code=label.code,
            name=label.name,
            definition=label.definition,
            exclusion=label.exclusion,
        )
        for label in skill.labels
    )
    # 🔴 **"정확히 5종" 규칙을 완화한다**(spec 087 T003 · 2026-08-27).
    #   전에는 등록 어휘가 코드 프리셋과 **같아야** 했다 — 코드 상수가 판정 어휘의 정본이었기
    #   때문이다. 이제 정본은 이 행이고 판정·저장이 이 어휘를 주입받으므로(T001·T002), 어휘를
    #   늘리는 것이 정상 운영이다. 늘리려고 코드를 배포해야 했던 것이 그 규칙의 부작용이었다.
    #
    #   그래도 **완전히 열지는 않는다.** 저장 유니크 키가 ``(entity_type, entity_uid)`` 라 어휘가
    #   비었거나 이름이 겹치면 그 사고가 노드로 굳는다:
    #     · 빈 어휘   → 프롬프트에 타입이 없어 전 자산이 개체 0 이 되고, 원인이 안 보인다.
    #     · 이름 중복 → 같은 이름의 정의문 둘 중 어느 것이 프롬프트에 실릴지가 우연이 된다.
    #   이름 유일성은 085 검증기(``load_skill``)가 라벨명 중복으로 이미 막지만, 여기서 한 번 더
    #   본다 — 이 함수가 판정 경로의 마지막 관문이라 사유를 mm_meta 언어로 말해야 한다.
    names = [d.name for d in defs]
    if not names:
        raise MmMetaPersistError(
            "타입 어휘 등록 행에 타입이 하나도 없다"
            f"(mm_meta_type_vocab · vocab_code={DEFAULT_VOCAB_CODE}) — "
            "빈 어휘로 판정하면 전 자산이 개체 0 이 되고 원인이 드러나지 않는다."
        )
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise MmMetaPersistError(
            "타입 어휘 등록 행에 같은 이름이 두 번 있다"
            f"(mm_meta_type_vocab · vocab_code={DEFAULT_VOCAB_CODE}) — {dup}. "
            "저장 키가 (entity_type, entity_uid) 라 어느 정의문이 실릴지가 우연이 되면 안 된다."
        )
    return defs


# ── 카탈로그 행 ─────────────────────────────────────────────────────────────────
# 오등록 교정문. ``ensure_relation_kind_for_llm_proposal`` 의 ON CONFLICT 는 이름·설명만 갱신하고
# is_symmetric·status 는 건드리지 않는다(LLM 재제안이 사람 승인을 되돌리지 못하게 하는 안전장치).
# 그 덕에 **한 번 잘못 등록되면 스스로 못 고친다** — mm_member 는 사람이 심사하는 종류가 아니라
# 시스템이 소유하는 종류이므로, 어긋난 행만 골라 되돌린다. WHERE 가드가 있어 정상 행에는 쓰기 0이다.
_REPAIR_KIND_SQL = """
UPDATE relation_kind
   SET is_symmetric = FALSE, status = 'active'
 WHERE kind_code = %s
   AND (is_symmetric IS DISTINCT FROM FALSE OR status <> 'active')
"""


def ensure_mm_member_kind(conn: Connection[Any]) -> str:
    """``mm_member`` 관계 종류를 **비대칭·active** 로 보장한다(멱등 · DB 에 쓴다).

    ``ensure_relation_kind_for_llm_proposal`` 을 감싼 전용 함수다(plan 확인점 3 확정). 그 함수를
    그대로 쓰지 않는 이유 두 가지:

        - 기본값이 ``is_symmetric=True``·``status='inactive'`` 다(LLM 제안 전용 시그니처). 소속 엣지는
          **방향이 있고**(자산→개체), inactive 면 엣지가 될 수 없다(``graph_persist`` 는 active kind
          만 받는다).
        - 그 함수의 ON CONFLICT 는 두 값을 갱신하지 않아 **오등록을 스스로 못 고친다** → 어긋난
          행만 골라 되돌리는 교정문을 뒤에 붙인다.

    active 로 두면서도 LLM 관계 프롬프트에 실리지 않는 이유는 ``PROMPT_EXCLUDED_KIND_CODES``
    (``src/relations/schema.py``)가 카탈로그 조회에서 이 코드를 빼기 때문이다(spec 착수 전 결정 ②).

    Args:
        conn: DB 커넥션(호출부 트랜잭션 안에서 돈다 · 커밋은 호출부 몫).

    Returns:
        확정된 ``relation_kind_id``(문자열).
    """
    kind_id = ensure_relation_kind_for_llm_proposal(
        conn,
        kind_code=MM_MEMBER_KIND_CODE,
        kind_name_ko=MM_MEMBER_KIND_NAME_KO,
        description=MM_MEMBER_KIND_DESCRIPTION,
        is_symmetric=False,
        status="active",
    )
    with conn.cursor() as cur:
        cur.execute(_REPAIR_KIND_SQL, (MM_MEMBER_KIND_CODE,))
    return kind_id


# ── 메타 노드 ───────────────────────────────────────────────────────────────────
_SELECT_ENTITY_NODE_SQL = """
SELECT node_id, canonical FROM node
 WHERE node_kind = 'entity' AND entity_type = %s AND entity_uid = %s
 LIMIT 1
"""
# 부분 유니크 인덱스(``uq_node_entity … WHERE node_kind='entity'``)라 ON CONFLICT 대상에 **인덱스
# 술어를 함께** 적어야 PostgreSQL 이 그 인덱스를 고른다(``ensure_asset_node`` 선례와 같은 형태).
_INSERT_ENTITY_NODE_SQL = """
INSERT INTO node (node_id, node_kind, entity_type, entity_uid, canonical)
VALUES (%s, 'entity', %s, %s, %s::jsonb)
ON CONFLICT (entity_type, entity_uid) WHERE node_kind = 'entity' DO NOTHING
RETURNING node_id
"""


def ensure_entity_node(
    conn: Connection[Any],
    entity_type: str,
    name: str,
    *,
    allowed_types: frozenset[str] | None = None,
) -> str:
    """메타(개체) 노드가 있는지 확인하고 **없으면 만들어서** node_id 를 돌려준다.

    **DB 에 쓴다**(노드가 없을 때만 INSERT). 호출자의 트랜잭션 안에서 돈다.

    유니크 키는 ``(entity_type, entity_uid)`` 이고 ``entity_uid`` 는 표기 키
    (``normalize_text_key`` — 083/084 공용 정본)다. 그래서 "제주도"·"제 주 도"·"제주도 "는 한 노드로
    모이고, **같은 표기·다른 타입은 별개 노드**다(동음이의 분리 — '파리'(장소)와 '파리'(인물)).

    ``canonical`` 에는 ``{"name": 대표 표기}`` 만 넣는다. 표기는 **최초 등록 시 1회 고정**이며 이후
    호출은 표기를 덮지 않는다(계약 ① · spec §4). 등록/발굴 구분 표식(``source``)은 수동 선등록
    경로(T013)가 붙인다 — 이 함수가 만든 노드에 ``source`` 가 없다는 것이 곧 "발굴(auto)"이다.

    Args:
        allowed_types: 허용 타입 이름 집합. ``None`` 이면 코드 프리셋(5종). 🔴 **여기가 실질
            게이트다**(spec 087 T003) — 저장 유니크 키가 ``(entity_type, entity_uid)`` 라 어휘 밖
            값이 한 번 들어오면 손으로 지워야 한다. 등록 어휘를 늘렸으면 **그 어휘를 넘겨야** 한다.
        entity_type: 개체 타입. 허용 집합 밖이면 예외 — 어휘 밖 값이 노드가 되면
            묶음 축이 무한히 늘어난다.
        name: 개체 대표 표기(LLM 판정의 표준표기). 정규화 후 빈 값이면 예외.

    Returns:
        확정된 ``node_id``(문자열). 기존 노드면 그 값, 신규면 방금 만든 값.

    Raises:
        MmMetaPersistError: 타입이 어휘 밖이거나 표기가 비었을 때. **쓰기 시도조차 하지 않는다.**
    """
    allowed = allowed_types if allowed_types is not None else ENTITY_TYPES
    if entity_type not in allowed:
        raise MmMetaPersistError(
            f"개체 타입이 허용 어휘 밖이다: {entity_type!r} (허용 {sorted(allowed)})"
        )
    uid = normalize_text_key(name)
    if not uid:
        raise MmMetaPersistError("개체 표기가 비어 있다 — 표기 키를 만들 수 없다")
    display = name.strip()

    with conn.cursor() as cur:
        # READ-then-INSERT: 대부분의 호출은 이미 있는 노드다(같은 개체가 여러 자산에서 나온다).
        cur.execute(_SELECT_ENTITY_NODE_SQL, (entity_type, uid))
        row = cur.fetchone()
        if row is not None:
            return str(row[0])  # 표기 불변 — UPDATE 하지 않는다(계약 ①)
        cur.execute(
            _INSERT_ENTITY_NODE_SQL,
            (uuid7_str(), entity_type, uid,
             json.dumps({"name": display}, ensure_ascii=False)),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return str(inserted[0])
        # RETURNING 이 비었다 = 동시 삽입 경쟁에서 다른 트랜잭션이 먼저 커밋(DO NOTHING).
        cur.execute(_SELECT_ENTITY_NODE_SQL, (entity_type, uid))
        again = cur.fetchone()
        if again is None:  # 정상 경로에선 도달 불가 — 조용히 넘기면 엣지가 소리 없이 사라진다
            raise MmMetaPersistError(
                f"메타 노드 해소 실패(type={entity_type} uid={uid}) — uq_node_entity 확인"
            )
        return str(again[0])


# 등록·발굴된 메타 노드 전량(색인 두 벌의 공통 재료 — 공식 표기 색인·별칭 색인). 한 문장을 공유하는
# 이유: 두 색인이 **같은 행 집합**을 봐야 하고(한쪽만 필터가 생기면 조용히 갈린다), 배치는 이 질의를
# 시작에 한 번씩만 돈다. ``ORDER BY`` 는 결정성 습관이다 — 두 색인 모두 순서에 의존하지 않게
# 만들어 뒀지만(동점 규칙이 값으로 정해진다), 정렬이 있으면 로그·디버깅 재현이 쉽다.
_ENTITY_NODE_ROWS_SQL = """
SELECT entity_type, entity_uid, canonical FROM node
 WHERE node_kind = 'entity'
 ORDER BY entity_type, entity_uid
"""

# 개체별 **묶음 크기**(= 붙어 있는 자산 수 · 2026-08-24 T018 결함 ① 수정). 공식 표기를 "자산이 많이
# 붙은 표기"로 고르려면 이 수가 필요하다.
#   ⚠️ ``count(DISTINCT ge.src_node)`` 인 이유: 세는 것은 **자산 수**다. ``uq_graph_edge_kind`` 가
#      (src,dst,kind) 중복을 막으므로 실제로는 ``count(*)`` 와 같은 값이지만, 손 SQL·데이터 사고로
#      중복 엣지가 생겼을 때 크기가 부풀어 병합 방향이 뒤집히는 것은 막아야 한다.
#   ⚠️ 노드 행 조회(``_ENTITY_NODE_ROWS_SQL``)와 **따로** 도는 이유: 그 문장은 별칭 색인
#      (``fetch_registered_alias_index``)과 공유하는 재료다. 여기에 JOIN·GROUP BY 를 붙이면 크기를
#      쓰지 않는 쪽 질의까지 무거워진다. 두 질의 모두 **배치 시작 1회**라 왕복 한 번이 더 드는 비용은
#      무의미하다(자산 루프 안에서는 한 번도 돌지 않는다).
#   ⚠️ 소속이 0인 개체는 GROUP BY 결과에 **아예 나오지 않는다** — 읽는 쪽이 "없으면 0"으로 본다
#      (수동 선등록만 된 빈 메타도 후보로 남긴다는 뜻이다).
#   ⚠️ 바인딩 순서: (statuses, kind_code).
_ENTITY_BUNDLE_SIZE_SQL = """
SELECT en.entity_type, en.entity_uid, count(DISTINCT ge.src_node) AS bundle_size
FROM node en
JOIN graph_edge ge ON ge.dst_node = en.node_id AND ge.status = ANY(%s)
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id AND rk.kind_code = %s
JOIN node an ON an.node_id = ge.src_node AND an.node_kind = 'asset'
WHERE en.node_kind = 'entity'
GROUP BY en.entity_type, en.entity_uid
ORDER BY en.entity_type, en.entity_uid
"""


def fetch_official_name_index(conn: Connection[Any]) -> dict[tuple[str, str], str]:
    """이미 등록된 메타로 **공식 표기 색인**을 만든다(조회 전용·결정적 · 구현 확정 1·2).

    무엇을 위한 것인가: 접미 병합('서울' → '서울특별시')은 후보가 한자리에 모여야 동작하는데,
    증분 배치에서는 공식형이 지난 배치의 노드로만 남아 있고 이번 판정에는 짧은 표기만 온다. 실측이
    그랬다 — 서울 2건 / 서울특별시 9건이 **다른 자산**에서 나와 자산 안에서는 절대 만나지 않았다.
    그래서 등록된 표기를 색인으로 만들어 ``apply_rules(official_index=...)`` 에 넘긴다.

    🔴 **배치 시작에 한 번만 부른다.** 자산마다 부르면 자산 수 × 개체 수만큼 질의·조립이 늘고
    (자산 10만 × 개체 10만 = 100억) 그 비용이 그대로 배치 시간이 된다 — 별칭 색인
    (``fetch_registered_alias_index``)과 같은 규율이다.

    🔴 **타입 스코프**(2026-08-24 개정). 옛 ``fetch_known_entity_names`` 는 "표기 키 → 대표 표기"라
    타입을 잃었고, 그래서 ``[사건] 경주``(경마)가 등록된 ``[장소] 경주시`` 로 잘못 합쳐질 수 있었다
    (실데이터 발생 0건의 잠재 결함). 색인 키에 타입이 들어가면 그 계열의 실수가 불가능해지고, 같은
    표기가 타입만 달리 존재할 때 "어느 행이 이기나"를 정할 필요도 없어진다(키가 갈리므로).

    🔴 **묶음 크기를 함께 읽는다**(2026-08-24 T018 결함 ① 수정). 공식형을 "접미가 긴 표기"로 고르면
    실제 묶음을 배신했다 — dev 실측에서 ``(장소,'제주')`` 의 공식형이 자산이 거의 없는
    '제주특별자치도' 로 정해졌고 알찬 묶음은 '제주도' 였다(강원도도 같은 상태). 그대로 두면 앞으로
    '제주' 판정이 빈 쪽으로 흘러가 묶음이 둘로 갈라진다. 그래서
    개체별 자산 수를 함께 조회해 순수 빌더에 넘기고, 빌더가 **큰 쪽**을 고른다(§구현 확정 2 의
    "이미 자산이 붙은 곳으로 합류"가 그 근거다). 크기를 세는 상태는 카드 조회와 같은
    ``MM_META_VISIBLE_STATUSES`` 이며, 소속 0인 노드(수동 선등록만 된 빈 메타)도 크기 0 후보로 남는다.

    별칭(``canonical.aliases`` · T013)도 **같은 타입 스코프로** 후보에 넣는다. 등록 표기가 짧은 쪽
    ('서울')이고 공식형이 별칭('서울특별시')인 경우까지 합류시키려는 것이다. 별칭 후보의 크기는 **그
    노드의 묶음 크기**다 — 어느 표기로 합류하든 붙게 되는 자산 묶음은 그 노드 하나이기 때문이다.
    그렇게 표기가 공식형으로 갈아탄 뒤에는 ``resolve_registered_aliases`` 가 **등록 표기로 되돌린다**
    (호출 순서 4의 이유) — 두 단계가 이 순서라야 등록 메타의 묶음이 갈라지지 않는다.

    Args:
        conn: DB 커넥션.

    Returns:
        ``{(entity_type, 몸통 표기 키): 공식 표기}`` — ``build_official_name_index`` 의 산출물
        그대로다(이 함수가 그 순수 함수에 위임한다. 두 곳에서 따로 조립하면 규칙이 갈린다).
        후보가 없으면 빈 dict(접미가 붙은 **장소** 표기가 없을 때 · 몸통 2글자 미만도 빠진다).
        ``canonical.name`` 이 없는 행(손 SQL·구버전)은 ``entity_uid`` 를 표기로 쓴다(빈 라벨을
        내보내지 않는다).
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ENTITY_NODE_ROWS_SQL)
        rows = cur.fetchall()
        cur.execute(_ENTITY_BUNDLE_SIZE_SQL,
                    (list(MM_META_VISIBLE_STATUSES), MM_MEMBER_KIND_CODE))
        # 없으면 0 — 소속 0인 개체는 GROUP BY 결과에 나오지 않고, ``mm_member`` 카탈로그 행이 아직
        # 없을 때도(첫 배치 이전) 전부 0 이 된다. 그때는 크기 축이 전부 동점이라 옛 규칙으로 떨어진다.
        sizes = {
            (str(row["entity_type"]), str(row["entity_uid"])): int(row["bundle_size"])
            for row in cur.fetchall()
        }

    candidates: list[tuple[str, str] | tuple[str, str, int]] = []
    for row in rows:
        canonical = row["canonical"] if isinstance(row["canonical"], dict) else {}
        entity_type = str(row["entity_type"])
        display = str(canonical.get("name") or row["entity_uid"] or "").strip()
        if not display:
            continue
        size = sizes.get((entity_type, str(row["entity_uid"])), 0)
        # 대표 표기 자신 + 별칭 전부가 후보다(전역 별칭 사전은 폐기 유지 — 별칭은 등록 메타에만 있다).
        # 별칭도 **그 노드의 크기**로 겨룬다(합류하면 붙는 묶음이 같으므로).
        for candidate in [display, *_alias_list(canonical)]:
            if normalize_text_key(candidate):
                candidates.append((entity_type, candidate, size))
    # 판정 규칙(묶음 큰 쪽 → 긴 접미 → 표기 키 사전순 · 장소만 · 몸통 2글자 이상)은 순수 함수 한
    # 곳에만 둔다.
    return dict(build_official_name_index(candidates))


# ── 수동 선등록(spec §6-1) ──────────────────────────────────────────────────────
# ``canonical`` 만 갱신한다. ``node`` 에는 ``updated_at`` 컬럼이 없으므로(200_graph_unified.sql)
# 그 값을 함께 쓰려 들면 실 DB 에서 터진다 — 모의 테스트로는 못 잡히는 종류의 실수라 여기 적어 둔다.
_UPDATE_ENTITY_CANONICAL_SQL = """
UPDATE node SET canonical = %s::jsonb
 WHERE node_id = %s
"""

# 등록 목록 조회 — 표시에 필요한 것만 꺼낸다(``canonical`` 전문은 별칭을 읽으려고 함께 받는다).
#   ``%s::text IS NULL OR …`` 형태로 필터를 **하나의 질의**에 담는다: SQL 문을 분기로 나누면
#   조건 문자열 조립이 늘어나고(오타 위험) 실행 계획도 갈린다.
_LIST_MM_META_SQL = """
SELECT entity_type, entity_uid, COALESCE(NULLIF(canonical->>'name', ''), entity_uid) AS name,
       COALESCE(canonical->>'source', 'auto') AS source, canonical
FROM node
WHERE node_kind = 'entity'
  AND (%s::text IS NULL OR COALESCE(canonical->>'source', 'auto') = %s)
ORDER BY entity_type, entity_uid
LIMIT %s
"""

# 등록 목록의 기본 상한 — 등록 메타는 사람이 손으로 넣는 것이라 규모가 작다(수십~수백). 상한을
# 두는 이유는 발굴(auto) 메타까지 함께 볼 때 콘솔이 수천 줄로 흐르는 것을 막는 것이다.
LIST_MM_META_DEFAULT_LIMIT = 200


def normalize_registration(
    entity_type: str,
    name: str,
    aliases: Sequence[str] = (),
    *,
    allowed_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    """등록 요청을 검사·정규화한다(순수 · DB 접속 없음).

    등록 CLI 의 dry-run 이 **DB 없이** 무엇이 등록될지 보여 줄 수 있어야 해서 검사와 쓰기를 갈라
    두었다. 별칭 정리 규칙 셋:

        - 앞뒤 공백을 자르고 빈 값은 버린다(콘솔 입력에서 흔한 잡음).
        - **표기 키가 같은 별칭은 하나만** 남긴다("이지은"·"이 지 은"은 같은 별칭이다).
        - 대표 표기와 표기 키가 같은 별칭은 버린다 — 이미 ``entity_uid`` 로 잡히므로 저장해도
          매칭에 아무 것도 더하지 않는다.

    Args:
        entity_type: 개체 타입. 허용 집합 밖이면 예외.
        allowed_types: 허용 타입 이름 집합. ``None`` 이면 코드 프리셋(5종 · spec 087 T003).
        name: 대표 표기(사용자가 정한 이름). 정규화 후 빈 값이면 예외.
        aliases: 별칭 목록. **등록 메타 한정**이며 전역 별칭 사전이 아니다(spec 비범위 유지) —
            "이 메타에 한해 이 표기도 같은 것으로 본다"는 선언이다.

    Returns:
        ``{entity_type, entity_uid, name, aliases}``. ``name`` 은 strip 만 한 표시용 표기이고
        ``entity_uid``·별칭 대조는 표기 키(``normalize_text_key``)로 한다. ``aliases`` 는 입력
        순서를 지키는 튜플(사용자가 적은 순서가 곧 표시 순서다).

    Raises:
        MmMetaPersistError: 타입이 어휘 밖이거나 대표 표기가 비었을 때.
    """
    allowed = allowed_types if allowed_types is not None else ENTITY_TYPES
    if entity_type not in allowed:
        raise MmMetaPersistError(
            f"개체 타입이 허용 어휘 밖이다: {entity_type!r} (허용 {sorted(allowed)})"
        )
    uid = normalize_text_key(name)
    if not uid:
        raise MmMetaPersistError("개체 표기가 비어 있다 — 표기 키를 만들 수 없다")

    cleaned: list[str] = []
    seen_keys = {uid}
    for raw in aliases or ():
        alias = str(raw or "").strip()
        key = normalize_text_key(alias)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        cleaned.append(alias)
    return {"entity_type": entity_type, "entity_uid": uid, "name": name.strip(),
            "aliases": tuple(cleaned)}


def register_mm_meta(
    conn: Connection[Any], entity_type: str, name: str, *, aliases: Sequence[str] = ()
) -> dict[str, Any]:
    """멀티모달 메타를 **사람이 직접 등록**한다(멱등 · DB 에 쓴다 · spec §6-1).

    무엇을 위한 경로인가: 발굴 모드 기본값이 ``propose`` 라 배치는 **등록된 메타에만** 자산을
    붙인다(spec §1-1). 즉 이 함수가 후보 승인의 유일한 수단이고, 등록해 두면 다음 배치가 알아서
    채운다. 소속 0건인 **빈 메타도 허용**한다 — 먼저 정의하고 데이터가 들어오면 붙는 순서다.

    기존 노드와 만났을 때의 규칙(계약 ①의 연장):

        - **대표 표기는 바꾸지 않는다.** 배치가 먼저 만든 노드(auto·표기 '아이유')에 사람이 'IU'
          로 등록하면, 표기는 '아이유'로 남고 'IU' 는 **별칭으로** 들어간다. 표기를 덮으면 이미
          그 이름으로 노출된 묶음의 간판이 배치 순서에 따라 흔들린다(spec §4). 요청 표기가 저장
          표기와 다르면 반환값의 ``name_kept`` 로 알려 준다 — 조용히 무시하면 "등록했는데 이름이
          안 바뀐다"를 다시 조사하게 된다.
        - ``source`` 는 ``user`` 로 **승격**한다(auto → user 는 되지만 반대는 이 함수가 하지 않는다).
        - 별칭은 **병합**이다(기존 것을 지우지 않는다). 지우려면 사람이 DB 에서 지운다 — 등록 메타의
          name·aliases 는 사용자 소유이므로 코드가 임의로 줄이지 않는다.
        - 바뀔 것이 없으면 **아무 것도 쓰지 않는다**(``unchanged``).

    Args:
        entity_type: 개체 타입(닫힌 5종). 같은 표기·다른 타입은 별개 메타다(동음이의 분리).
        name: 대표 표기. 신규 등록이면 이 값이 간판이 되고, 기존 노드가 있으면 별칭 후보가 된다.
        aliases: 이 메타에 한해 같은 것으로 볼 표기들(전역 사전 아님). 판정이 이 표기로 나오면
            ``resolve_registered_aliases`` 가 대표 표기로 이어 준다.

    Returns:
        ``{action, node_id, entity_type, entity_uid, name, requested_name, aliases, added_aliases,
        source, name_kept}``. ``action`` 은 ``registered``(신규)·``updated``(기존 갱신)·
        ``unchanged``(쓰기 0), ``added_aliases`` 는 이번에 새로 붙은 별칭이다.

    Raises:
        MmMetaPersistError: 타입이 어휘 밖이거나 표기가 빌 때. **쓰기 시도조차 하지 않는다.**
    """
    plan = normalize_registration(entity_type, name, aliases)
    uid = plan["entity_uid"]
    requested = plan["name"]

    with conn.cursor() as cur:
        cur.execute(_SELECT_ENTITY_NODE_SQL, (entity_type, uid))
        row = cur.fetchone()
        if row is None:
            canonical = {"name": requested, "source": MM_META_SOURCE_USER,
                         "aliases": list(plan["aliases"])}
            cur.execute(
                _INSERT_ENTITY_NODE_SQL,
                (uuid7_str(), entity_type, uid,
                 json.dumps(canonical, ensure_ascii=False)),
            )
            inserted = cur.fetchone()
            if inserted is not None:
                return {"action": "registered", "node_id": str(inserted[0]),
                        "entity_type": entity_type, "entity_uid": uid, "name": requested,
                        "requested_name": requested, "aliases": list(plan["aliases"]),
                        "added_aliases": list(plan["aliases"]),
                        "source": MM_META_SOURCE_USER, "name_kept": False}
            # ON CONFLICT DO NOTHING 이 빈 RETURNING 을 준 경합 경로 — 재조회 후 갱신 경로로 간다.
            cur.execute(_SELECT_ENTITY_NODE_SQL, (entity_type, uid))
            row = cur.fetchone()
            if row is None:  # 정상 경로에선 도달 불가
                raise MmMetaPersistError(
                    f"메타 노드 해소 실패(type={entity_type} uid={uid}) — uq_node_entity 확인"
                )

    node_id = str(row[0])
    existing = row[1] if isinstance(row[1], dict) else {}
    kept_name = str(existing.get("name") or "").strip() or requested

    # 별칭 후보를 **순서대로** 모은다: 기존 별칭 → 요청 표기 → 요청 별칭.
    #   ① 기존 것이 앞 = 사람이 추가해 온 순서가 화면에 그대로 보인다(병합은 덧붙이기다).
    #   ② 요청 표기가 저장 표기와 다르면 그 표기도 별칭이 된다 — 사용자가 'IU' 로 등록을 시도했으면
    #      적어도 그 표기로 매칭은 되게 해 주는 것이 요청에 가깝다(대표 표기만 안 바꾼다).
    #   ③ 중복은 **표기 키**로 접는다(대표 표기와 같은 것도 버린다 — 이미 entity_uid 로 잡힌다).
    previous = _alias_list(existing)
    candidates = list(previous)
    if normalize_text_key(requested) != normalize_text_key(kept_name):
        candidates.append(requested)
    candidates.extend(plan["aliases"])

    merged: list[str] = []
    keys = {normalize_text_key(kept_name)}
    for candidate in candidates:
        key = normalize_text_key(candidate)
        if not key or key in keys:
            continue
        keys.add(key)
        merged.append(candidate)
    added = [alias for alias in merged if alias not in previous]

    canonical = {**existing, "name": kept_name, "source": MM_META_SOURCE_USER,
                 "aliases": merged}
    if canonical == existing:
        # 같은 내용을 다시 쓰면 얻는 것 없이 행만 흔든다(멱등 — 085 영속의 "변경 없음" 관례).
        return {"action": "unchanged", "node_id": node_id, "entity_type": entity_type,
                "entity_uid": uid, "name": kept_name, "requested_name": requested,
                "aliases": merged, "added_aliases": [], "source": MM_META_SOURCE_USER,
                "name_kept": kept_name != requested}

    with conn.cursor() as cur:
        cur.execute(_UPDATE_ENTITY_CANONICAL_SQL,
                    (json.dumps(canonical, ensure_ascii=False), node_id))
    return {"action": "updated", "node_id": node_id, "entity_type": entity_type,
            "entity_uid": uid, "name": kept_name, "requested_name": requested,
            "aliases": merged, "added_aliases": added, "source": MM_META_SOURCE_USER,
            "name_kept": kept_name != requested}


def _alias_list(canonical: Mapping[str, Any]) -> list[str]:
    """``canonical.aliases`` 를 문자열 목록으로 꺼낸다(형이 어긋나면 빈 목록).

    Args:
        canonical: 노드의 ``canonical`` jsonb 값(무엇이든 올 수 있다고 가정한다 — 손 SQL·구버전).

    Returns:
        별칭 문자열 목록. 키가 없거나 리스트가 아니면 빈 목록.
    """
    aliases = canonical.get("aliases")
    if not isinstance(aliases, list):
        return []
    return [str(a).strip() for a in aliases if str(a or "").strip()]


def fetch_registered_alias_index(conn: Connection[Any]) -> dict[tuple[str, str], str]:
    """등록 메타의 **(타입, 표기 키) → 대표 표기** 색인(조회 전용·결정적).

    별칭 매칭을 **배치 한 번의 조회**로 끝내려고 만든 색인이다. 판정 건마다 DB 를 찾으면 자산
    수 × 개체 수만큼 질의가 늘고, 대량 적재에서 그 비용이 그대로 배치 시간이 된다. 등록 메타는
    사람이 손으로 넣는 규모(수십~수백)라 통째로 들고 있어도 가볍다.

    ``fetch_official_name_index`` 와 다른 점: 저쪽은 **접미 계열의 공식형**을 찾는 색인
    ((타입, 몸통 표기 키) → 공식 표기)이고, 이쪽은 **표기 그 자체**를 등록 표기로 잇는다
    ((타입, 표기 키) → 대표 표기). 둘 다 타입 스코프다 — 별칭은 표기가 겹치기 쉬워(가수 별명 vs
    도시명) 타입을 안 보면 동음이의 분리(spec §4)가 무너진다.

    대표 표기 자신도 색인에 넣는다: 판정이 '제 주 도' 처럼 흔들린 표기로 와도 등록 표기로 맞춰
    주는 것이 같은 규칙의 자연스러운 확장이다(노드는 어차피 표기 키로 하나로 모인다).

    Args:
        conn: DB 커넥션.

    Returns:
        ``{(entity_type, 표기 키): 대표 표기}``. 메타가 없으면 빈 dict. 같은 (타입, 키)가 여러 행에
        걸리면 **entity_type·entity_uid 오름차순 첫 행**이 이긴다(SQL 정렬 + ``setdefault`` — 순서가
        흔들리면 치환 결과가 흔들린다).
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ENTITY_NODE_ROWS_SQL)
        rows = cur.fetchall()

    index: dict[tuple[str, str], str] = {}
    for row in rows:
        canonical = row["canonical"] if isinstance(row["canonical"], dict) else {}
        entity_type = str(row["entity_type"])
        display = str(canonical.get("name") or row["entity_uid"] or "").strip()
        if not display:
            continue
        for candidate in [display, *_alias_list(canonical)]:
            key = normalize_text_key(candidate)
            if key:
                index.setdefault((entity_type, key), display)
    return index


def resolve_registered_aliases(
    entities: Sequence[ExtractedEntity],
    alias_index: Mapping[tuple[str, str], str],
) -> tuple[ExtractedEntity, ...]:
    """판정 표기를 **등록 메타의 대표 표기로 갈아** 준다(순수 · DB 호출 없음).

    이것이 spec §6-1 의 "매칭 합류"다 — 판정이 별칭으로 나와도 등록 메타에 붙는다("이지은"으로
    판정된 자산이 "아이유" 묶음에 들어간다). 치환은 표기만 바꾸고 ``keyword``(근거)는 원문을
    보존한다 — ``reason`` 의 ``kw=`` 는 "LLM 이 무엇을 보고 판정했나"의 기록이다.

    **호출 순서**: ``judge`` → ``apply_rules``(제외·스톱·접미 병합) → **이 함수** → ``upsert_entity_edges``.
    접미 병합 뒤에 두는 이유: 병합이 표기를 공식형으로 바꿀 수 있고, 등록 메타 별칭은 그 결과에도
    적용돼야 한다(등록이 규칙보다 나중 말이다).

    같은 메타로 접힌 판정은 **첫 것만 남긴다**(``apply_rules`` 의 중복 접기와 같은 규칙) — 한 자산이
    '아이유'와 '이지은'을 함께 말하면 개체는 하나이고 엣지도 하나여야 한다(유니크 (src,dst,kind)).

    Args:
        entities: 규칙까지 통과한 판정 목록(보통 자산 하나 분량). 입력 순서를 보존한다.
        alias_index: ``fetch_registered_alias_index`` 결과. **빈 dict 면 아무 것도 치환하지 않는다**
            (등록 메타가 없는 환경 = 배치 기본 동작 그대로).

    Returns:
        치환·중복 접기를 마친 판정 튜플. 입력 목록은 변형하지 않는다(전부 새 객체·불변).
    """
    resolved: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()
    for entity in entities:
        name = alias_index.get((entity.entity_type, entity.uid), entity.name)
        item = entity if name == entity.name else replace(entity, name=name)
        key = (item.entity_type, item.uid)
        if key in seen:
            continue  # 같은 메타를 가리키는 다른 표기 — 첫 판정만 남긴다(대표 키워드 고정)
        seen.add(key)
        resolved.append(item)
    return tuple(resolved)


def list_mm_meta(
    conn: Connection[Any],
    *,
    source: str | None = None,
    limit: int = LIST_MM_META_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """등록·발굴된 메타 목록을 조회한다(읽기 전용 — 등록 CLI 의 "지금 무엇이 있나").

    소속 건수는 주지 않는다 — 그것은 묶음 조회(``src.relations.graph_query.mm_meta_bundle``)의
    일이고, 여기서 함께 세면 같은 숫자를 두 곳에서 계산하게 된다(어긋나면 어느 쪽을 믿을지 모른다).

    Args:
        source: ``user``(수동 선등록)·``auto``(배치 발굴) 필터. ``None``(기본)이면 **둘 다**.
        limit: 최대 행수. 등록 메타는 규모가 작지만 발굴분까지 함께 볼 때 콘솔이 흐르지 않게 둔다.

    Returns:
        ``[{entity_type, entity_uid, name, source, aliases}]`` — ``(entity_type, entity_uid)``
        오름차순(결정적). 없으면 빈 리스트.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_MM_META_SQL, (source, source, int(limit)))
        rows = cur.fetchall()
    return [
        {
            "entity_type": str(r["entity_type"]),
            "entity_uid": str(r["entity_uid"]),
            "name": str(r["name"] or r["entity_uid"]),
            "source": str(r["source"] or MM_META_SOURCE_AUTO),
            "aliases": _alias_list(r["canonical"] if isinstance(r["canonical"], dict) else {}),
        }
        for r in rows
    ]


# ── reason 스탬프 ───────────────────────────────────────────────────────────────
def format_member_reason(*, keyword: str, prompt_version: str, rule_version: int) -> str:
    """소속 엣지의 ``reason`` 스탬프를 만든다(고정 형식 · 순수).

    형식은 ``kw=<원문 키워드>|pv=<프롬프트 판>|rv=<규칙 판>`` 이다. 세 값을 함께 남기는 이유:
    키워드는 **근거**(왜 이 파일이 이 묶음에 있나), 두 판은 **재선별 기준**(무슨 규칙·문안이 만든
    판정인가)이다. 저장된 사실만으로 되짚을 수 있어야 한다(헌법 3조 · spec §4).

    Args:
        keyword: 판정의 출처 키워드(원문 그대로 — 정규화하지 않는다. 사람이 읽는 근거다).
        prompt_version: 프롬프트 문안 판(``judge.PROMPT_VERSION`` · 문자열 지문).
        rule_version: 규칙 판(``rules.RULE_VERSION`` · 정수. 순서 비교를 하므로 정수다).

    Returns:
        스탬프 문자열. ``parse_member_reason`` 으로 왕복한다.
    """
    return (f"{_STAMP_KEYS[0]}={keyword}{_STAMP_SEP}"
            f"{_STAMP_KEYS[1]}={prompt_version}{_STAMP_SEP}"
            f"{_STAMP_KEYS[2]}={rule_version}")


def parse_member_reason(reason: str | None) -> dict[str, Any] | None:
    """소속 엣지의 ``reason`` 스탬프를 되돌려 읽는다(순수).

    **오른쪽에서** 두 칸만 떼어 읽는다 — 키워드에 구분자(``|``·``=``)가 섞여도 원문이 보존되게 하려는
    것이다(키워드는 요약기가 만든 자유 문자열이다).

    Args:
        reason: 엣지의 ``reason`` 값. ``None``·빈 값도 받는다.

    Returns:
        ``{"keyword": str, "prompt_version": str, "rule_version": int}``. 형식이 아니거나 값이 비면
        ``None`` — 호출부(배치 재선별)는 ``None`` 을 "판 미상"으로 보고 **재대상**으로 둔다(구 형식
        엣지를 최신으로 오인해 백필에서 빠뜨리지 않기 위해서다).
    """
    if not reason:
        return None
    parts = str(reason).rsplit(_STAMP_SEP, 2)
    if len(parts) != 3:
        return None
    values: list[str] = []
    for expected_key, part in zip(_STAMP_KEYS, parts, strict=True):
        key, sep, value = part.partition("=")
        if sep != "=" or key.strip() != expected_key or not value:
            return None
        values.append(value)
    try:
        rule_version = int(values[2])
    except ValueError:
        return None  # rv 가 정수가 아니면 순서 비교를 할 수 없다 → 판 미상 취급
    return {"keyword": values[0], "prompt_version": values[1], "rule_version": rule_version}


# ── 소속 엣지 ───────────────────────────────────────────────────────────────────
# 자산 스코프 교체의 DELETE. 두 조건으로 좁힌다 — **그 종류**(mm_member)와 **그 자산**.
#   종류를 빼면 자산↔자산 관계 엣지까지 지워지고, 자산을 빼면 남의 소속이 사라진다.
#   자산 노드를 하위 질의로 푸는 이유: 노드가 아직 없을 수도 있고(첫 판정·개체 0), 없을 때
#   노드를 먼저 만들면 엣지 0 인 고아 노드가 남는다(graph_persist 가 지키는 순서와 같은 이유).
#   RETURNING 으로 지운 엣지를 받아 개수를 센다 — 배치 diff 리포트가 "삭제 엣지"를 보고한다.
_DELETE_MEMBER_EDGES_SQL = """
DELETE FROM graph_edge
 WHERE relation_kind_id = %s
   AND src_node IN (SELECT node_id FROM node WHERE node_kind = 'asset' AND asset_id = %s)
RETURNING edge_id
"""
# 앞의 DELETE 가 자리를 비웠으므로 **순수 INSERT** 다(ON CONFLICT 없음 — 085 영속 관례와 같은 결).
# confidence 는 **NULL 을 명시**한다: 0.0 을 넣으면 "가장 약한 관계"로 읽히고 GREATEST 화해·신뢰도
# 정렬에 끼어든다. 소속은 확신의 정도를 매기는 값이 아니다(spec §4 — 미산정).
_INSERT_MEMBER_EDGE_SQL = """
INSERT INTO graph_edge (edge_id, src_node, dst_node, relation_kind_id, confidence, reason, status)
VALUES (%s, %s, %s, %s, NULL, %s, %s)
"""


def upsert_entity_edges(
    conn: Connection[Any],
    asset_id: str,
    entities: Sequence[ExtractedEntity],
    *,
    prompt_version: str = PROMPT_VERSION,
    rule_version: int = RULE_VERSION,
    agent: str = LINEAGE_AGENT,
    allowed_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    """자산 하나의 소속 엣지를 **전체 교체**하고 판정 이력을 남긴다 — DB 에 쓴다(한 트랜잭션).

    하는 일 순서: 중복 검사 → 카탈로그 확인 → (트랜잭션) 그 자산의 기존 ``mm_member`` 엣지 삭제 →
    메타 노드 보장 → 엣지 삽입(``proposed``·``confidence`` NULL·스탬프) → 계보 ``entity.judged.v1``.

    🔴 **성공 판정만 이 함수를 부른다.** ``entities`` 가 비면 "성공·개체 0"으로 취급해 **기존 엣지를
    지우고 이력을 남긴다**(그래야 다음 배치가 이 자산을 또 판정하지 않는다). 판정 실패(``ok=False``)를
    이 함수로 넘기면 그 실패가 "판정 완료"로 굳어 재시도가 영구히 멈춘다 — 실패는 호출부가 여기
    오기 전에 걸러야 한다(spec §2 · 구현 확정 4).

    ``conn.transaction()`` 으로 감싸는 이유: 엣지만 지워지고 삽입·계보가 실패하면 그 자산은 소속을
    잃고도 "판정 완료"로 남는다. 전부 아니면 전무여야 한다. 호출부가 이미 트랜잭션 안이면 psycopg3
    가 SAVEPOINT 로 중첩 처리한다.

    Args:
        asset_id: 판정 대상 자산(UUID 문자열).
        entities: 규칙(``apply_rules``)까지 통과한 판정 목록. **입력 순서대로** 저장한다(대표 키워드가
            그 순서로 정해진다 · 결정성). 같은 ``(타입, 표기 키)`` 가 두 번 오면 예외 —
            ``uq_graph_edge_kind`` 위반이 될 요청이라 쓰기 전에 막는다.
        prompt_version: 스탬프의 ``pv``. 기본값은 현행 문안 판이며, 과거 문안 판정을 이관할 때만 명시한다.
        rule_version: 스탬프의 ``rv``. 기본값은 현행 규칙 판.
        agent: 계보의 수행 주체. 기본 ``mm_meta_binding``(배치). 다른 실행 경로(측정 하니스 등)가
            남길 때 구분하려면 명시한다.
        allowed_types: 허용 타입 이름 집합 — 그대로 ``ensure_entity_node`` 로 넘긴다.
            ``None`` 이면 코드 프리셋(5종)이다. 🔴 **배치는 반드시 등록 어휘를 넘겨야 한다**
            (spec 087 T003·T011): 넘기지 않으면 어휘를 늘려도 쓰기 게이트가 5종으로 막아
            `음식` 개체가 저장 직전에 예외로 떨어진다(2026-08-27 실측으로 확인한 그 결함).

    Returns:
        ``{asset_id, edges_deleted, edges_inserted, entities, prompt_version, rule_version}``.
        ``entities`` 는 ``{entity_type, entity_uid, name, keyword, node_id}`` 목록으로,
        ``(타입, 표기 키)`` 오름차순이다(배치 diff 리포트가 쓰는 순서 — 결정적).

    Raises:
        MmMetaPersistError: 같은 개체가 중복이거나, ``mm_member`` 카탈로그 행이 없거나 비활성일 때.
            둘 다 **쓰기 전에** 막는다.
    """
    judged = list(entities)
    seen: set[tuple[str, str]] = set()
    for entity in judged:
        key = (entity.entity_type, entity.uid)
        if key in seen:
            raise MmMetaPersistError(
                f"같은 개체가 두 번 왔다: type={key[0]} uid={key[1]} — "
                "uq_graph_edge_kind 위반이 된다(apply_rules 가 접어 넘겨야 한다)"
            )
        seen.add(key)

    kind = fetch_relation_kind(conn, kind_code=MM_MEMBER_KIND_CODE, status="active")
    if kind is None:
        raise MmMetaPersistError(
            f"관계 종류 {MM_MEMBER_KIND_CODE!r} 가 없거나 비활성이다 — "
            "배치 시작에 ensure_mm_member_kind 를 부른다(조용히 0건을 쓰지 않는다)"
        )
    kind_id = str(kind["relation_kind_id"])

    records: list[dict[str, Any]] = []
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(_DELETE_MEMBER_EDGES_SQL, (kind_id, asset_id))
            deleted = len(cur.fetchall())

        if judged:
            src_node = ensure_asset_node(conn, asset_id)
            for entity in judged:
                dst_node = ensure_entity_node(
                    conn, entity.entity_type, entity.name, allowed_types=allowed_types
                )
                reason = format_member_reason(
                    keyword=entity.keyword,
                    prompt_version=prompt_version,
                    rule_version=rule_version,
                )
                with conn.cursor() as cur:
                    cur.execute(
                        _INSERT_MEMBER_EDGE_SQL,
                        (uuid7_str(), src_node, dst_node, kind_id, reason,
                         GraphEdgeStatus.PROPOSED.value),
                    )
                records.append({"entity_type": entity.entity_type, "entity_uid": entity.uid,
                                "name": entity.name, "keyword": entity.keyword,
                                "node_id": dst_node})

        # 결정적 정렬(관계 계보 관례) — 같은 입력이면 계보 내용도 같아야 비교·재현이 된다.
        records.sort(key=lambda r: (r["entity_type"], r["entity_uid"]))
        # 계보도 **같은 트랜잭션**이다: 엣지는 바뀌었는데 이력이 없거나 그 반대인 반쪽 상태를 만들지
        # 않는다(반쪽이면 재선별이 잘못 판단한다).
        record_lineage(
            conn,
            uuid.UUID(str(asset_id)),
            activity=LINEAGE_ACTIVITY,
            agent=agent,
            generated={"edges_inserted": len(records), "edges_deleted": deleted,
                       "entities": records},
            payload={"prompt_version": prompt_version, "rule_version": rule_version},
        )

    return {"asset_id": str(asset_id), "edges_deleted": deleted,
            "edges_inserted": len(records), "entities": records,
            "prompt_version": prompt_version, "rule_version": rule_version}


# ── 메타 설명(spec §8-1) ────────────────────────────────────────────────────────
# 상태 집합(``MM_META_VISIBLE_STATUSES``)은 파일 앞머리 공용 상수다 — 색인의 묶음 크기와 설명 대상이
# **같은 상태**를 세야 하므로 한 곳에만 둔다.
#
# 설명을 다시 만들어야 하는 사유 — **닫힌 어휘**(배치 리포트가 사유별로 셀 수 있게).
#   missing        : 설명이 아예 없다(첫 생성).
#   member_count   : 구성이 바뀌었다(자산이 붙거나 빠지면 설명이 낡는다 · spec §8-1).
#   prompt_version : 문안이 개정됐다(``describe.DESC_PROMPT_VERSION`` 상승).
# 판정 순서도 이 순서다 — 먼저 걸린 사유를 돌려준다(설명이 없는데 "문안 개정"이라 적으면 리포트가
# 원인을 잘못 가리킨다).
DESC_TARGET_REASONS = ("missing", "member_count", "prompt_version")

# 구성 변화로 설명을 다시 만드는 **비율 임계**(2026-08-24 결함 ③ 수정).
#   왜 필요한가: 낡음 판정이 "저장 건수 ≠ 현재 묶음 크기"였다. 그러면 인기 개체(제주도 13→14→15…)는
#   자산 1건이 붙을 때마다 설명이 재생성된다 — 대량 적재에서 같은 메타에 LLM 을 수십 번 부르고,
#   결과 문장은 거의 같다. 비유하면 시리즈 책 소개문이다: 한 권 더 나올 때마다 소개문을 다시 쓰지
#   않고, 구성이 눈에 띄게 달라졌을 때 고친다.
#   분모는 **그 설명을 만든 시점의 건수**다(``desc_member_count``). 그래서 조금씩 늘어나는 동안은
#   미뤄지고, 마지막 생성 이후 누적 변화가 임계를 넘는 순간 한 번 다시 만든다(등비 간격 —
#   2→1000 으로 자라도 재생성은 수십 번이지 998번이 아니다).
#   0.2 를 고른 이유: 판정 예시로 감을 잡을 수 있는 크기다 — 2→3(50%·재생성) · 13→14(7.7%·스킵) ·
#   13→17(31%·재생성). 20% 는 "묶음 성격이 달라졌다"고 말할 수 있는 최소선이고, 더 낮추면(10%)
#   인기 개체가 다시 매번 걸리고 더 높이면(50%) 카드 설명이 오래 낡는다.
#   🔴 **문안 판 변경·설명 부재는 임계 무관**이다(아래 판정 순서 참조) — 그 둘은 "구성 변화"가 아니다.
#   절대 하한(예: "delta 2건 미만은 스킵")은 **두지 않는다**: 분모가 1 이상이라(0 은 저장 단계에서
#   거부) 작은 묶음은 비율이 자연히 크고(2→3=50%·5→6=20%), 손잡이를 둘로 늘리면 "왜 이 메타가
#   재생성됐나"를 두 규칙으로 설명해야 한다.
DESC_REGEN_MIN_DELTA_RATIO = 0.2

# ``canonical`` 안의 설명 관련 키. 저장(``upsert_meta_description``)과 낡음 판정
# (``fetch_meta_description_targets``)이 **같은 이름**을 봐야 하므로 상수로 묶는다(한쪽만 오타나면
# 매 배치가 같은 메타를 다시 만든다 — 조용히 비용만 늘어나는 종류의 결함이다).
_DESC_KEY = "description"
_DESC_COUNT_KEY = "desc_member_count"
_DESC_PV_KEY = "desc_prompt_version"

# 설명 대상 조회 — 메타별 **묶음 크기**를 함께 센다(낡음 판정의 한쪽 축이다).
#   ⚠️ 낡음 판정 자체는 **파이썬**이 한다. SQL 로 옮기면 ``(canonical->>'desc_member_count')::int``
#      캐스팅이 필요한데, 손 SQL·구버전이 남긴 문자열 하나로 **질의 전체가 터진다**(메타 835건
#      배치가 한 행 때문에 멈춘다). 행수도 메타 수(수백)라 전부 받아 걸러도 가볍다.
#   ⚠️ 바인딩 순서: (statuses, kind_code, min_bundle_size).
_DESC_TARGETS_SQL = """
SELECT en.entity_type, en.entity_uid, en.node_id,
       COALESCE(NULLIF(en.canonical->>'name', ''), en.entity_uid) AS name,
       en.canonical,
       count(*) AS bundle_size
FROM node en
JOIN graph_edge ge ON ge.dst_node = en.node_id AND ge.status = ANY(%s)
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id AND rk.kind_code = %s
JOIN node an ON an.node_id = ge.src_node AND an.node_kind = 'asset'
WHERE en.node_kind = 'entity'
GROUP BY en.node_id, en.entity_type, en.entity_uid, en.canonical
HAVING count(*) >= %s
ORDER BY count(*) DESC, en.entity_type, en.entity_uid
"""

# 설명 재료 조회 — 구성 자산의 모달리티·요약. ``asset_metadata`` 는 **LEFT JOIN** 이다:
#   INNER JOIN 이면 요약이 없는 자산(빈 STT 등)이 행에서 사라져 "구성원 수가 카드와 다른" 상태가
#   된다(``mm_classify`` 재료 조회와 같은 판단). 빈 요약을 버리는 것은 문안 조립의 몫이다.
#   요약 상한도 파이썬이 자른다 — 묶음 규모(수~수십 건)에서 전송량 차이가 없고, 모의 커넥션
#   테스트가 상한을 그대로 검증할 수 있다.
#   ⚠️ 바인딩 순서: (statuses, kind_code, entity_type, entity_uid).
_DESC_MEMBERS_SQL = """
SELECT a.modality, m.ext_meta->>'summary' AS summary
FROM node en
JOIN graph_edge ge ON ge.dst_node = en.node_id AND ge.status = ANY(%s)
JOIN relation_kind rk ON rk.relation_kind_id = ge.relation_kind_id AND rk.kind_code = %s
JOIN node an ON an.node_id = ge.src_node AND an.node_kind = 'asset'
JOIN asset a ON a.asset_id = an.asset_id
LEFT JOIN asset_metadata m ON m.asset_id = a.asset_id
WHERE en.node_kind = 'entity' AND en.entity_type = %s AND en.entity_uid = %s
ORDER BY a.asset_id
"""

def fetch_meta_members(
    conn: Connection[Any],
    entity_type: str,
    entity_uid: str,
    *,
    summary_max_chars: int = MEMBER_SUMMARY_MAX_CHARS,
) -> list[tuple[str, str]]:
    """메타 하나의 **설명 재료**(모달리티, 요약)를 읽는다(읽기 전용·결정적).

    반환 모양이 ``describe.describe_meta`` 의 입력 계약과 **그대로 같다** — 호출부(배치)에 변환
    코드가 필요 없다. ``asset_id`` 를 함께 주지 않는 이유: 설명의 재료가 아니고, 구성 자산 목록은
    묶음 조회(``src.relations.graph_query.mm_meta_bundle``)가 이미 준다(같은 것을 두 곳에서 세면
    어긋날 때 어느 쪽을 믿을지 모른다).

    Args:
        entity_type: 개체 타입(닫힌 5종). 같은 표기·다른 타입은 별개 메타다(동음이의 분리).
        entity_uid: 표기 키. 컬럼에는 정규화 키만 있으므로 입력도 같은 규칙으로 눌러 대조한다
            (``normalize_text_key`` 는 멱등이라 이미 키인 값은 그대로다 — 리포트·URL 로 오는 원표기
            차이를 흡수한다).
        summary_max_chars: 요약 하나를 담을 길이 상한(글자). 기본 150 = 파일럿 기준선.
            **1 미만이면 예외** — 0 을 허용하면 재료가 빈 프롬프트가 나가고, 그러면 LLM 이 이름만
            보고 문장을 지어낸다(외부 지식 금지 규칙이 무력해진다).

    Returns:
        ``[(모달리티, 요약)]`` — **asset_id 오름차순**(같은 묶음이면 늘 같은 프롬프트가 나온다 ·
        결정성). 값은 전부 문자열로 눌러 담는다(``None`` 이 문안에 "None" 으로 새지 않게).
        메타가 없거나 노출 대상 상태의 소속이 없으면 빈 목록.

    Raises:
        MmMetaPersistError: ``summary_max_chars`` 가 1 미만일 때.
    """
    limit = int(summary_max_chars)
    if limit < 1:
        raise MmMetaPersistError(
            f"요약 상한은 1 이상이어야 한다: {summary_max_chars!r} "
            "(0 이면 재료 없는 프롬프트가 나가고 설명이 통째로 환각이 된다)"
        )
    uid = normalize_text_key(entity_uid)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_DESC_MEMBERS_SQL,
                    (list(MM_META_VISIBLE_STATUSES), MM_MEMBER_KIND_CODE, entity_type, uid))
        rows = cur.fetchall()
    return [
        (str(r["modality"] or ""), str(r["summary"] or "").strip()[:limit]) for r in rows
    ]


def upsert_meta_description(
    conn: Connection[Any],
    entity_type: str,
    entity_uid: str,
    *,
    description: str,
    member_count: int,
    prompt_version: str,
) -> dict[str, Any]:
    """메타 설명을 ``node.canonical`` 에 **병합 저장**한다(멱등 · DB 에 쓴다 · 마이그레이션 0).

    왜 병합인가: ``canonical`` 한 칸에 대표 표기(``name``)·출처(``source``)·별칭(``aliases``)이 이미
    살고 있다. 통째로 덮으면 **사용자가 등록한 이름과 별칭이 설명 저장으로 지워진다** — 등록 메타의
    그 두 필드는 사람 소유이므로 배치가 건드리지 않는다(spec §6-1).

    세 값을 **함께** 남긴다: 설명 + 그때의 묶음 크기 + 문안 판. 뒤의 둘이 없으면 재생성 대상 판정
    (``fetch_meta_description_targets``)이 성립하지 않는다 — 구성이 바뀌어도, 문안을 고쳐도 낡은
    설명이 영구히 카드에 남는다.

    🔴 **성공한 설명만 저장한다.** 실패(``describe.MetaDescription.ok=False``)를 저장하면 그 메타는
    다음 배치에서 "설명 있음·최신"으로 보여 다시 만들어지지 않는다(장애가 굳는다).

    Args:
        entity_type: 개체 타입(닫힌 5종).
        entity_uid: 표기 키(원표기도 받는다 — ``normalize_text_key`` 로 눌러 대조한다).
        description: 저장할 한 문장. 빈 값·공백뿐이면 예외. **길이 상한은 검사하지 않는다** —
            그 정책은 ``describe.interpret_description`` 이 지킨다(같은 규칙을 두 곳에서 검사하면
            한쪽만 고쳐져 갈린다).
        member_count: 이 설명을 만든 시점의 **묶음 크기**(= 카드가 세는 "확인된 N건"). 재료 줄 수가
            아니다 — 요약 없는 자산이 재료에서 빠져도 이 값은 묶음 크기여야 낡음 판정이 성립한다
            (판정이 같은 축을 비교하지 않으면 매 배치가 같은 메타를 다시 만든다). **1 미만은 예외**:
            0 을 저장하면 묶음 크기와 절대 같아지지 않아 무한 재생성이 된다.
        prompt_version: 문안 판(``describe.DESC_PROMPT_VERSION``). 빈 값은 예외 — 현행 판과 절대
            같아지지 않으므로 역시 무한 재생성이다.

    Returns:
        ``{action, node_id, entity_type, entity_uid, description, member_count, prompt_version}``.
        ``action`` 은 ``updated``(저장)·``unchanged``(값이 같아 쓰기 0).

    Raises:
        MmMetaPersistError: 설명·건수·문안 판이 계약을 어겼거나 **그 메타가 없을 때**. 조용히 0건을
            쓰면 "설명이 왜 안 붙나"를 매번 다시 조사한다(계약 ⑤ 침묵 금지). 검사는 전부 쓰기보다
            먼저 한다.
    """
    text = str(description or "").strip()
    if not text:
        raise MmMetaPersistError("메타 설명이 비어 있다 — 실패한 생성 결과는 저장하지 않는다")
    count = int(member_count)
    if count < 1:
        raise MmMetaPersistError(
            f"묶음 크기는 1 이상이어야 한다: {member_count!r} "
            "(0 을 저장하면 낡음 판정이 영원히 참이 되어 매 배치가 다시 만든다)"
        )
    version = str(prompt_version or "").strip()
    if not version:
        raise MmMetaPersistError(
            "문안 판(prompt_version)이 비어 있다 — 현행 판과 같아질 수 없어 무한 재생성이 된다"
        )

    uid = normalize_text_key(entity_uid)
    with conn.cursor() as cur:
        cur.execute(_SELECT_ENTITY_NODE_SQL, (entity_type, uid))
        row = cur.fetchone()
    if row is None:
        raise MmMetaPersistError(
            f"메타가 없다: type={entity_type} uid={uid} — 설명은 이미 있는 메타에만 붙인다"
            "(배치가 대상을 읽은 뒤 메타가 사라졌다면 그 메타는 건너뛴다)"
        )

    node_id = str(row[0])
    existing = row[1] if isinstance(row[1], dict) else {}
    canonical = {**existing, _DESC_KEY: text, _DESC_COUNT_KEY: count, _DESC_PV_KEY: version}
    if canonical == existing:
        # 같은 내용을 다시 쓰면 얻는 것 없이 행만 흔든다(``register_mm_meta`` 와 같은 규율).
        return {"action": "unchanged", "node_id": node_id, "entity_type": entity_type,
                "entity_uid": uid, "description": text, "member_count": count,
                "prompt_version": version}

    with conn.cursor() as cur:
        cur.execute(_UPDATE_ENTITY_CANONICAL_SQL,
                    (json.dumps(canonical, ensure_ascii=False), node_id))
    return {"action": "updated", "node_id": node_id, "entity_type": entity_type,
            "entity_uid": uid, "description": text, "member_count": count,
            "prompt_version": version}


def _stored_member_count(canonical: Mapping[str, Any]) -> int | None:
    """저장된 ``desc_member_count`` 를 정수로 읽는다(못 읽으면 ``None``).

    Args:
        canonical: 노드의 ``canonical`` 값(손 SQL·구버전이 넣은 **무엇이든** 올 수 있다고 가정한다).

    Returns:
        정수 건수. 키가 없거나 정수로 읽을 수 없으면 ``None`` — 호출부는 이것을 "판 미상"으로 보고
        **재생성 대상**으로 둔다(``parse_member_reason`` 이 구 형식 스탬프를 다루는 것과 같은 규율:
        모호하면 다시 만든다).
    """
    raw = canonical.get(_DESC_COUNT_KEY)
    if isinstance(raw, bool) or raw is None:
        return None  # bool 은 int 하위형이라 먼저 걸러 낸다(True 가 1 로 읽히면 안 된다)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _member_count_is_stale(
    stored: int | None, bundle_size: int, *, min_delta_ratio: float
) -> bool:
    """구성 변화가 **재생성할 만큼** 큰가(순수 · 비율 임계 판정).

    판정 예시(임계 0.2 기준): 2→3 은 50% 로 **재생성** · 13→14 는 7.7% 로 **스킵** · 13→17 은 31% 로
    **재생성**. 분모는 그 설명을 만든 시점의 건수이므로, 스킵된 변화는 사라지지 않고 다음 판정에
    누적된다(13 에 저장하고 14·15 를 스킵하면 16 에서 23% 로 걸린다).

    Args:
        stored: 저장된 ``desc_member_count``. ``None``(키 없음·정수 아님)이면 **재생성 대상**이다 —
            비교할 기준이 없으면 보수적으로 다시 만든다(``parse_member_reason`` 이 구 형식 스탬프를
            "판 미상 → 재대상"으로 다루는 것과 같은 규율).
        bundle_size: 지금 세어 본 묶음 크기(노출 대상 상태의 소속 수).
        min_delta_ratio: 비율 임계(0 초과). 변화 비율이 **이 값 미만이면 대상이 아니다**.

    Returns:
        재생성 대상이면 ``True``.
    """
    if stored is None or stored < 1:
        # 0·음수는 손 SQL 이 남긴 값이다 — 비율의 분모가 될 수 없다(0 나눗셈). 모호하면 다시 만든다.
        return True
    delta = abs(int(bundle_size) - stored)
    if delta == 0:
        return False
    return delta / stored >= min_delta_ratio


def fetch_meta_description_targets(
    conn: Connection[Any],
    *,
    prompt_version: str,
    min_bundle_size: int = 2,
    min_delta_ratio: float = DESC_REGEN_MIN_DELTA_RATIO,
) -> list[dict[str, Any]]:
    """설명이 **없거나 낡은** 메타를 골라 온다(읽기 전용·결정적 · spec §8-1).

    배치가 스스로 대상을 고르는 근거다. 낡음은 두 가지이고 둘 다 **결정적 판정**이라 사람이 목록을
    관리할 필요가 없다:

        - **구성이 크게 바뀌었다** — 저장된 건수 대비 변화가 ``min_delta_ratio`` 이상. 자산이 붙거나
          빠지면 "무엇이 들어 있는지"가 달라지므로 설명도 낡는다. 다만 **조금** 바뀐 것은 대상이
          아니다(2026-08-24 결함 ③): 예전에는 "≠" 였고, 그래서 인기 개체(제주도 13→14→15…)가 자산
          1건마다 재생성됐다 — 판정 예시는 2→3(50%·재생성) · 13→14(7.7%·스킵) · 13→17(31%·재생성).
        - **문안이 바뀌었다** — 저장된 문안 판 ≠ 현행. 규칙을 고쳤으면 옛 문장은 그 규칙을 안 지킨다.
          🔴 **임계와 무관**하다(설명 부재도 마찬가지) — 그 둘은 "구성 변화"가 아니다.

    묶음 크기 1 은 기본 대상 밖이다 — 카드가 노출하지 않으므로(spec §7 리뷰 지점 ②) 설명을 만들어도
    쓰이지 않고 LLM 호출만 든다.

    Args:
        prompt_version: **현행** 문안 판(``describe.DESC_PROMPT_VERSION``). 빈 값이면 예외 — 저장된
            판과 절대 같아지지 않아 전량이 대상이 된다(비용만 드는 무한 재생성).
        min_bundle_size: 대상이 되는 최소 묶음 크기. 기본 2(카드가 노출하는 하한). 1 이하를 주면
            소속이 1건뿐인 메타까지 포함된다 — 조회가 소속 엣지를 조인하므로 소속 0건인 메타
            (수동 선등록한 빈 메타)는 어떤 값을 줘도 나오지 않는다.
        min_delta_ratio: 구성 변화를 낡음으로 볼 **비율 임계**(기본 ``DESC_REGEN_MIN_DELTA_RATIO``
            = 0.2). 0 을 주면 옛 동작("건수가 다르면 전부 재생성")으로 돌아간다 — 소급 재생성처럼
            의도적으로 전량을 다시 만들 때만 쓴다. 1 이상을 주면 웬만한 변화로는 재생성되지 않는다
            (설명이 오래 낡는다).

    Returns:
        ``[{entity_type, entity_uid, name, node_id, bundle_size, description, reason}]`` —
        **묶음 큰 순 → (타입, 표기 키) 순**(눈에 많이 띄는 카드부터 채우고, 같은 크기는 결정적으로).
        ``description`` 은 **지금 저장된**(낡은) 설명이며 없으면 빈 문자열이다 — 배치 diff 리포트가
        "무엇이 무엇으로 바뀌었나"를 보이는 데 쓴다. ``reason`` 은 ``DESC_TARGET_REASONS`` 중 하나.
        id 는 전부 문자열이다(조회행 id → str 관례). 최신이면 목록에서 빠진다.

    Raises:
        MmMetaPersistError: ``prompt_version`` 이 비었을 때.
    """
    version = str(prompt_version or "").strip()
    if not version:
        raise MmMetaPersistError(
            "현행 문안 판(prompt_version)이 비어 있다 — 전량이 낡음으로 잡혀 무한 재생성이 된다"
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_DESC_TARGETS_SQL,
                    (list(MM_META_VISIBLE_STATUSES), MM_MEMBER_KIND_CODE, int(min_bundle_size)))
        rows = cur.fetchall()

    targets: list[dict[str, Any]] = []
    for row in rows:
        canonical = row["canonical"] if isinstance(row["canonical"], dict) else {}
        stored = canonical.get(_DESC_KEY)
        description = str(stored).strip() if isinstance(stored, str) else ""
        bundle_size = int(row["bundle_size"])
        # 판정 순서 = DESC_TARGET_REASONS 순서. 먼저 걸린 사유를 돌려준다(설명이 없는데 "문안 개정"
        # 이라 적으면 리포트가 원인을 잘못 가리킨다).
        if not description:
            reason = DESC_TARGET_REASONS[0]
        elif _member_count_is_stale(_stored_member_count(canonical), bundle_size,
                                    min_delta_ratio=min_delta_ratio):
            reason = DESC_TARGET_REASONS[1]
        elif str(canonical.get(_DESC_PV_KEY) or "") != version:
            reason = DESC_TARGET_REASONS[2]
        else:
            continue  # 최신 — 다시 만들지 않는다
        targets.append({"entity_type": str(row["entity_type"]),
                        "entity_uid": str(row["entity_uid"]),
                        "name": str(row["name"] or row["entity_uid"]),
                        "node_id": str(row["node_id"]),
                        "bundle_size": bundle_size,
                        "description": description,
                        "reason": reason})
    return targets


__all__ = [
    "DESC_REGEN_MIN_DELTA_RATIO",
    "DESC_TARGET_REASONS",
    "LINEAGE_ACTIVITY",
    "LINEAGE_AGENT",
    "LIST_MM_META_DEFAULT_LIMIT",
    "MM_MEMBER_KIND_CODE",
    "MM_MEMBER_KIND_DESCRIPTION",
    "MM_MEMBER_KIND_NAME_KO",
    "MM_META_SOURCE_AUTO",
    "MM_META_SOURCE_USER",
    "MM_META_VISIBLE_STATUSES",
    "MmMetaPersistError",
    "ensure_entity_node",
    "ensure_mm_member_kind",
    "fetch_meta_description_targets",
    "fetch_meta_members",
    "fetch_official_name_index",
    "fetch_registered_alias_index",
    "format_member_reason",
    "list_mm_meta",
    "normalize_registration",
    "parse_member_reason",
    "register_mm_meta",
    "resolve_registered_aliases",
    "upsert_entity_edges",
    "upsert_meta_description",
]
