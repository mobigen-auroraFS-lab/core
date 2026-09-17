# CHANGELOG — 공개 API 변경만 적습니다

이 파일은 **다른 레포(파이프라인·백엔드)가 import 하는 이름**(README 「공개 API」 표)의 변경만 기록합니다.
내부 구현 변경은 적지 않습니다 — 그 이력은 문서 레포 설계이력에 있습니다.

버전은 git 태그 `vMAJOR.MINOR.PATCH` 로 관리합니다(`pyproject.toml` 의 버전은 건드리지 않습니다).

| 올리는 자리 | 언제 |
|---|---|
| **MAJOR** | 공개 API 를 **깨는** 변경 — 이름·인자·반환 모양이 바뀌거나 사라짐. 소비 레포 PR 을 같은 날 함께 연다 |
| **MINOR** | 공개 API **추가** — 기존 호출은 그대로 동작 |
| **PATCH** | 공개 API 무변경 — 내부 수정·버그 수정 |

## [Unreleased] — 2026-09-17 (099 G7 커서 **조건 지문** · 개체 목록 **이름 우선 티어**)

> 🔴 **깨는 변경 둘**(MAJOR). 둘 다 **실측 결함을 막는 장치**이며, 소비 레포(백엔드)는 같은 브랜치에서
> 함께 바뀐다. 097/099 커서는 머지됐지만 **프론트가 아직 배선 전이라 실사용자가 없어**, 지금이 깰 수
> 있는 유일한 시점이다.

### 깨는 변경 (MAJOR)

- `search.cursor.encode_cursor(sort_name, sort_values, *, scope)` · `decode_cursor(token, *, expect_sort, expect_arity=None, expect_scope)` — **조건 지문**(`scope`)을 **필수 키워드**로 받는다.
  - 왜: 종전 커서는 정렬 이름만 담아 **정렬이 같고 조건만 다른** 요청을 막지 못했다. 실측(2026-09-17 · 실 DB·실 OS) — 개체 `q=사찰` 커서를 `q=석탑` 요청에 쓰자 **200** 으로 이어 주었고 1쪽의 석굴암·경주시가 통째로 빠졌다. 파일도 같다(전체 훑기 커서를 `modality=text` 에 쓰자 첫 건 누락). 계약 주석에 "조건이 바뀌면 커서를 버려라"라고 **적혀만** 있었고, 강제하지 않는 계약은 프론트가 한 번 실수하면 **오류 없이 자료가 사라진다**.
  - 무엇을 담나: 호출부가 모은 문자열의 **지문**(sha256 앞 16자리)만 담는다. 🔴 **조건 원문은 토큰에 싣지 않는다** — 실으면 내부 파라미터가 곧 계약이 되고(097 이 날것 `sort` 배열을 감싼 것과 같은 이유), 토큰이 조건 길이만큼 길어지며, 질의어가 주소창에 드러난다. 지문은 길이가 늘 같아(16자) 길이로도 정보가 새지 않는다.
  - 🔴 **지문이 없는 옛 토큰은 거부**한다(`CursorError`). "없으면 통과"로 두면 옛 토큰 하나로 검사 전체를 우회할 수 있어 장치가 없는 것과 같다.
  - 🔴 **무엇을 지문 재료로 넣을지는 호출부(화면)가 정한다** — 코어는 화면 파라미터를 알면 안 된다(093 경계). 코어는 받은 문자열을 담고 대조만 한다.
  - 전환: 호출부가 "이번 결과 집합을 정의하는 것 전부"(질의·좁히기·필터·임계 등)를 한 문자열로 모아 넘긴다. 정렬 이름은 넣지 않는다(`expect_sort` 가 이미 따로 대조한다). 쪽 크기(`limit`)도 넣지 않는다 — 집합을 바꾸지 않으므로 넣으면 멀쩡한 순회가 끊긴다.
- `search.file_search.browse_files(…, scope)` — 위와 같은 이유로 **필수 키워드**가 늘었다. 이 함수가 커서를 만들고(`encode`) 되읽는다(`decode`).
- `relations.graph_query.list_entities(…, after_tier=None, uid_first=None)` — 개체 목록 정렬이 **3단**이 됐다: `우선 티어 DESC → confirmed_count DESC → entity_uid ASC`. 이어읽기 책갈피도 **두 값 → 세 값**이다(옛 2값 토큰은 호출부 커서 검사에서 400).
  - 왜: 이름으로 찾아도 그 개체가 위에 오지 않았다(실측 — `숭례문` **7위** · `경포대` **15위**, 1~3위는 서울특별시·운문사·화엄사). 정렬이 구성 자산 수뿐이라 **큰 개체가 늘 위**로 왔다(spec 099 §3-2a 결정의 부작용). 전화번호부에서 이름을 정확히 아는 사람에게 두꺼운 항목부터 보여 준 셈이다.
  - 🔴 **`uid_first=None`·`set()` 이면 결과·순서가 종전과 완전히 같다**(전원 티어 0). ⚠️ `uid_allow` 와 달리 **빈 집합이 0건을 뜻하지 않는다** — 저쪽은 "무엇을 남길까"(거르기)이고 이쪽은 "무엇을 앞세울까"(순서)다.
  - 🔴 **누가 우선인지의 판정 규칙은 코어에 없다** — "이름이 정확히 같다"는 화면 정책이라 호출부가 집합으로 준다(093 경계). 백엔드는 코어 정본 `normalize_text_key` 로 눌러 `entity_uid` 와 견준다(새 규칙을 만들지 않았다).
  - 🔴 keyset 조건도 **3단**으로 확장했다 — `(티어 <) OR (티어 = AND (수 < OR (수 = AND 표기 >)))`. 한 단이라도 빠지면 동점 무더기(실측: 구성 자산 3건짜리 **291개**)나 티어 경계에서 행을 통째로 잃거나 중복한다. 둘 다 오류를 내지 않아 아무도 모른다.
  - 반환 행 모양은 **그대로다**(티어는 순서용 내부 컬럼이라 응답 키를 늘리지 않는다).

### 추가 (MINOR — 기존 호출 무변경)

- `relations.graph_query.count_entities(…, uid_first=None)` — **총계는 이 값에 영향받지 않는다**(줄을 어떻게 세우든 사람 수는 같다). 목록과 **같은 인자 묶음**을 그대로 넘겨 "조건이 어긋난 두 수"를 만들지 않게 하려고 받는다. 단위 테스트가 무영향을 봉인한다.

## [Unreleased] — 2026-09-17 (099 G4 개체 검색을 **집합 판정**으로 · 개체 화이트리스트 필터)

> 🔴 **2026-09-17 결정 뒤집기**: 이 블록의 초안은 집합 판정에서 **의미(kNN)를 빼고** 낱말 매칭만 쓰기로
> 했었다. 사용자 결정으로 **의미 검색을 되살렸다** — 낱말만 쓰면 `발효`→김치 같은 어휘 불일치를 통째로
> 잃는데, 그것이 바로 spec `090-entity-semantic-search` 가 통째로 풀려던 문제였기 때문이다. 아래
> `match_entity_keys` 항목은 **되살린 뒤의 계약**이다(미배포 블록이라 그 자리에서 고쳤다).
>
> 🔴 **2026-09-17 후속 보완**: `match_entity_keys` 의 반환을 **집합 하나 → `EntityMatchSet`**
> (집합 + 어느 갈래로 걸렸는지)으로 넓혔다. 같은 미배포 블록이라 여기서 그대로 고친다 —
> 이 이름을 쓰는 **배포된 소비처가 아직 없다**(백엔드 `service/portal/mm_meta.py` 한 곳이
> 같은 브랜치에서 함께 바뀐다). 판정 규칙·질의 수·결과 집합은 **하나도 바뀌지 않는다**.

### 추가 (MINOR — 기존 호출 무변경)
- `search.entity_search_os.match_entity_keys(client, index, *, query, query_vector, max_hits=10000, candidate_size=20, gate_eps=0.15) -> EntityMatchSet` — 질의에 **맞는 개체 전부**의 키 집합. 순위가 아니라 **집합**이며, 순서·쪽 나누기·카드 재료는 DB 목록(`list_entities`)이 맡는다. 🔴 **찾아오기(`q`)와 결과 내 재검색이 이 함수 하나를 쓴다**(spec 099 §3-2a) — 둘 다 「질의를 던져 매칭 개체 집합을 얻기」라서다. 호출부가 교집합(`A ∩ B`)으로 합친다.
  - 🔴 **집합 = ① BM25 낱말 매칭 전부 ∪ ② (kNN 게이트 통과 시) kNN 창 안 전부**(순수 합집합 · 순위 없음). 낱말만 쓰면 **글자가 없는 매칭**을 통째로 잃는다 — `발효`→김치 0건 · `도자기`→고려청자 0건. 089 이후 남은 검색 실패 30건이 그 어휘 불일치였다(spec `090-entity-semantic-search` 가 통째로 그것을 위한 것).
  - ② 는 **새 임계를 만들지 않는다** — 순위 경로가 쓰는 게이트(`gate_signal` + `passes_cutoff(eps=0.15, floor=0)`)를 같은 기본값으로 그대로 쓴다. 절대 코사인 하한은 두지 않는다(실측: 무의미 질의 1등 0.442 vs 유관 질의 1등 0.456 — 붙어 있어 절대값으로 못 가른다. 자산의 `SEMANTIC_MIN_COSINE_DEFAULT=0.60` 은 개체에 쓰면 전 구간이 잘린다).
  - 🔴 **② 의 kNN 창은 `candidate_size`(20) 고정** — `max_hits` 를 창으로 쓰지 않는다. 창을 키우면 게이트 배경(하위 절반 평균)이 내려가 게이트가 **반드시 더 관대해진다**(099 T020 단조성 증명). eps 0.15 의 보정 전제를 지키는 장치다.
  - 🔴 `query_vector` 는 **기본값 없는 필수 키워드**다(깜빡하면 `TypeError`). `None` 은 "의미 갈래를 끈다"는 명시적 선택이며, 그때도 경고 로그를 남긴다 — 의미 재현이 조용히 사라지지 않게.
  - ⚠️ **빈 질의는 `ValueError`** — 빈 집합(=0건)과 "묻지 않았다"(=필터 없음)를 같은 값으로 만들면 교집합에서 결과가 통째로 사라진다.
  - 로그로 드러나는 것 셋: 낱말 갈래가 상한(`max_hits`)에서 잘림 · 의미 갈래가 **게이트에 막힘**(top·baseline·격차·eps 동봉) · 의미 갈래가 꺼짐(벡터 미제공/후보 0건).
- `search.entity_search_os.EntityMatchSet(keys, text_keys, semantic_keys, semantic_gate_passed)` — 위 함수의 반환 모양. `keys` 는 **파생값**(= `text_keys | semantic_keys`)이라 결과 집합은 종전과 완전히 같고, 갈래 둘이 **화면의 「걸린 이유」 재료**다. 🔴 필요한 이유: 뜻(kNN)으로 걸린 결과는 **화면 어디에도 검색어가 보이지 않는다** — `왕실 무덤` 으로 찾으면 `영릉` 이 나오는데 그 카드에는 "왕실 무덤" 이라는 글자가 한 자도 없어, 근거가 없으면 사용자가 "검색이 고장났나"로 읽는다(089·090·092 가 만든 설명 가능성).
  - **추가 질의 0회** — 판정은 이미 두 갈래로 따로 계산되고 마지막에 합쳐질 뿐이라, 버리지 않고 함께 돌려주기만 한다(낱말 1회 + 의미 1회 · 종전과 같다).
  - `semantic_gate_passed=False` 이면 `semantic_keys` 는 빈 집합이다(게이트에 막혔거나 벡터를 주지 않았거나 후보가 0건 — 셋의 구분은 경고 로그가 말한다). `EntitySemanticMatch` 와 같은 결(NamedTuple · 키는 `frozenset`)이다.
- `search.entity_search_os.semantic_entity_keys(client, index, *, query_vector, candidate_size=20, gate_eps=0.15) -> EntitySemanticMatch` — ② 갈래 단독 진입점. 게이트 차단 사실을 **값으로** 읽는 경로다(로그만 두면 화면이 근거를 보일 수 없다).
  - `EntitySemanticMatch(keys, gate_passed, top, baseline, sample_size)` — `gate_passed=False` 이면 `keys` 는 빈 집합(일부만 버리지 않고 통째로 버린다 · 090 후속 게이트 계약). `sample_size=0` 은 "막혔다"가 아니라 "후보가 없었다"를 뜻한다.
- `search.entity_search_os.entity_match_clause(query) -> dict | None` — 위 판정의 순수 부품(엔진 없이 단위 검증 가능). **낱말끼리 AND · 한 낱말 안에서 필드끼리 OR**(091 §2-4 규율 = 파일 경로 `file_search.refine_clause` 와 같은 규칙). 빈 값이면 `None`. ⚠️ `multi_match`+`operator=and` 를 쓰지 않는다 — 그것은 **한 필드 안에** 모든 낱말이 있기를 요구해 `전통음식`(키워드) + `배추`(구성 자산 요약) 같은 흔한 경우가 통째로 탈락한다.
- 상수 `src.config.search_constants.ENTITY_MATCH_FIELDS_DEFAULT`(= `name`·`keywords`·`description`·`member` · 색인 텍스트 필드 전량) · `ENTITY_MATCH_MAX_HITS_DEFAULT`(= 10,000). 공개 API 표에는 올리지 않는다(소비 레포가 직접 쓰지 않음 · 근거는 상수 주석).
- `relations.graph_query.list_entities(…, uid_allow=None)` · `count_entities(…, uid_allow=None)` — 개체 **화이트리스트 필터**. 집합 판정 결과를 목록·총계에 얹는 자리다. 🔴 **`None` 과 빈 집합은 다른 값이다**: `None` = 필터 없음(**종전과 완전히 같은 결과**) · `set()` = **0건**(매칭 없음). 판정을 파이썬이 아니라 **SQL 에서** 가른다(배열이 `NULL` 이면 조건이 열리고, 빈 배열이면 `unnest` 가 0행이라 `EXISTS` 가 거짓). 섞으면 "검색했는데 전체가 나오는" 조용한 오류가 된다.
  - 조건은 CTE **안쪽** `WHERE` 에 건다(커서 조건은 바깥 — 099 G1 구조 유지). 개체 자연키가 (타입, 표기) 둘이라 두 배열을 나란히 풀어 **짝으로** 맞춘다(타입만 맞는 동명 개체 유입 차단).
  - 칩 집계(`count_entities_by_type`·`count_entities_by_area`)와 묶음 내려받기(`assets_of_entities`)는 **현행 유지** — 화이트리스트를 얹지 않는다(종류는 갈아타는 축이라 검색으로 좁히면 갈아탈 칩이 사라진다 · spec 087 2차 정정과 같은 사유). 검색을 칩에 반영할지는 화면 정책(G5).

### 변경 없음
- `search_entities_hybrid` 의 계약·기본값(`top_n=5` · `operator=or` · 게이트 `eps=0.15` · `candidate_size=20`)은 **그대로다** — 되돌림 경로로 남긴다(plan 099 §1-④). 단위 테스트가 이 불변을 함께 지킨다.

## [Unreleased] — 2026-09-17 (색인 분석기에 `lowercase` — 검색 대소문자 구분 해소)

### 변경 (동작 변경 — 🔴 **전량 재색인 필요**)
- `search.opensearch_sync.build_index_body` 의 `nori_user` 분석기 filter 에 **`lowercase`** 추가.
  종전에는 filter 가 `["nori_josa_pos","nori_josa_word"]` 뿐이라 라틴 문자 대소문자가 **다른 토큰**이었다 —
  실 OpenSearch `_analyze` 실측: `AI`→`AI` · `ai`→`ai` · `PDF문서`→`PDF`+`문서`.
  질의도 같은 분석기를 지나므로 **`pdf` 로 검색하면 `PDF` 가 든 자산이 나오지 않았다**(한글은 무관).
- 이 구멍은 **099 가 만든 것이 아니라 원래 있던 것**이다. 종전 파이썬 재검색이 `casefold` 로 대소문자를
  무시해 **재검색이 본검색보다 관대한** 비대칭이 있었고, 099 G3 이 재검색을 엔진으로 옮기며 드러났다.
- ⚠️ **분석기는 매핑처럼 덧붙여 고칠 수 없다.** 적용하려면 `run_opensearch_resync --recreate` 로
  전량 재색인해야 하며, 그 전까지 `ensure_index` 가 `'analysis-stale'` 을 돌려준다.
  실측 선례: 1,526건 재색인 17초(2026-09-09) → 현 16,865건 기준 수 분.

## [Unreleased] — 2026-09-17 (099 G3 파일 결과 내 재검색을 **검색 엔진 질의 절**로)

### 추가 (MINOR — 기존 호출 무변경)
- `search.file_search.refine_clause(refine) -> dict | None` · `REFINE_FIELDS`(= `file_name`·`summary`·`keywords`) — 결과 내 재검색어를 **집합을 좁히는 AND 절**로 만드는 순수 부품. 낱말끼리 AND, 한 낱말 안에서 필드끼리 OR(091 §2-2·§2-4 규율 그대로). 빈 값이면 `None`(좁히지 않음 = 되돌림).
- `search_files(…, refine=None)` · `browse_files(…, refine=None)` · `build_rank_body` · `build_browse_body` · `build_facet_body` · `build_facet_plan` 에 같은 인자 추가. 🔴 **랭킹·커서 두 경로가 같은 부품을 쓴다**(plan 099 §1-⑤) — 따로 만들면 한쪽만 고쳐져 경로에 따라 결과가 갈린다. 빈 값이면 질의 본문이 **종전과 바이트 동일**하다.
- `search_files`·`browse_files` 응답에 **`scope_total`** 추가 — 좁히기 **이전** 결과 집합 크기(화면의 "지우면 N건"). `total` 은 좁히기 **이후** 모수이며 둘 다 **엔진이 센 모수**다. refine 을 준 요청에서만 질의가 하나 늘고(묶음이라 왕복은 그대로), refine 이 없으면 `scope_total == total`.
- 공개 API 표에 `browse_files` · `build_browse_body` · `browse_scope_clause` · `STABLE_SORTS` 등재 — 097 이 표·목록·CHANGELOG 셋 다 빠뜨려 백엔드(`routes_file_search.py`)가 **계약 밖 이름**을 import 하고 있었다(코드리뷰 2026-09-16 §11 · G1 이월분). 코드 변경 0 · 계약면 명시.

### 변경 (동작 변경 — 소비 레포 확인 필요)
- 🔴 **파일 refine 의 매칭이 「부분 문자열」에서 「낱말(형태소)」로 바뀐다**(spec 099 §3-4 · 2026-09-17 사용자 결정). 색인 텍스트 필드가 `nori_user` 분석기라 질의 절로 옮기면 분석기가 같이 걸린다. `치찌` 로 `김치찌개` 가 걸리던 **엉뚱한 매칭이 사라지고**, `김치를` 처럼 조사가 붙어도 걸린다. 대체로 더 정확하지만 **결과가 달라진다** — 091 의 D1(위양성 0)·D2(다어절)는 새 방식으로 재측정 대상이다(SC-008).
- 🔴 refine 이 **결과 집합 전체**에 걸린다. 종전에는 받아 온 한 페이지 안에서만 좁혀 2쪽 이후 자산은 구조적으로 닿지 못했다(091 §2-3 의 의도된 한계 · 099 가 그 전제를 개정). 커서 순회 중 refine 이 바뀌면 집합이 바뀌므로 화면은 **커서를 버리고 처음부터** 받아야 한다.
- `browse_files` 가 커서를 풀 때 정렬값 **개수**까지 대조한다(`decode_cursor(…, expect_arity=len(SORT_OPTIONS[sort]))`). 종전에는 개수가 틀린 위조·구버전 토큰이 `search_after` 로 흘러 엔진 400 → **HTTP 500** 이 됐다. 이제 `CursorError` 라 호출부가 400 으로 바꾼다(099 G1 이월 · 코드리뷰 2026-09-16).

## [Unreleased] — 2026-09-17 (099 G1 개체 커서 순회 · 커서 토큰 개수·타입 검사)

### 추가 (MINOR — 기존 호출 무변경)
- `relations.graph_query.list_entities(…, after_count=None, after_uid=None)` — 개체 목록의 **이어읽기 책갈피**(무한 스크롤). 직전 쪽 마지막 개체의 `(confirmed_count, entity_uid)` 를 주면 그 다음부터 잇는다. 둘 다 생략하면 첫 쪽이고 **종전과 같은 결과**다. 한쪽만 주면 `ValueError`(반쪽 커서를 조용히 무시하면 첫 쪽을 다시 읽어 중복이 난다).
  - `confirmed_count` 는 `HAVING COUNT(DISTINCT …)` **집계**라 `WHERE` 에서 비교할 수 없어, 본문을 CTE(`ent`)로 한 겹 감싸고 바깥에서 조건을 건다. 정렬이 `수 내림차순 + 표기 키 오름차순` 으로 섞여 있어 튜플 비교 대신 `수 < 기준 OR (수 = 기준 AND 표기 키 > 기준)` 로 쓴다 — **동점 무더기를 가르는 조건이 핵심이다**(dev 실측 최대 동점 그룹 291개 · `<` 만 쓰면 통째로 잃고 `<=` 면 통째로 중복).
  - dev 실측: 822건 완주 · 중복 0 · 누락 0 · 쪽 크기(50·37)를 바꿔도 한 번에 받은 목록과 **완전 일치** · 2회 순회 순서 동일(헌법 3조).
- `relations.graph_query.count_entities(conn, *, entity_type=None, area_names=None, min_bundle_size, statuses=None) -> int` — 지금 조건으로 **노출 개체가 모두 몇 개인지**(모수). 화면의 "N건 중 M건"에서 N 을 만든다. 목록은 한 쪽만 돌려주므로 돌려준 개수를 세면 쪽 크기가 나온다 — 200개만 받아 놓고 "200건"이라 적던 것이 그 오해였다(dev 실측 실제 노출 대상 **822개**). 노출 개체의 정의·필터는 기존 집계 함수들과 **같은 조각**을 쓰고, 카드 재료(배열 집계)는 조립하지 않는다.
- 공개 API 표에 `src.search.cursor` 등재 — `encode_cursor` · `decode_cursor` · `CursorError`. 097 에서 표·목록·CHANGELOG 셋 다 빠져 백엔드(`routes_file_search.py`)가 **계약 밖 이름**을 import 하고 있었다(코드리뷰 2026-09-16 §11). 코드 변경 0 · 계약면 명시.

### 변경 (MINOR — 인자 추가 · 잘못된 입력의 응답만 바뀜)
- `search.cursor.decode_cursor(token, *, expect_sort, expect_arity=None)` — 정렬값 **개수** 검사를 붙였다. `expect_arity` 를 주면 개수가 다른 토큰을 `CursorError` 로 끊는다. 생략하면 개수를 따지지 않아 **기존 호출은 그대로**다.
- 🔴 `decode_cursor` 가 정렬값 **원소 타입**(스칼라: 문자열·수·`None`)을 **항상** 검사한다. 종전에는 위조·구버전 토큰의 객체 원소가 그대로 통과해 `search_after` 로 흘렀고, 검색 엔진이 400 을 내면 그 예외는 `CursorError` 가 아니라서 호출부가 400 으로 바꾸지 못하고 **HTTP 500** 이 됐다(코드리뷰 2026-09-16). 이제 `CursorError` 로 끊긴다.
  - ⚠️ **호출부 조치**: 새로 커서를 쓰는 곳은 `expect_arity=<정렬 키 수>` 를 주는 것을 권한다. 기존 파일 검색 경로(`file_search.browse_files`)는 이번 변경에서 **배선하지 않았다**(하위호환 유지).

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
