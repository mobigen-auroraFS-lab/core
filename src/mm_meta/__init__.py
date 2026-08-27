"""084 **멀티모달 메타**(mm_meta) — 개체 중심 크로스모달 묶음의 순수 로직 패키지.

무엇을 하는 패키지인가: 적재 때 이미 만들어진 **요약과 키워드**만 읽어, 그 자산이 어떤 개체
(인물·장소·조직·작품·사건)를 말하고 있는지 판정하고, 결정 규칙으로 정리한다. "이순신을 찾으면
이순신의 이미지·텍스트·오디오·영상이 한 묶음으로" — 수집 선언도 별칭 사전도 없이(084 spec §목적).

이름에 대해: 물리 계층은 그대로 ``node_kind='entity'``·``entity_*`` 컬럼이고(마이그레이션 0),
도메인·화면 이름만 "멀티모달 메타"다. LLM 판정의 개념어는 여전히 **개체 추출**이다 — 추출되는
것은 개체이고, 저장·노출되는 레코드가 메타다(ADR `2026-08-24-multimodal-meta-model.md`).

네 층으로 갈라 둔 것이 이 패키지의 핵심이다:

    1. ``judge`` — **LLM 판정**(요약 앞 250자 + 키워드 → 키워드별 개체·타입). 단일 seam 경유,
       temperature=0, ``client=`` 주입. 성공/실패를 **명시 구분**해 돌려준다(실패를 "개체 0"으로
       뭉개면 일시 장애가 "판정 완료"로 영구히 굳는다 · spec §2).
    2. ``rules`` — **결정 규칙**(광역 제외 → 지정·자격 스톱패턴 → 행정 접미 병합). 순수 함수·
       코드 상수. LLM 이 경계를 일관되게 못 가른 부분만 이쪽으로 이관했다(검증 §6-③ 실측).
    3. ``persist`` — **영속**(``mm_member`` 카탈로그 행·메타 노드·소속 엣지·판정 이력). 마이그레이션
       0(기존 ``node``·``graph_edge``·``asset_lineage``). 재판정은 **자산 스코프 교체**이고
       ``reason`` 에 ``kw=…|pv=…|rv=…`` 스탬프를 남긴다(spec §4·§6). **수동 선등록**
       (``register_mm_meta`` + 별칭 색인·치환)도 이 층이다 — 발굴 모드 ``propose`` 에서는 등록된
       메타만 채워지므로, 등록이 곧 후보 승인이다(spec §6-1 · CLI ``scripts/register_mm_meta.py``).
    4. ``describe`` — **메타 설명**(구성 자산 요약 → "무엇이 들어 있는지" 한 문장). 카드가 집계값만
       보여 주면 성격 파악에 한 박자가 더 걸린다는 사용자 지적에서 나왔다(spec §8-1). 🔴 재료는
       **구성 자산 요약뿐**이고 프롬프트가 **외부 지식 금지**를 못 박는다 — 백과사전 지식으로 쓰면
       코퍼스에 없는 사실이 카드에 실려 검증이 불가능해진다. 성공/실패 구분은 ``judge`` 와 같은
       규율이며, 저장은 ``persist`` 가 ``node.canonical`` 에 병합한다(마이그레이션 0).

**타입 어휘의 정본은 DB 등록 행**이다(F05 · 2026-08-25 · spec §10). 타입 5종은 이름뿐이던 코드 상수
에서 **정의문("이 뜻으로만 판정한다" + "아닌 것")을 가진 등록 행**(``mm_skill`` ·
``mm_meta_type_vocab``·``vocab_code='default'``)으로 옮겼다 — 이름만 주면 경계를 LLM 상식이 정해 같은 개체가 자산마다
다른 타입으로 갈렸고, 정의문을 붙이자 그 흔들림이 거의 전부 통일됐다(수치는 spec §10). 배치는
``fetch_meta_type_vocab(conn)`` 으로 **시작에 한 번** 읽어 ``judge_asset_entities(type_defs=)`` 로
넘긴다. 행이 없으면 코드 프리셋(``ENTITY_TYPE_DEFS``)으로 폴백한다. 🔴 저장소는 085 것을 빌려 쓰되
**판정 엔진은 공유하지 않는다** — 085 는 자산을, 이 어휘는 개체를 나눈다(등록 CLI
``scripts/register_mm_meta_types.py``).

왜 갈랐나: 규칙은 같은 입력에 언제나 같은 결과를 내야 하고(헌법 3조), 판정은 확률적이다. 섞어
두면 "왜 이 자산이 이 묶음에 있나"를 되짚을 때 원인 층을 가릴 수 없다. 영속되는 ``reason`` 스탬프도
``pv=``(프롬프트 판)와 ``rv=``(규칙 판)를 따로 남긴다. 설명 문안 판(``DESC_PROMPT_VERSION``)도 판정
문안 판과 **따로** 늙는다 — 한쪽을 고칠 때 다른 쪽이 전량 재생성되면 안 된다.

의존성: ``judge``·``rules``·``describe`` 는 표준 라이브러리 + ``src.domain.text_norm`` 만 쓴다. LLM
클라이언트(``openai``)는 두 판정 모듈이 **호출 시점에** import 하므로 이 패키지를 불러오는 것만으로
무거운 의존성이 딸려오지 않는다(``src.relations``·``src.mm_classify`` 지연 로드 관례와 같다).
``persist`` 는 psycopg(코어 런타임 필수 의존)만 쓰며 커넥션은 호출부가 준다 — 풀을 열지 않는다.

설계 배경: `specs/084-entity-bundle`(spec §2 판정 계약 · §3 결정 규칙 · §4 영속 계약 ·
§8-1 메타 설명) · 검증 `docs/개체묶음_사전검증_20260820.md`
"""

from __future__ import annotations

from src.mm_meta.entity_label import (
    DEFAULT_MEMBER_SUMMARIES,
    EntityLabelError,
    build_entity_material,
    fetch_label_targets,
    replace_entity_labels,
)
from src.mm_meta.describe import (
    DESC_PROMPT_VERSION,
    DESCRIPTION_KEY,
    DESCRIPTION_MAX_CHARS,
    DESCRIPTION_TARGET_CHARS,
    MEMBER_SUMMARY_MAX_CHARS,
    DescribeFailure,
    MetaDescription,
    build_description_prompt,
    describe_meta,
    interpret_description,
)
from src.mm_meta.judge import (
    JUDGEMENT_KEY,
    PROMPT_VERSION,
    PROMPT_VERSION_WITHOUT_TYPE_DEFS,
    SUMMARY_MAX_CHARS,
    EntityJudgement,
    JudgeFailure,
    build_entity_prompt,
    interpret_response,
    judge_asset_entities,
    prompt_version_for,
)
from src.mm_meta.persist import (
    DESC_REGEN_MIN_DELTA_RATIO,
    DESC_TARGET_REASONS,
    LINEAGE_ACTIVITY,
    LINEAGE_AGENT,
    MM_MEMBER_KIND_CODE,
    MM_META_SOURCE_AUTO,
    MM_META_SOURCE_USER,
    MM_META_VISIBLE_STATUSES,
    MmMetaPersistError,
    ensure_entity_node,
    ensure_mm_member_kind,
    fetch_meta_description_targets,
    fetch_meta_members,
    fetch_meta_type_vocab,
    fetch_official_name_index,
    fetch_registered_alias_index,
    format_member_reason,
    list_mm_meta,
    normalize_registration,
    parse_member_reason,
    register_mm_meta,
    resolve_registered_aliases,
    upsert_entity_edges,
    upsert_meta_description,
)
from src.mm_meta.rules import (
    ENTITY_TYPE_DEFS,
    ENTITY_TYPE_ORDER,
    ENTITY_TYPES,
    EXCLUDED_ENTITIES,
    MIN_BASE_LENGTH,
    RULE_VERSION,
    STOP_PATTERNS,
    SUFFIX_MERGE_TYPES,
    SUFFIXES,
    EntityTypeDef,
    ExtractedEntity,
    apply_rules,
    type_names,
    build_official_name_index,
    is_excluded_entity,
    is_stopped_keyword,
)

__all__ = [
    "DEFAULT_MEMBER_SUMMARIES",
    "DESCRIPTION_KEY",
    "DESCRIPTION_MAX_CHARS",
    "DESCRIPTION_TARGET_CHARS",
    "DESC_PROMPT_VERSION",
    "DESC_REGEN_MIN_DELTA_RATIO",
    "DESC_TARGET_REASONS",
    "ENTITY_TYPES",
    "ENTITY_TYPE_DEFS",
    "ENTITY_TYPE_ORDER",
    "EXCLUDED_ENTITIES",
    "JUDGEMENT_KEY",
    "LINEAGE_ACTIVITY",
    "LINEAGE_AGENT",
    "MEMBER_SUMMARY_MAX_CHARS",
    "MIN_BASE_LENGTH",
    "MM_MEMBER_KIND_CODE",
    "MM_META_SOURCE_AUTO",
    "MM_META_SOURCE_USER",
    "MM_META_VISIBLE_STATUSES",
    "PROMPT_VERSION",
    "PROMPT_VERSION_WITHOUT_TYPE_DEFS",
    "RULE_VERSION",
    "STOP_PATTERNS",
    "SUFFIXES",
    "SUFFIX_MERGE_TYPES",
    "SUMMARY_MAX_CHARS",
    "DescribeFailure",
    "EntityJudgement",
    "EntityLabelError",
    "EntityTypeDef",
    "ExtractedEntity",
    "JudgeFailure",
    "MetaDescription",
    "MmMetaPersistError",
    "apply_rules",
    "build_description_prompt",
    "build_entity_material",
    "build_entity_prompt",
    "build_official_name_index",
    "describe_meta",
    "ensure_entity_node",
    "ensure_mm_member_kind",
    "fetch_label_targets",
    "fetch_meta_description_targets",
    "fetch_meta_members",
    "fetch_meta_type_vocab",
    "fetch_official_name_index",
    "fetch_registered_alias_index",
    "format_member_reason",
    "interpret_description",
    "interpret_response",
    "is_excluded_entity",
    "is_stopped_keyword",
    "judge_asset_entities",
    "list_mm_meta",
    "normalize_registration",
    "parse_member_reason",
    "prompt_version_for",
    "register_mm_meta",
    "replace_entity_labels",
    "resolve_registered_aliases",
    "type_names",
    "upsert_entity_edges",
    "upsert_meta_description",
]
