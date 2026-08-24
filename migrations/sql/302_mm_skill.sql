-- =============================================================================
-- 분류 스킬 저장 — mm_skill + asset_mm_skill_label (spec 085 · v302).
--
-- 배경: 사용자가 선언적 설정(스킬 — 라벨·정의문·경계·정책·버전)을 등록하면 배치가 그 렌즈로
--   자산을 분류하는 층을 추가한다(ADR 2026-08-24-classification-skill). 실행 엔진은 하나로
--   고정되고 능력만 데이터로 주입되므로, 스킬의 정본은 코드가 아니라 **이 테이블의 등록 행**이다
--   (JSON 파일은 등록 CLI 의 입력 수단일 뿐 — 058 시드 공개 블로커 전례 회피).
--
-- mm_skill (스킬 레지스트리):
--   PK 는 topic_registry 관례를 따른다 — 앱 발급 UUIDv7 skill_id + 자연키 skill_code UNIQUE
--   (헌법 6조 UUIDv7 PK 정책 준수). ⚠️ 라벨 행이 존재하면 skill_code 는 자식 FK(NO ACTION)에
--   막혀 사실상 불변이다 — 개명은 후속 수동 절차(OS 색인 키도 "스킬코드/라벨코드"라 재색인 동반).
--   policy/labels 는 선언 전문을 JSONB 로
--   보존한다 — 구조 검증은 등록 CLI 의 앱 레벨 fail-fast 가 담당(FK·CHECK 로 옮기지 않는 이유:
--   라벨 어휘가 스킬마다 다르고 개정마다 변해 DDL 로 고정하면 개정 = 마이그레이션이 돼 버린다).
--
-- asset_mm_skill_label (판정 결과·이력):
--   multi 정책이 기본이라 자산당 스킬당 1..N행 — PK(asset_id, skill_code, label_code).
--   해당없음(unassigned)도 단독 1행으로 기록한다: **행 존재 = 판정 이력**이며 행 부재 = 미판정/실패
--   (재대상). 판정 실패는 기록하지 않는다(judge 가 실패/성공을 명시 구분 — 084 §2 동형 규율).
--   skill_version 은 판정 당시 스킬 버전 — 개정(버전 증가) 시 배치가 version<현행 행을 재선별해
--   백필한다(헌법 3조 재현성: 읽기 경로 LLM 0·재판정은 백필만).
--   skill_code FK 는 기본 NO ACTION — 스킬 삭제 전 라벨 행 수동 정리를 강제한다(spec 비범위:
--   자동 정리는 후속·조용한 연쇄 삭제 방지).
-- 멱등: 두 테이블 모두 IF NOT EXISTS — 재실행 안전. 기존 테이블 무접촉(신규 2개만 추가).
-- 적용 순서: v301 이후.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mm_skill (
    skill_id   UUID PRIMARY KEY,
    skill_code TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    policy     JSONB NOT NULL,
    labels     JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS asset_mm_skill_label (
    asset_id      UUID NOT NULL REFERENCES asset (asset_id) ON DELETE CASCADE,
    skill_code    TEXT NOT NULL REFERENCES mm_skill (skill_code),
    label_code    TEXT NOT NULL,
    skill_version INTEGER NOT NULL,
    prompt_version VARCHAR(40) NOT NULL,
    decided_by    VARCHAR(20) NOT NULL DEFAULT 'llm',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_id, skill_code, label_code)
);

-- 스킬·라벨 단위 관리 조회(라벨 분포 리포트·SC 검증)용 — PK 선두가 asset_id 라 스킬 축 접근이 느리다.
CREATE INDEX IF NOT EXISTS idx_asset_mm_skill_label_skill
    ON asset_mm_skill_label (skill_code, label_code);

COMMENT ON TABLE mm_skill IS
    '분류 스킬 레지스트리(085) — 선언적 설정(라벨·정의문·정책)의 단일 정본. 파일은 입력 수단일 뿐.';
COMMENT ON COLUMN mm_skill.skill_id IS
    'PK — 앱 발급 UUIDv7(src/database/ids.uuid7 · topic_registry 관례).';
COMMENT ON COLUMN mm_skill.skill_code IS
    '자연키(유니크) — 라벨 행·OS mm_skill_labels 키("스킬코드/라벨코드")가 이 코드를 쓴다.';
COMMENT ON COLUMN mm_skill.name IS
    '스킬 표시명(한국어 — 패싯 축 제목 등 노출용). labels JSONB 내부의 라벨별 name 과 별개.';
COMMENT ON COLUMN mm_skill.version IS
    '스킬 버전 — 개정(정의문 수정 등) 시 +1. 배치가 version<현행 판정 행을 백필 재분류(SC-03).';
COMMENT ON COLUMN mm_skill.policy IS
    '집행 정책 전문(JSONB) — selection(multi|single)·unassigned 라벨·max_labels. 코드가 결정적으로 집행.';
COMMENT ON COLUMN mm_skill.labels IS
    '라벨 선언 전문(JSONB) — [{code,name,definition,not}…]. definition/not 만 프롬프트로 소비.';
COMMENT ON COLUMN mm_skill.status IS
    '스킬 상태 어휘: active(배치 대상)·disabled(중단 — 판정 이력은 보존).';

COMMENT ON TABLE asset_mm_skill_label IS
    '스킬 판정 결과(085) — 자산당 스킬당 1..N행(multi). 행 존재=판정 이력·부재=미판정(재대상).';
COMMENT ON COLUMN asset_mm_skill_label.label_code IS
    '판정 라벨 code — 해당없음도 unassigned code 단독 1행으로 기록(실패와 구분).';
COMMENT ON COLUMN asset_mm_skill_label.skill_version IS
    '판정 당시 스킬 버전 — 현행보다 낮으면 백필 재분류 대상(헌법 3조 재현성).';
COMMENT ON COLUMN asset_mm_skill_label.prompt_version IS
    '판정 당시 프롬프트 문안 버전(mm_classify PROMPT_VERSION) — asset_topic.policy_version 선례.
     재선별 기준은 skill_version(문안 개정 재판정은 수동 백필) — 이 칸은 이력 완전성 목적.';
COMMENT ON COLUMN asset_mm_skill_label.decided_by IS
    '판정 경로 어휘: llm(배치 판정·기본). 확장 여지: manual.';
