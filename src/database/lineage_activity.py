"""계보(``asset_lineage``) **활동명 정본** — 쓰는 쪽(파이프·코어)과 읽는 쪽(백엔드)이 같은 글자를 보게 한다.

무엇인가: 자산에 어떤 처리가 일어났는지 적는 이름표다(예: ``ingest.registered.v1`` = "적재 완료").
파이프라인이 처리 단계마다 이 이름으로 계보 한 행을 남기고, 백엔드는 같은 이름으로 세어 화면에 보여
준다(관계 제안이 붙은 자산 수는 ``relations.proposed.v1`` 행이 있는지로 판별한다).

왜 코어에 있나(093 1단계 · 2026-09-07): 종전에는 이 문자열이 **세 레포에 각자 리터럴**로 박혀 있었다 —
파이프 9곳·코어 2곳·백엔드 1곳. 한 글자라도 어긋나면 예외 없이 **집계가 조용히 0**이 된다(백엔드 5버킷의
``relation_proposed`` 가 그 예). 쓰는 쪽과 읽는 쪽이 같은 상수를 import 하면 어긋날 길이 없다.

표기 규약: ``<대상>.<사건>.<판>``(관계 ``relations.proposed.v1`` 선례). 판(``v1``)은 **의미가 바뀔 때만**
올린다 — 올리면 옛 행과 새 행이 다른 활동이 되어 집계가 갈린다(``mm_meta.persist.LINEAGE_ACTIVITY`` 주석 참조).

``StrEnum`` 이라 문자열처럼 동작한다: 비교(``==``)·f-string·``json.dumps`` 모두 값을 낸다. psycopg 3.3 도
SQL 파라미터로 넘기면 **값**(``StrDumperUnknown`` → ``b'ingest.received.v1'``)을 덤프한다(2026-09-07 실측).
그래서 리터럴을 이 멤버로 바꿔도 저장되는 바이트가 같다.

새 활동을 추가하면: 여기 멤버 추가 → 쓰는 쪽이 그 멤버를 쓴다 → 백엔드 집계가 필요하면 같은 멤버로 센다.
백엔드 ``/admin/lineage?activity=`` 필터는 자유 문자열을 받으므로(운영자가 타이핑) 여기로 검증하지 않는다.
"""
from __future__ import annotations

from enum import StrEnum


class LineageActivity(StrEnum):
    """``asset_lineage.activity`` 에 저장되는 활동명 — 3레포 공용 정본."""

    # ── 수집·적재(파이프 ``processing.ingest`` 가 남긴다) ──
    INGEST_RECEIVED = "ingest.received.v1"        # 파일 픽업·asset 행 생성
    INGEST_ROUTING = "ingest.routing.v1"          # 모달리티·경로 판정 시작
    INGEST_CLASSIFYING = "ingest.classifying.v1"  # 도메인·분류 시작
    INGEST_CLASSIFIED = "ingest.classified.v1"    # 분류 결과 확정(상태값이 아니라 결과 표식)
    INGEST_DEFERRED = "ingest.deferred.v1"        # 의료 표준 포맷 추출 보류(종착)
    INGEST_EXTRACTING = "ingest.extracting.v1"    # 추출·임베딩·적재 시작
    INGEST_REGISTERED = "ingest.registered.v1"    # 적재 완료(종착)
    INGEST_FAILED = "ingest.failed.v1"            # 잡힌 예외로 실패 — 재시도 cap 의 카운트 소스
    INGEST_RESET = "ingest.reset.v1"              # 고착 자산 received 리셋 — 크래시 루프 cap 카운트 소스
    # ── 관계(코어 ``relations.asset_entry`` 가 남긴다) ──
    RELATIONS_PROPOSED = "relations.proposed.v1"  # LLM 관계 제안 저장 완료 — 백엔드 5버킷 판별 축
    # ── 멀티모달 메타(코어 ``mm_meta.persist`` 가 남긴다) ──
    ENTITY_JUDGED = "entity.judged.v1"            # 개체 판정 이력 — 백필 대상 선별 축(바꾸면 전량 재판정)


__all__ = ["LineageActivity"]
