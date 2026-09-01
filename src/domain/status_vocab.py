"""DB ``CHECK (… IN (…))`` 제약과 동기화하는 닫힌 어휘(StrEnum) — spec 042.

PostgreSQL ``ENUM`` 타입이 아니라 기존 마이그레이션의 ``TEXT + CHECK IN`` 과 **코드만 동기**.
신규 값 추가 시: DDL CHECK · 본 모듈 · ``tests/test_status_vocab.py`` 동시 갱신.

OM 039~042 에서 쓰이는 어휘
    ``AccessTier``              — 040 v290/v291 ``access_tier`` 컬럼
    ``RegistryFieldStatus``     — 041 ``ext_meta_field_registry.status``
    ``GraphEdgeStatus``         — cross-asset (v230, 042에서 타입 정본화만)
    ``RelationResolutionStatus``— 관계 큐 (v250/v260, 042에서 타입 정본화만)

``AssetStatus`` 는 **값 목록만 여기 있고 전이 규칙은 파이프라인**(``processing.ingest.status``)에 있다.
왜 나눴나(2026-09-02): 값은 파이프·백엔드 **둘 다** 쓰는데 전이(FSM)는 파이프만 쓴다. 값까지 파이프에
두면 백엔드가 가져다 쓸 길이 없어(3레포 구조상 service 는 pipeline 을 의존하지 않는다) 문자열을
직접 타이핑하게 된다 — 실제로 백엔드 17곳·코어 23곳·파이프 21곳에 흩어져 있었다.

주의(US-F 스코프): ``GraphEdgeStatus``/``RelationResolutionStatus`` 는 prod 코드가 값을 직접
참조하지 않고 ``tests/test_status_vocab.py`` 의 DDL 교차검증만 소비한다. 그럼에도 삭제하지 않는다
— DB CHECK 제약과 동기화되는 '닫힌 어휘의 정본'이므로 소비처가 테스트뿐이어도 스키마 계약 자체다
(US-F 죽은코드 청소에서 의도적으로 제외·069 코드리뷰 2026-07-15).
"""

from __future__ import annotations

from enum import StrEnum


class AssetStatus(StrEnum):
    """``asset.status`` CHECK 7값 (v160 · `migrations/sql/160_asset_status_deferred.sql`).

    정상 경로: received → routing → classifying → extracting → registered.
    종착은 셋 — ``registered``(정상)·``failed``(오류)·``deferred``(의료 표준 포맷 추출 보류).

    🔴 **값 목록만 여기 있다.** 전이 규칙(``ALLOWED_TRANSITIONS``·``TERMINAL``)은 파이프라인
    ``processing.ingest.status`` 소관이다 — 상태를 **바꾸는** 것은 처리 파이프라인의 일이고,
    상태를 **읽는** 것은 백엔드·코어도 한다. 읽는 쪽이 값을 알려면 값이 공유 코어에 있어야 한다.

    ⚠️ 값을 늘리면 **셋을 함께** 고친다: DDL CHECK · 이 Enum · 파이프의 ``ALLOWED_TRANSITIONS``.
    앞의 둘은 ``tests/test_status_vocab.py`` 가 DDL 파일과 대조해 잡는다.
    """

    RECEIVED = "received"        # 파일 픽업·asset 행 생성 직후
    ROUTING = "routing"          # 모달리티·경로 판정 중
    CLASSIFYING = "classifying"  # 도메인·modality 분류 중
    EXTRACTING = "extracting"    # 추출·임베딩·적재 중
    REGISTERED = "registered"    # 적재 완료·종착 — 검색·관계 배치 노출 대상
    FAILED = "failed"            # 오류 종착(status_reason 에 사유)
    DEFERRED = "deferred"        # 의료 표준 포맷 추출 보류·종착(실패가 아니다 · 단계 D 대기)


class AccessTier(StrEnum):
    """``ext_meta_field_registry.access_tier`` CHECK 4값 (v290·v291 · spec 040).

    ordinal(낮→높): public < authenticated < authorized < regulated.
    read projection(042): principal clearance ≥ effective_field_tier 인 키만 응답에 포함(미달 키 omit).
    """

    PUBLIC = "public"  # 익명·비인증 principal.
    AUTHENTICATED = "authenticated"  # 필드 tier·중간 clearance.
    AUTHORIZED = "authorized"  # JWT 2-tier MVP clearance 상한(042).
    REGULATED = "regulated"  # 규제·의료 등 최고 등급 — ``domain_floor(medical)`` 바닥.


class GraphEdgeStatus(StrEnum):
    """``graph_edge.status`` CHECK (v230) — cross-asset 관계 HITL 검토 상태."""

    PROPOSED = "proposed"  # LLM 제안·미검토(기본값).
    ACTIVE = "active"  # 승인·자동승인 — graph_query·포탈 노출 대상.
    REJECTED = "rejected"  # 반려 — 검색·그래프 조회에서 제외.


class RelationResolutionStatus(StrEnum):
    """``relation_resolution.status`` CHECK (v250·v260) — 관계 해소 배치 큐 상태."""

    PENDING = "pending"  # 미해소·일시 실패 후 재시도 대기.
    RESOLVED = "resolved"  # 관계≥1 생성 완료.
    ISOLATED = "isolated"  # 평가 완료·관계 0(고립, 실패≠) — 재평가 대상.
    FAILED = "failed"  # 재시도 상한 도달(DLQ).


class RegistryFieldStatus(StrEnum):
    """``ext_meta_field_registry.status`` / 레거시 ``schema_registry.status`` (041)."""

    ACTIVE = "active"  # ingest ``validate_ext_meta`` · read ``fetch_access_tiers`` 대상.
    INACTIVE = "inactive"  # 비활성 — 레지스트리 조회·검증 제외(행 보존).


class MmSkillStatus(StrEnum):
    """``mm_skill.status`` (085 · v302) — 분류 스킬 활성 상태."""

    ACTIVE = "active"  # 분류 배치(run_mm_classify) 대상.
    DISABLED = "disabled"  # 중단 — 배치 제외(판정 이력 asset_mm_skill_label 은 보존).
