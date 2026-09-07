# CHANGELOG — 공개 API 변경만 적습니다

이 파일은 **다른 레포(파이프라인·백엔드)가 import 하는 이름**(README 「공개 API」 표)의 변경만 기록합니다.
내부 구현 변경은 적지 않습니다 — 그 이력은 문서 레포 설계이력에 있습니다.

버전은 git 태그 `vMAJOR.MINOR.PATCH` 로 관리합니다(`pyproject.toml` 의 버전은 건드리지 않습니다).

| 올리는 자리 | 언제 |
|---|---|
| **MAJOR** | 공개 API 를 **깨는** 변경 — 이름·인자·반환 모양이 바뀌거나 사라짐. 소비 레포 PR 을 같은 날 함께 연다 |
| **MINOR** | 공개 API **추가** — 기존 호출은 그대로 동작 |
| **PATCH** | 공개 API 무변경 — 내부 수정·버그 수정 |

## [v0.2.0] — 2026-09-07 (093 1단계 · 어휘·상수 정본)

### 추가 (MINOR — 기존 호출 무변경)
- `src.database.lineage_activity.LineageActivity` — 계보 활동명 11종(StrEnum · psycopg 는 값으로 덤프).
- `src.domain.status_vocab.RelationKindStatus` — `relation_kind.status`(active·inactive).
- `src.config.search_modalities.MODALITY_TO_BUCKET` · `BUCKET_TO_MODALITY` — 검색 버킷 키 ↔ 모달리티.
- `src.relations.approval_policy.TIER_ORDER` · `tier_rank` — 노출 등급 순위(강칸 먼저).
- `src.domain.numeric.safe_float` — 유한 실수 정화(의존 0).

### 내부(공개 API 무변경)
- 코어 안 리터럴·사본 7곳을 위 정본 참조로. `search_service._MODALITY_BUCKETS`·`fusion._safe_float` 는 별칭으로 유지.

## [v0.1.0] — 2026-09-07 (093 0단계 · 계약면 가드)

### 추가
- README 「공개 API」 표 신설 + `tests/test_public_api.py`(표의 이름이 import 되고 밑줄이 없고 표와 같음을 봉인).
- 첫 CI 워크플로 `.github/workflows/ci.yml` — 코어 게이트(ruff·단위 테스트·policy_gate·test_integrity·args_gate)
  + 백엔드 소비자 스모크(백엔드 `main` 의 DB 없는 단위 테스트를 이 코어에 대해 실행).

## 예고

### 다음 MINOR
- `src.search.search_service.search_hybrid(..., tuning=)` 인자 추가(093 2단계).

### 다음 MAJOR 후보 (095 이후)
- `src.search.refine.asset_refine_fields` 제거 — 백엔드 `service.portal.search_group` 으로 이동 완료(2026-09-07).
- `src.relations.review._REVIEW_STATUSES` — 내부 이름. 백엔드는 `GraphEdgeStatus` 를 쓴다(2026-09-07).
