-- =============================================================================
-- 묶음의 상위 계층 — entity_mm_skill_label 신설 (spec 087 T012 · v304).
--
-- 배경: 분류 스킬 라벨이 **자산**에 붙어 있어서, 갈래로 좁히면 한 대상(개체)이 갈래마다 쪼개졌다.
--   실측(2026-08-26) — `경주시` 자료 6건 = 갈래없음 5 + 음료 1 + 디저트 1 → 목록 카드는 5건인데
--   상세로 들어가면 6건이 나왔다. 같은 결함 7개. 화면에서 두 숫자를 병기해 **증상만** 덮었고
--   원인은 남아 있었다(설계이력 2026-08-27(2)).
--   라벨을 **개체에** 붙이면 갈래가 묶음의 상위 계층이 되고 쪼개짐이 사라진다.
--
-- 🔴 **자산 라벨(`asset_mm_skill_label`)을 대체하지 않는다.** 둘은 용도가 다르다:
--     · 자산 라벨 = "무엇을 받을까" — 「한식 자료 90건 통째 zip」. 그 90건 중 묶음에 든 것은
--       일부여서, 나머지를 데이터셋으로 받으려면 파일에 붙은 라벨이 필요하다.
--     · 개체 라벨 = "무엇을 볼까" — 「음악 갈래 → 아이유」 탐색 계층.
--
-- 모양은 `asset_mm_skill_label`(v302)과 **같다** — 키가 자산에서 개체로 바뀐 것뿐이다. 앱 불변식
--   2종도 그대로 가져간다: ①`unassigned` 행은 같은 (개체, 스킬)의 다른 라벨과 공존 금지(단독)
--   ②판정 실패는 행을 남기지 않는다(행 존재 = 판정 이력 · 부재 = 미판정/실패 → 재선별).
--
-- ⚠️ **FK 를 걸 수 없다.** 참조 대상 `node(entity_type, entity_uid)` 의 유니크가
--   `uq_node_entity ... WHERE node_kind='entity'` 라는 **부분 인덱스**이고, PostgreSQL 은 부분
--   유니크를 FK 대상으로 받지 않는다. 그래서 개체 존재 검증은 **앱**이 한다(persist 계약).
--   `skill_code` FK 는 v302 관례대로 NO ACTION — 스킬 삭제 전 라벨 행 수동 정리를 강제한다.
--
-- multi 정책이 기본이라 개체당 스킬당 1..N행 — PK(entity_type, entity_uid, skill_code, label_code).
--   사용자 결정(2026-08-27): **multi 로 간다.** 근거 — "음악으로 검색했을 때 여러 음악가의 음악이
--   나와야 한다". single 이면 갈래가 비고, `김치`=한식+발효처럼 둘 다 참인 경우 정보를 잃는다.
--
-- 🔴 **시험 전제 — 합격선 미달이면 폐기한다**(spec 087 §3). downgrade = `DROP TABLE` 로 완전 가역이며
--   다른 테이블은 무접촉이다(신규 1개만 추가).
-- 멱등: IF NOT EXISTS — 재실행 안전. 적용 순서: v303 이후.
-- =============================================================================

-- 🔴 **컬럼 타입은 참조 대상과 정확히 맞춘다**(2026-08-27 정정 · 사용자 지적으로 발견).
--   처음 쓸 때 v302·node 를 확인하지 않아 네 컬럼이 어긋나 있었다:
--     skill_code  VARCHAR(100) → 부모 `mm_skill.skill_code` 가 TEXT 인데 **자식이 더 좁았다**
--     entity_type VARCHAR(50)  → `node.entity_type` 은 VARCHAR(40)
--     entity_uid  TEXT         → `node.entity_uid` 는 VARCHAR(255)
--     prompt_version/decided_by TEXT → v302 는 VARCHAR(40)/VARCHAR(20)
--   FK 는 암묵 캐스팅으로 동작했으나, 같은 뜻에 다른 타입을 쓰면 나중에 "왜 여기만 제한이
--   다른가"를 조사하게 된다. 087 이 시험 중이고 행이 적어 지금 고치는 것이 가장 싸다.
CREATE TABLE IF NOT EXISTS entity_mm_skill_label (
    entity_type    VARCHAR(40)  NOT NULL,   -- node.entity_type 과 같은 폭
    entity_uid     VARCHAR(255) NOT NULL,   -- node.entity_uid 와 같은 폭(표기 키)
    skill_code     TEXT         NOT NULL
                   REFERENCES mm_skill(skill_code),   -- 부모와 같은 TEXT · NO ACTION(v302 관례)
    label_code     TEXT         NOT NULL,   -- 스킬 라벨 코드(어휘는 mm_skill.labels 가 정본)
    skill_version  INTEGER      NOT NULL,   -- 판정 당시 스킬 버전 — version<현행 행이 백필 대상
    prompt_version VARCHAR(40)  NOT NULL,   -- v302 와 같은 폭(이력 완전성 · 재선별 기준 아님)
    decided_by     VARCHAR(20)  NOT NULL DEFAULT 'llm',   -- v302 와 같은 폭·기본값
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_uid, skill_code, label_code)
);

-- 갈래 → 개체 조회가 이 축으로 돈다(라벨로 개체를 찾는 것이 이 테이블의 주 용법).
CREATE INDEX IF NOT EXISTS idx_entity_mm_skill_label_label
    ON entity_mm_skill_label (skill_code, label_code);

COMMENT ON TABLE entity_mm_skill_label IS
    '개체(멀티모달 메타)의 분류 스킬 판정 라벨 — 갈래가 묶음의 상위 계층이 되게 한다. '
    'asset_mm_skill_label(자산 라벨)을 대체하지 않는다: 자산 라벨=무엇을 받을까 · '
    '개체 라벨=무엇을 볼까(spec 087 · v304). FK 불가 사유는 마이그레이션 SQL 헤더 참조.';
