# CHANGELOG — 공개 API 변경만 적습니다

이 파일은 **다른 레포(파이프라인·백엔드)가 import 하는 이름**(README 「공개 API」 표)의 변경만 기록합니다.
내부 구현 변경은 적지 않습니다 — 그 이력은 문서 레포 설계이력에 있습니다.

버전은 git 태그 `vMAJOR.MINOR.PATCH` 로 관리합니다(`pyproject.toml` 의 버전은 건드리지 않습니다).

| 올리는 자리 | 언제 |
|---|---|
| **MAJOR** | 공개 API 를 **깨는** 변경 — 이름·인자·반환 모양이 바뀌거나 사라짐. 소비 레포 PR 을 같은 날 함께 연다 |
| **MINOR** | 공개 API **추가** — 기존 호출은 그대로 동작 |
| **PATCH** | 공개 API 무변경 — 내부 수정·버그 수정 |

## [Unreleased] — 2026-09-09 (형태소 분석기가 조사를 걷어낸다 · 분석기 어긋남 감지)

### 변경 (MINOR — 반환값 추가 · 기존 호출 무변경)
- `ensure_index` 반환값에 **`'analysis-stale'`** 추가 — 색인의 분석기 설정이 코드 정본과 다르면 이 값을 준다(빠진 필드 보강은 그대로 한다). 분석기는 매핑처럼 덧붙여 못 고치므로 `recreate=True` 로 다시 만들어야 반영된다. 상태 문자열을 `'exists'`/`'updated'` 로만 분기하던 호출부는 이 값을 "재생성 필요" 로 다뤄야 한다.
- `build_index_body` 의 `nori_user` 분석기에 **조사 제거**(`nori_part_of_speech` J* 9태그 + 불용어 `의`)를 붙였다 — `화학에서` 가 색인·질의 양쪽에서 `화학` 이 된다. 🔴 **기존 색인은 재생성해야 반영된다**(`run_opensearch_resync --recreate`). 근거: 골든 758질의 재측정 — 조사 층 파일 검색 재현 0.33→1.00 · 다른 층 회귀 0.
- 상수 추가 `src.config.search_constants.NORI_STOPTAGS_DEFAULT` · `NORI_STOPWORDS_DEFAULT`(왜·태그 뜻은 상수 주석).

## [v0.6.0] — 2026-09-08 (096 파일 검색 조회 — 조건으로 좁히고 정확히 센다)

### 추가 (MINOR — 기존 호출 무변경)
- `src.search.file_search.search_files(client, index, *, query, query_vector, filters=None, from_=0, size=50, …)` — 파일 검색 화면(시나리오 ③)용 조회. 집합을 **단어 일치 + 조건**으로 확정하고, 순서는 검색 엔진의 정규화·결합 파이프라인이 매기며, 개수·좁히기 칩을 **검색 엔진이 센다**. 그래서 "적힌 숫자 = 누르면 나오는 수"가 성립한다(실측 410건 불일치 0).
  - 함께 공개: `build_rank_body`·`build_facet_body`·`build_facet_plan`(순수 · 본문·계획 조립) · `FACET_FIELDS` · `FACET_SELF_FILTERS` · 설계 상수 `RANK_DEPTH_DEFAULT`(1,000) · `TOTAL_CAP_DEFAULT`(10,000) · `FACET_SIZE_DEFAULT`(24) · `SORT_DEPTH_DEFAULT`(10,000) · `SEARCH_PIPELINE_DEFAULT` · `WORD_OPERATOR_DEFAULT`(`and`) · `SEMANTIC_MIN_COSINE_DEFAULT`(0.60).
  - **집합 = 단어 ∪ 뜻이 임계 이상**(2026-09-08 사용자 지시로 재설계). 단어 절은 멀티모달 검색과 **같은 것**을 쓰고(`query_builder.build_word_should` 신설 — 각자 만들면 같은 질의가 다른 파일을 찾는다 · 실측 상위 10 중 3건만 겹침), 뜻은 「상위 k개」가 아니라 **유사도 하한**(radial kNN `min_score`)으로 청한다. k 로 청하면 관련이 없어도 k 개를 채워 주므로 개수가 질의가 아니라 k 가 정한다(실측: 코퍼스에 없는 `컬링`·`베이글` 도 k=100 이면 100건).
  - 🔴 **의미 집합은 id 목록으로 굳혀 모든 질의가 공유한다**(`build_semantic_body` · `SEMANTIC_CAP_DEFAULT` 500). 벡터 검색은 근사라 필터 유무로 찾아내는 문서가 달라지는데(작은 집합에서는 전수 비교로 바뀐다) 축별 집계는 필터를 일부러 바꾼다 — 질의마다 다시 하면 칩 건수와 클릭 결과가 어긋난다(실측 `등산` 칩 12 대 클릭 13). 조건 없이 한 번만 구해 굳히면 조건이 무엇이든 같은 문서를 가리킨다(질의 1회 추가).
  - 임계 0.60 은 골든 464질의 전수 측정으로 골랐다 — 되찾음:잡음 101:107(0.55 는 213:1,022) · 자료 없는 질의 34개 중 32개가 0건 유지. 효과: `남한산성` 354→3건 · `클래식 피아노 연주회` 0→4건(글자로는 못 찾던 베토벤·피아노 자료 회복) · `컬링` 1건 · `증권 약관` 0건.
  - ⚠️ **정렬과 무관하게 질의 임베딩이 필요하다** — 뜻이 집합 판정에 쓰이므로. 정렬에 따라 개수가 달라지면 화면이 거짓말을 한다(v0.6.0 개발 중 "필드 정렬은 임베딩 생략" 최적화를 철회).
  - ⚠️ **관련도 컷오프를 쓰지 않는다** — 컷오프는 받아온 뒤 파이썬에서 계산하므로 검색 엔진이 셀 수 없고, 세는 대상이 확정되지 않으면 칩 건수가 클릭 결과와 어긋난다. 기존 `search_hybrid`(멀티모달 검색 화면)는 컷오프를 그대로 유지한다.
  - **칩은 축마다 자기 조건을 뺀 채 센다**(`build_facet_plan`) — 그래야 주제를 고른 뒤에도 다른 주제로 갈아탈 수 있다(전부 적용해 세면 고른 주제 하나만 남는다 · 실측 칩 5·9개 → 1·3개). 하위주제는 주제의 자식이라 주제 축에서 함께 뺀다. 뺄 조건이 같은 축은 한 질의로 묶어 **왕복 한 번**으로 보낸다(`msearch`). 칩 숫자의 뜻 = **그 칩 하나만 골랐을 때 나오는 수**(다른 축 조건은 적용).
  - **정렬** `sort=` — `SORT_OPTIONS` 의 닫힌 목록 9종(`relevance` 기본 · 이름 · 수정일 · 등록일 · 크기 각 ↑↓). 필드 정렬은 **벡터 질의를 보내지 않는다**(순서를 필드가 정하므로) → 임베딩이 필요 없고(`query_vector=None` 허용) 정규화 파이프라인도 붙이지 않으며, 하이브리드의 깊이 제약이 사라져 `SORT_DEPTH_DEFAULT`(10,000)까지 넘길 수 있다. 동률은 자산 id 로 갈린다(페이징 중복·누락 방지).
  - 🔴 **줄을 세우는 값은 화면에 보이는 값이다** — 이름 정렬은 새 색인 필드 `file_name_sort`(= `display_file_name` 그대로)를 쓴다. 검색용 `file_name`(잡음 정제 값)으로 세우면 화면의 **84.5%가 제자리에 오지 않는다**(실측 1,526건 · 색인값과 화면값이 일치하는 자산은 0건).

### 변경 (색인 매핑 · 소비 레포 조치 필요)
- `opensearch_sync.build_index_body` 에 정렬용 필드 3종 추가 — `file_name_sort`(keyword) · `file_size`(long) · `filter_date.updated_at`(date). 재동기화 SELECT 에 `a.updated_at`·`a.file_size` 를 싣고 `build_filter_index_fields(updated_at=…, file_size=…)` 가 채운다. 날짜는 생성일과 같은 **날짜 단위**(화면 표도 날짜까지만 보인다 · `filter_date.created_at` 의 단위는 **바꾸지 않았다** — 전체 타임스탬프로 바꾸면 `created_to` 가 그 날 오전 0시로 해석되어 하루가 빠진다).
- `opensearch_sync.ensure_index` 가 기존 색인에 **빠진 매핑 속성만 보강**한다(반환값에 `'updated'` 추가). 보강 없이 재색인하면 검색 엔진 자동 매핑으로 문자열이 분석 필드가 되어 **정렬만 조용히 실패**한다. 기존 필드 정의는 건드리지 않는다(그때는 `--recreate`).
- 🔴 **소비 레포 조치**: 새 정렬을 쓰려면 `run_opensearch_resync --env <env>` 를 한 번 돌려야 한다(매핑 보강 + 값 채우기). dev 실행 결과 = `updated · 1,526건 · 오류 0`.
- `search_filters.applied_date_bounds(filters)` — 기간 필터가 **실제로 적용되는 날짜 문자열**(검색 절과 같은 계산). 백엔드가 「적용 조건」을 되돌릴 때 원문 대신 이것을 쓴다(리뷰 2026-09-09 — 원문을 되돌리면 공백·시각이 섞여 안 걸린 조건이 걸린 것처럼 보였다).
- `query_builder.build_word_should(query, *, operator)` — 검색어를 필드별 단어 절 묶음으로(순수). 두 검색 화면의 **단어 절 정본**. 종전 `build_bm25_body` 내부 로직을 그대로 뽑은 것이라 기존 호출의 결과는 바이트 동일. 공개 API 표에 `build_bm25_body`·`build_knn_body` 도 함께 등재(코드 변경 0 · 계약면 명시).
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
