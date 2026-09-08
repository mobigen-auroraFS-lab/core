# CHANGELOG — 공개 API 변경만 적습니다

이 파일은 **다른 레포(파이프라인·백엔드)가 import 하는 이름**(README 「공개 API」 표)의 변경만 기록합니다.
내부 구현 변경은 적지 않습니다 — 그 이력은 문서 레포 설계이력에 있습니다.

버전은 git 태그 `vMAJOR.MINOR.PATCH` 로 관리합니다(`pyproject.toml` 의 버전은 건드리지 않습니다).

| 올리는 자리 | 언제 |
|---|---|
| **MAJOR** | 공개 API 를 **깨는** 변경 — 이름·인자·반환 모양이 바뀌거나 사라짐. 소비 레포 PR 을 같은 날 함께 연다 |
| **MINOR** | 공개 API **추가** — 기존 호출은 그대로 동작 |
| **PATCH** | 공개 API 무변경 — 내부 수정·버그 수정 |

## [v0.6.0] — 2026-09-08 (096 파일 검색 조회 — 조건으로 좁히고 정확히 센다)

### 추가 (MINOR — 기존 호출 무변경)
- `src.search.file_search.search_files(client, index, *, query, query_vector, filters=None, from_=0, size=50, …)` — 파일 검색 화면(시나리오 ③)용 조회. 집합을 **단어 일치 + 조건**으로 확정하고, 순서는 검색 엔진의 정규화·결합 파이프라인이 매기며, 개수·좁히기 칩을 **검색 엔진이 센다**. 그래서 "적힌 숫자 = 누르면 나오는 수"가 성립한다(실측 410건 불일치 0).
  - 함께 공개: `build_rank_body`·`build_facet_body`·`build_facet_plan`(순수 · 본문·계획 조립) · `FACET_FIELDS` · `FACET_SELF_FILTERS` · 설계 상수 `RANK_DEPTH_DEFAULT`(1,000) · `TOTAL_CAP_DEFAULT`(10,000) · `FACET_SIZE_DEFAULT`(24) · `SORT_DEPTH_DEFAULT`(10,000) · `SEARCH_PIPELINE_DEFAULT` · `WORD_FIELDS_DEFAULT`.
  - ⚠️ **관련도 컷오프를 쓰지 않는다** — 컷오프는 받아온 뒤 파이썬에서 계산하므로 검색 엔진이 셀 수 없고, 세는 대상이 확정되지 않으면 칩 건수가 클릭 결과와 어긋난다. 기존 `search_hybrid`(멀티모달 검색 화면)는 컷오프를 그대로 유지한다.
  - **칩은 축마다 자기 조건을 뺀 채 센다**(`build_facet_plan`) — 그래야 주제를 고른 뒤에도 다른 주제로 갈아탈 수 있다(전부 적용해 세면 고른 주제 하나만 남는다 · 실측 칩 5·9개 → 1·3개). 하위주제는 주제의 자식이라 주제 축에서 함께 뺀다. 뺄 조건이 같은 축은 한 질의로 묶어 **왕복 한 번**으로 보낸다(`msearch`). 칩 숫자의 뜻 = **그 칩 하나만 골랐을 때 나오는 수**(다른 축 조건은 적용).
  - **정렬** `sort=` — `SORT_OPTIONS` 의 닫힌 목록(`relevance` 기본 · `name_asc`/`name_desc` · `created_desc`/`created_asc`). 필드 정렬은 **벡터 질의를 보내지 않는다**(순서를 필드가 정하므로) → 임베딩이 필요 없고(`query_vector=None` 허용) 정규화 파이프라인도 붙이지 않으며, 하이브리드의 깊이 제약이 사라져 `SORT_DEPTH_DEFAULT` 까지 넘길 수 있다. ⚠️ **수정일·크기 정렬은 색인에 필드가 없어 불가**(넣으려면 파이프라인 색인 매핑 변경 + 전량 재색인).
- `search_filters.SearchFilters` 의 주제·하위주제가 **여럿**을 받는다 — `topics: tuple[str, ...]`·`subtopics: tuple[str, ...]`. 같은 축의 여러 값은 「또는」(태그와 같은 규칙)이며 OS 절은 종전과 같은 `terms` 배열이라 모양이 바뀌지 않는다. `parse_search_filters(topic=…, subtopic=…)` 는 **문자열 하나도 목록도** 받는다. 종전 이름 `.topic`·`.subtopic` 은 **첫 값을 주는 읽기 전용 속성**으로 남겨 소비 코드가 깨지지 않는다(새 코드는 복수 이름을 읽는다).

## [v0.5.0] — 2026-09-07 (095 개체 화면 seam · 라벨 읽기 · 이유 코드)

### 추가 (MINOR — 기존 호출 무변경)
- `relations.graph_query.list_entities` · `count_entities_by_type` · `count_entities_by_area` · `assets_of_entities` — 개체(멀티모달 메타) 화면의 목록·종류별 수·갈래별 수·구성 자산 조회 seam. 데모 라우트의 직접 SQL 을 코어로 올린 것(실 DB 대조 테스트로 결과 동일 증명).
- `relations.graph_query.mm_meta_bundle` 반환에 `description` 키 추가(키 추가만 · 기존 키 불변).
- `mm_classify.read.label_names_of_assets` — 자산들의 스킬 라벨 **이름**을 정의 순서로(미부여 제외). `fetch_active_skills` 재수출.
- `mm_meta.entity_search.match_entity_reason` + `REASON_CODE_NAME|KEYWORD|DESCRIPTION` — 걸린 이유를 문구가 아니라 코드·토큰으로. 종전 `match_entity` 문구는 이 코드에서 조립되며 동일.
- 공개 API 표에 개체 화면이 쓰는 기존 이름들을 등재: `entity_search` 함수·상수, `entity_embedding.find_similar_entities`, `search.entity_search_os.search_entities_hybrid`, `search.query_embed.embed_query_for_media_search`, `search.opensearch_sync.get_client`, `settings.active_embed_channel`, `search_constants.ENTITY_INDEX_DEFAULT`(코드 변경 0 · 계약면 명시).

## [v0.4.0] — 2026-09-07 (093 5단계 · 설정 역할 · 소비 레포 부트스트랩)

### 추가 (MINOR — 기존 호출 무변경)
- `settings.init_settings(profile, *, role="processing")` — 설정 초기화 **역할**. `serving`(HTTP API)은 적재 전용 필수 env 5개(`ENCODING`·`CHUNK_SIZE`·`OVERLAP_SIZE`·`SUMMARY_MAX_CHARS`·`TOP_K_KEYWORDS`)가 없어도 자리값으로 기동. 기본 `processing` 은 종전과 같다.
- `bootstrap.bootstrap_env(env, *, repo_root=None, role="processing")` — 공개 API 표에 등재. 소비 레포가 `repo_root=` 로 자기 `.env` 를 읽고 `role=` 로 역할을 고른다. 인자 없는 호출은 종전과 같다.

### 문서
- README 「이 레포에 대해」를 공개 우선 개발(2026-08-06) 이후 사실에 맞게 고침. `.env.example` 에서 백엔드 전용 절을 빼고 보관 경로 설명을 정정(백엔드는 DB `fs_path` 를 읽음).

## [v0.3.0] — 2026-09-07 (093 2단계 · 검색 손잡이 · 패싯 집계 정본)

### 추가 (MINOR — 기존 호출 무변경)
- `search_service.search_hybrid(…, tuning: SearchTuning | None = None)` — 검색 튜닝 묶음을 호출자가 넘기는 손잡이. `None`(생략)이면 종전과 같이 설정에서 해소한다. 백엔드가 닫힌 프리셋을 값으로 바꿔 넘기는 자리(ADR 2026-09-02 §6).
- `src.search.facets.aggregate_facets(rows, *, keys_of, top_n=None, min_count=1, unit_of=None, label_of=…)` — 축과 무관한 결과-스코프 패싯 집계 정본(단위당 1회 계수·하한·상위 N·결정적 정렬·최빈 표기). 호출자가 "무엇으로 묶는가"(`keys_of`)만 준다.
- `src.config.search_constants.TAG_FACET_TOP_N_DEFAULT` · `TAG_FACET_MIN_COUNT_DEFAULT` — 태그 패싯 표시 기본값(12·2). 설정 `SEARCH_TAG_FACET_*` 의 기본과 소비 레포의 "설정 미초기화" 폴백이 같은 값을 보게.

### 내부(공개 API 무변경)
- `tag_facets.aggregate_tag_facets` 는 위 정본의 태그 축 래퍼가 됐다 — 입·출력 동일(무작위 300조합 대조 테스트).

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
