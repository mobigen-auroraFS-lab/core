# dataplatform-core

멀티모달(텍스트·이미지·영상·오디오) 데이터 통합 플랫폼의 공유 라이브러리입니다.

## 이 레포지토리는 무엇인가

플랫폼은 저장소 세 개로 나뉘어 있습니다. 이 레포는 그중 **세 저장소가 함께 쓰는 코드와
데이터베이스 스키마**를 담당합니다. 검색 순위를 매기는 방식이나 자산의 상태값처럼 셋이
같은 답을 내야 하는 것들을 여기 한 곳에 둡니다.

| 저장소 | 하는 일 |
|---|---|
| **core** (이 레포) | 공통 코드 · 데이터베이스 스키마 |
| [pipeline](https://github.com/mobigen-auroraFS-lab/pipeline) | 파일 수집 · 분류 · 메타데이터 추출 · 색인 · 자산 간 관계 생성 |
| [service](https://github.com/mobigen-auroraFS-lab/service) | 웹 화면이 사용하는 HTTP API |

**이 레포에는 실행 파일이 없습니다.** 라이브러리이므로 다른 두 레포가 설치해서 함수를
불러 쓰는 형태입니다. 직접 실행하는 것은 테스트와 데이터베이스 마이그레이션뿐입니다.

패키지 이름은 `meta-extract`, 코드에서 불러 쓸 때의 이름은 `src` 입니다.

## 디렉터리 구조

```
src/
  config/          # 설정 읽기, 환경변수 검증, 임베딩 차원 같은 상수
  database/        # PostgreSQL 연결·트랜잭션, 자산 ID 생성, 처리 이력 기록
  domain/          # 자산 상태값 어휘. DB 제약조건과 짝을 이룹니다
  embedders/       # 텍스트·이미지·영상을 벡터로 바꾸는 코드
  file/            # 파일 종류 판별, 해시 계산, 경로 규칙
  llm/             # LLM 호출 창구와 요약 생성. 모든 LLM 호출이 여기를 지납니다
  mm_classify/     # 사용자가 정의한 분류 기준으로 자산을 판정
  mm_meta/         # 여러 자산에 공통으로 등장하는 개체를 묶는 로직
  registry/        # 접근 등급, 추가 메타데이터 필드 관리
  relations/       # 자산 사이의 관계를 찾고 저장. 관계 조회도 여기를 거칩니다
  search/          # 검색 질의 조립, 키워드·벡터 결과 합치기, 색인 정의
  topic/           # 자산에 붙은 주제 조회

migrations/        # 데이터베이스 스키마 변경 이력 (alembic)
scripts/           # 시드 데이터 적재, 코드 검사 도구
tests/             # 단위 테스트
constraints.txt    # 의존성 버전 고정 파일
```

`llm/` 과 `relations/` 는 우회할 수 없게 만들어 두었습니다. LLM 호출은 전부
`llm/client.py` 를 지나야 하고, 관계 조회는 `relations/graph_query.py` 를 거쳐야 합니다.
관계는 양방향으로 저장되기 때문에 한쪽 방향만 조회하면 결과의 절반이 빠집니다.

## 사용 환경

### 하드웨어

이 레포는 라이브러리라 자체 서버가 없습니다. 아래는 코어를 설치해 쓰는 장비 기준이며,
실제 운영 사양은 pipeline 레포 README 를 참고하십시오. 그쪽이 가장 무겁습니다.

| 구분 | 최소 | 권장 |
|---|---|---|
| CPU | 2 코어 | 4 코어 |
| 메모리 | 4 GB | 8 GB |
| GPU | 불필요 | 불필요 |

PyTorch 는 CPU 버전으로 동작합니다. 임베딩과 LLM 은 별도 서버에 요청하는 구성이 기본이라
이 장비에는 GPU 가 필요 없습니다.

디스크는 데이터 양에 비례합니다. 아래는 자산 20,505건(원본 33GB)을 적재한 뒤 잰 값입니다.

| 항목 | 자산 1건당 | 1만 건 | 10만 건 |
|---|---|---|---|
| 원본 파일 | 1.6 MB | 16 GB | 160 GB |
| PostgreSQL | 66 KB | 0.7 GB | 6.6 GB |
| OpenSearch 색인 | 6.8 KB | 70 MB | 0.7 GB |

원본 파일 크기는 데이터 종류에 따라 크게 달라집니다. 영상이 많으면 몇 배가 됩니다.

### 소프트웨어

| 항목 | 요구 버전 | 개발 확인 |
|---|---|---|
| Python | 3.13 이상 | 3.13.13 |
| PostgreSQL | 17 + pgvector 확장 | 17.9 · pgvector 0.8.2 |
| OpenSearch | 3.x | 3.6.0 |
| PyTorch | — | 2.11.0 |
| opensearch-py | — | 3.2.0 |
| psycopg | — | 3.3.3 |
| sentence-transformers | — | 5.4.1 |
| alembic | — | 1.18.4 |

OpenSearch 에는 플러그인 세 개가 필요합니다.

| 플러그인 | 용도 |
|---|---|
| `analysis-nori` | 한국어 형태소 분석 |
| `opensearch-knn` | 벡터 검색 |
| `opensearch-neural-search` | 키워드 검색과 벡터 검색 결과 합치기 |

외부 서비스로 LLM 서버와 임베딩 서버가 필요합니다. 둘 다 OpenAI 호환 방식으로 호출합니다.
설정 이름에 `OPENAI_` 가 들어가지만 외부 OpenAI 서비스가 아니라 **직접 운영하는 서버**를
가리킵니다.

## 설치 방법

가져다 쓸 때는 태그를 지정해 설치합니다.

```bash
pip install "meta-extract @ git+https://github.com/mobigen-auroraFS-lab/core.git@v0.7.0"
```

데이터베이스 마이그레이션까지 돌리려면 `[migrate]` 를 붙입니다.

```bash
pip install "meta-extract[migrate] @ git+https://github.com/mobigen-auroraFS-lab/core.git@v0.7.0"
```

이 레포를 직접 고칠 때는 내려받아 설치합니다.

```bash
git clone https://github.com/mobigen-auroraFS-lab/core.git
cd core
pip install -e ".[migrate]" -c constraints.txt
```

`constraints.txt` 는 의존성 버전을 정확한 값으로 고정한 파일입니다. 같은 환경을 다시
만들어야 하는 경우(자동 빌드, 서버 배포)에 붙입니다.

## 실행 및 운영 방법

### 설정

`.env.dev` 또는 `.env.prod` 파일에 설정을 적거나 환경변수로 넘깁니다. 필수 항목이 빠지면
시작할 때 `필수 환경변수 누락: <이름>` 으로 멈춥니다.

```dotenv
META_MODEL=                 # LLM 모델 이름
OPENAI_BASE_URL=            # LLM 서버 주소
OPENAI_API_KEY=             # LLM 서버 인증 키
TEXT_EMBED_MODEL=           # 텍스트 임베딩 모델 이름
TEXT_EMBED_CHUNK_SIZE=512
TEXT_EMBED_NORMALIZE=true
ENCODING=utf-8              # 아래 5개는 적재 과정에서만 씁니다
CHUNK_SIZE=1000
OVERLAP_SIZE=100
SUMMARY_MAX_CHARS=500
TOP_K_KEYWORDS=10
```

service 레포는 파일을 적재하지 않으므로 아래 5개를 요구하지 않습니다.

이 밖에 데이터베이스 접속 정보(`POSTGRES_HOST`·`POSTGRES_PORT`·`POSTGRES_DB`·
`POSTGRES_USER`·`POSTGRES_PASSWORD`), 검색 엔진 주소(`OPENSEARCH_URL`), 임베딩 서버
설정(`EMBED_API_BASE_URL`·`EMBED_API_MODEL`·`EMBED_API_KEY`)이 필요합니다.

### 데이터베이스 준비

처음 한 번, 그리고 스키마가 바뀔 때마다 실행합니다.

```bash
alembic -c alembic.ini upgrade head
alembic -c alembic.ini current                            # 현재 적용된 버전 확인
python -m scripts.seed_topic_registry --env dev --apply   # 주제 분류 기초 데이터
```

마지막 줄을 빠뜨리면 자산 간 관계가 하나도 만들어지지 않습니다. 오류는 나지 않습니다.

### 검색 색인

색인의 형태(어떤 필드를 어떻게 분석할지)는 이 레포가 정의합니다. 실제로 색인을 만드는 것은
pipeline 레포의 재색인 도구이고, 그 결과를 아래 값 중 하나로 알려줍니다.

| 결과 | 뜻 | 할 일 |
|---|---|---|
| `created` | 새로 만들었습니다 | — |
| `recreated` | 지우고 다시 만들었습니다 | — |
| `updated` | 빠진 필드를 추가했습니다 | — |
| `exists` | 이미 올바른 상태입니다 | — |
| `mapping-stale` | 필드 형식이 코드와 다릅니다 | `--recreate` 로 다시 만드십시오 |
| `analysis-stale` | 분석기 설정이 코드와 다릅니다 | `--recreate` 로 다시 만드십시오 |

`mapping-stale` 은 벡터 검색이 동작하지 않는 상태입니다. 색인이 없는데 문서가 먼저
들어가면 검색 엔진이 색인을 임의로 만드는데, 이때 1536개짜리 벡터를 일반 숫자 배열로
잘못 인식합니다.

### 테스트와 코드 검사

```bash
python -m unittest discover -s tests   # 데이터베이스가 필요한 테스트는 자동으로 건너뜁니다
python scripts/policy_gate.py          # 코드 규칙 검사
python scripts/test_integrity.py       # 테스트를 약화시킨 변경 검사
python scripts/args_gate.py            # 함수 인자 설명 누락 검사
ruff check src tests
```

### 배포

공개 API 가 바뀌면 `CHANGELOG.md` 에 적고 새 태그를 붙입니다. pipeline 과 service 는 코어
태그가 올라간 뒤에 맞춰 올립니다.

## 실행 예제

```bash
$ alembic -c alembic.ini current
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
v306_entity_embedding (head)

$ python -m scripts.seed_topic_registry --env dev
[dry-run] 주제 N건 · 하위주제 M건 적재 예정 — 실행하려면 --apply

$ python -m unittest discover -s tests
Ran 2521 tests in 0.7s
OK (skipped=29)
```

라이브러리로 쓰는 예입니다. service 레포가 파일 검색에서 이렇게 호출합니다.

```python
from src.config.bootstrap import bootstrap_env
from src.search.opensearch_sync import get_client
from src.search.file_search import search_files
from src.search.search_filters import parse_search_filters

bootstrap_env("dev", repo_root=".", role="serving")
client = get_client()

result = search_files(
    client, "assets",
    query="김치",
    query_vector=embed("김치"),
    filters=parse_search_filters(topic=["음식"], modality=["video"]),
    sort="relevance", from_=0, size=20,
)

print(result["total"])
print([f["value"] for f in result["facets"]["topic"]])
```

검색어를 벡터로 바꾸는 일(`embed`)은 호출하는 쪽에서 합니다. 이때 **문서를 색인할 때 쓴
것과 같은 모델**이어야 합니다. 다르면 오류 없이 엉뚱한 결과가 나옵니다.

## 공개 API — 파이프라인·백엔드가 쓰는 계약면

이 레포는 라이브러리라 **다른 레포가 import 하는 이름이 곧 계약**입니다. 아래 표에 있는 이름만 밖에서
쓰십시오. 표에 없는 것, 특히 **밑줄(`_`)로 시작하는 이름은 내부 구현**이라 예고 없이 바뀝니다.
표와 실제 코드가 어긋나면 `tests/test_public_api.py` 가 실패합니다(이름이 import 되는지 · 밑줄이 없는지 ·
이 표에 적혀 있는지). 계약을 깨는 변경은 `CHANGELOG.md` 에 적고 태그의 MAJOR 를 올립니다.

| 영역 | 모듈 | 이름 |
|---|---|---|
| 설정 | `src.config.settings` | `init_settings` · `get_current_settings` · `PipelineSettings` · `active_embed_channel` |
| 설정 | `src.config.bootstrap` | `bootstrap_env` |
| 상수 | `src.config.embedding_constants` | `FIX_EMBEDDING_DIMENSION` · `EMBEDDING_KIND_ST` · `EMBEDDING_KIND_CLIP` · `DEFAULT_CLIP_MODEL_NAME` |
| 상수 | `src.config.search_constants` | `TAG_FACET_TOP_N_DEFAULT` · `TAG_FACET_MIN_COUNT_DEFAULT` · `ENTITY_INDEX_DEFAULT` |
| 파일명 | `src.config.filename_util` | `basename_of` · `strip_asset_id_prefix` · `display_file_name` |
| 검색 모달리티 | `src.config.search_modalities` | `VALID_SEARCH_MODALITIES` · `parse_modalities_csv` · `MODALITY_TO_BUCKET` · `BUCKET_TO_MODALITY` |
| DB | `src.database.postgres_util` | `PostgresUtil` |
| DB | `src.database.ids` | `uuid7` |
| DB | `src.database.lineage_persist` | `record_lineage` |
| DB | `src.database.lineage_activity` | `LineageActivity` |
| 어휘 | `src.domain.status_vocab` | `AssetStatus` · `AccessTier` · `GraphEdgeStatus` · `RelationResolutionStatus` · `RegistryFieldStatus` · `MmSkillStatus` · `RelationKindStatus` |
| 정규화 | `src.domain.text_norm` | `normalize_text_key` |
| 수치 | `src.domain.numeric` | `safe_float` |
| 권한 | `src.registry.access_tier` | `project_ext_meta` · `principal_clearance` |
| 권한 | `src.registry.ext_meta_field_registry` | `fetch_access_tiers` · `validate_ext_meta` |
| 그래프 읽기 | `src.relations.graph_query` | `fetch_relations_for_asset` · `fetch_active_relations_for_asset` · `mm_meta_of_asset` · `mm_meta_bundle` · `list_entities` · `count_entities` · `count_entities_by_type` · `count_entities_by_area` · `assets_of_entities` |
| 관계 정책 | `src.relations.approval_policy` | `TIER_ORDER` · `tier_rank` |
| 관계 검토 | `src.relations.review` | `list_edges_for_review` · `list_relation_kinds` · `bulk_review` · `revise_edge` · `promote_relation_kind` |
| 검색 | `src.search.search_service` | `search_hybrid` |
| 검색 | `src.search.search_filters` | `SearchFilters` · `parse_search_filters` · `applied_date_bounds` |
| 검색 | `src.search.search_tuning` | `SearchTuning` |
| 검색 | `src.search.refine` | `refine_rows` · `refine_tokens` |
| 검색 | `src.search.facets` | `aggregate_facets` |
| 파일 검색 | `src.search.file_search` | `search_files` · `build_rank_body` · `build_facet_body` · `build_facet_plan` · `build_semantic_body` · `ABOUT_BRANCH_DEFAULT` · `FACET_FIELDS` · `FACET_SELF_FILTERS` · `RANK_DEPTH_DEFAULT` · `TOTAL_CAP_DEFAULT` · `FACET_SIZE_DEFAULT` · `SEARCH_PIPELINE_DEFAULT` · `WORD_OPERATOR_DEFAULT` · `SEMANTIC_MIN_COSINE_DEFAULT` · `SEMANTIC_CAP_DEFAULT` · `SORT_OPTIONS` · `SORT_DEFAULT` · `SORT_DEPTH_DEFAULT` · `browse_files` · `build_browse_body` · `browse_scope_clause` · `STABLE_SORTS` · `refine_clause` · `REFINE_FIELDS` |
| 검색 커서 | `src.search.cursor` | `encode_cursor` · `decode_cursor` · `CursorError` |
| 검색 | `src.search.tag_facets` | `aggregate_tag_facets` · `normalize_tag_key` |
| 검색 | `src.search.query_embed` | `embed_query_for_media_search` |
| 검색 | `src.search.query_builder` | `build_word_should` · `build_bm25_body` · `build_knn_body` |
| 검색·색인 | `src.search.opensearch_sync` | `get_client` · `ensure_index` · `sync_all` · `index_asset` · `asset_to_doc` · `build_index_body` · `resolve_channel` · `check_pgvector_version` · `update_asset_mm_skill_labels` · `mm_skill_label_keys` |
| 개체 검색 | `src.search.entity_search_os` | `search_entities_hybrid` · `match_entity_keys` · `entity_match_clause` · `semantic_entity_keys` · `EntitySemanticMatch` · `EntityMatchSet` |
| 개체 검색 | `src.mm_meta.entity_search` | `split_query` · `match_entity_reason` · `narrow_entities` · `fuse_entity_results` · `gate_semantic_hits` · `entity_refine_fields` · `REASON_CODE_NAME` · `REASON_CODE_KEYWORD` · `REASON_CODE_DESCRIPTION` · `REASON_KEYWORD` · `REASON_DESCRIPTION` · `REASON_SEMANTIC` · `REASON_TEXT_MATCH` |
| 개체 검색 | `src.mm_meta.entity_embedding` | `find_similar_entities` |
| 주제 | `src.topic.asset_topic_query` | `fetch_asset_topic` · `find_same_topic_groups` · `list_topics` · `assets_in_topic` · `assets_unclassified` |
| 멀티모달 메타 | `src.mm_meta.rules` | `MIN_BUNDLE_SIZE` |
| 멀티모달 메타 | `src.mm_meta.persist` | `fetch_meta_type_vocab` |
| 분류 스킬 | `src.mm_classify.persist` | `fetch_active_skills` |
| 분류 스킬 | `src.mm_classify.read` | `label_names_of_assets` · `fetch_active_skills` |
| LLM | `src.llm.client` | `get_llm_client` · `complete_text` · `complete_json` · `complete_vision_json` |

> 🔴 **커서(책갈피)는 2026-09-17 에 깨는 변경이 있었습니다**(099 G7). `encode_cursor`·`decode_cursor`·
> `browse_files` 가 **조건 지문**(`scope`/`expect_scope`)을 **필수**로 받습니다 — 호출부가 "이번 조회를
> 정의하는 것 전부"를 문자열 하나로 모아 주면 커서가 그 지문을 담고 다음 쪽에서 대조합니다. 지문이
> 없거나 다른 토큰은 `CursorError` 입니다(예전 토큰 포함). 이유·전환 방법은 `CHANGELOG.md` 참조.

> ⚠️ `search_hybrid` 는 기존 멀티모달 검색 화면(종류별 그룹 응답)용이며 **새 화면은 `file_search.search_files` 를 쓴다**(대체 창구 — 같은 단어 절·점수식 · 개수·칩·페이징·정렬 · 12배 빠름). 남기는 이유는 모듈 docstring 에 있다. 표 셀은 이름만 적는다 — 위 표는 테스트(`tests/test_public_api.py`)가 그대로 읽는다.

> 표에 없는 이름이 필요하면 코어에 **함수 이름 · 도메인 용어 인자 · 반환 레코드** 로 요청하십시오.
> 응답 페이징 모양·화면 문구·페이지 번호는 코어가 받지 않습니다(그 부분은 호출하는 레포의 몫입니다).

## 자주 겪는 문제

| 증상 | 원인 |
|---|---|
| `필수 환경변수 누락: META_MODEL` | `.env` 파일을 읽지 못했습니다 |
| 관계가 하나도 안 생김 | 주제 분류 기초 데이터를 넣지 않았습니다 |
| 검색 결과가 비어 있음 | 색인이 없거나 `analysis-nori` 플러그인이 설치되지 않았습니다 |
| 벡터 검색만 안 됨 | 색인이 잘못 만들어졌습니다. `--recreate` 로 다시 만드십시오 |

## 제3자 오픈소스

전체 목록과 라이선스 전문은 `NOTICE` 파일에 있습니다.

| 구성요소 | 라이선스 |
|---|---|
| PyTorch | BSD 3-Clause |
| OpenSearch 클라이언트 · sentence-transformers · Transformers | Apache License 2.0 |
| Alembic | MIT |
| psycopg | LGPL 3.0 |

psycopg 는 LGPL 입니다. 파이썬에서 불러 쓰는 것은 이 소프트웨어의 라이선스에 영향을 주지
않지만, 사용 사실을 `NOTICE` 에 밝혀야 합니다. psycopg 자체를 고쳐서 배포할 때는 그
수정본을 같은 라이선스로 공개해야 합니다.
