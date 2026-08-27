-- =============================================================================
-- 개체 타입 어휘 분리 — mm_meta_type_vocab 신설 + mm_skill 예약 행 이관 (spec 086 · v303).
--
-- 배경(2026-08-27 개념 감사): `mm_skill` 한 테이블에 **성격이 다른 두 가지**가 섞여 있었다.
--   ① 분류 스킬 — 판정 대상이 **자산**. 결과는 `asset_mm_skill_label` 로 간다.
--   ② 개체 타입 어휘 — 판정 대상이 **개체**(node.entity). 결과는 `node.entity_type` 으로 간다.
-- 실측 증거: `mm_skill` 3행 중 `skill_code='mm_meta_type'` 만 `asset_mm_skill_label` 행이 0개이고,
--   모든 화면·API 조회가 `skill_code <> 'mm_meta_type'` 로 그 행을 **항상 걸러냈다**. 같은 테이블에
--   넣었는데 매번 걸러내야 하는 것은 원래 다른 것이라는 신호다. ADR 2026-08-24 §3 정정 참조.
--
-- 왜 지금인가: 대량 적재 전이어야 한다. 적재 후 분리는 전량 재판정을 부른다.
--
-- 컬럼 명명 — 도메인 언어로 바꾼다(혼동의 뿌리가 "스킬"이라는 말이었다):
--   skill_code → vocab_code · labels → types. `name`/`version`/`policy`/`status` 는 유지한다
--   (개정 카운터·활성 토글의 의미가 그대로다). 정의문 **문서 모양**은 085 와 동일하게 두어
--   검증기(`load_skill`)를 계속 공유한다 — 모양이 같은 것을 두 벌 검증하면 언젠가 갈린다.
--   읽기 SQL 이 `types AS labels`·`vocab_code AS skill_code` 로 별칭을 붙여 그 검증기에 넘긴다.
--
-- vocab_code UNIQUE 를 두는 이유: 지금은 'default' 한 행뿐이지만, 084 F04(다도메인 투입 시 개체
--   공간 분리)에서 도메인별 어휘가 필요해진다. 그때 행만 늘리면 되도록 자연키를 미리 둔다.
--   단일 행 강제(CHECK 로 상수 고정)를 택하지 않은 이유가 이것이다.
--
-- 가역성(원복 가능 — 사용자 요구 2026-08-27):
--   upgrade   = 테이블 생성 → mm_skill 의 예약 행을 **복사**(skill_id 를 vocab_id 로 보존) → 원본 삭제
--   downgrade = mm_skill 로 되돌려 INSERT(같은 skill_id·skill_code) → 테이블 DROP
--   skill_id 를 보존하므로 왕복 후 행이 완전히 동일하다. 이관 전 **자식 행 0건 가드**를 둔다 —
--   `asset_mm_skill_label` 이 이 코드를 참조하고 있으면(설계상 있을 수 없다) 삭제가 FK 에 막히므로,
--   조용한 실패 대신 사유가 분명한 예외로 세운다.
--
-- 멱등: CREATE TABLE IF NOT EXISTS + INSERT ... WHERE NOT EXISTS + DELETE(있을 때만) — 재실행 안전.
-- 기존 스키마 무접촉: 신규 테이블 1개 추가 + mm_skill 의 **행 하나** 이동뿐(컬럼·제약 변경 0).
-- 적용 순서: v302 이후.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mm_meta_type_vocab (
    vocab_id   UUID PRIMARY KEY,                  -- 앱 발급 UUIDv7(헌법 6조 · PG17 에 uuidv7() 없음)
    vocab_code VARCHAR(100) NOT NULL UNIQUE,      -- 자연키. 지금은 'default' 1행(F04 대비)
    name       TEXT         NOT NULL,             -- 사람이 읽는 어휘 이름
    version    INTEGER      NOT NULL,             -- 개정 카운터. 오르면 배치가 재판정 대상을 다시 집는다
    policy     JSONB        NOT NULL,             -- 선언 전문(085 와 같은 모양 — 검증기 공유)
    types      JSONB        NOT NULL,             -- 타입 정의문 배열(name/definition/not)
    status     VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_mm_meta_type_vocab_status CHECK (status IN ('active', 'disabled'))
);

COMMENT ON TABLE mm_meta_type_vocab IS
    '개체 타입 어휘(인물·장소·조직·작품·사건)의 정본. 판정 대상은 개체이며 결과는 node.entity_type '
    '으로 간다 — 자산을 분류하는 mm_skill 과는 대상·엔진이 다르다(spec 086 · v303).';

-- 예약 행 이관 — mm_skill 에 있던 타입 어휘를 옮긴다(skill_id 를 vocab_id 로 보존 → 왕복 동일).
DO $$
DECLARE
    child_rows BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM mm_skill WHERE skill_code = 'mm_meta_type') THEN
        RAISE NOTICE '이관할 예약 행이 없다(mm_meta_type) — 신규 배포이거나 이미 이관됐다. 테이블만 만든다.';
        RETURN;
    END IF;

    -- 가드: 이 코드로 판정된 자산 라벨이 있으면 설계 위반이다(타입 어휘는 자산에 붙지 않는다).
    --   그냥 DELETE 하면 FK(NO ACTION)에 막혀 "왜 실패했는지" 모를 오류가 난다.
    SELECT COUNT(*) INTO child_rows
      FROM asset_mm_skill_label WHERE skill_code = 'mm_meta_type';
    IF child_rows > 0 THEN
        RAISE EXCEPTION
            '타입 어휘 코드로 판정된 자산 라벨이 %건 있다 — 이관을 중단한다. '
            '타입 어휘는 자산에 붙지 않아야 한다(spec 086). 그 행들을 먼저 조사·정리한다.',
            child_rows;
    END IF;

    INSERT INTO mm_meta_type_vocab
        (vocab_id, vocab_code, name, version, policy, types, status, created_at, updated_at)
    SELECT skill_id, 'default', name, version, policy, labels, status, created_at, updated_at
      FROM mm_skill WHERE skill_code = 'mm_meta_type'
        ON CONFLICT (vocab_code) DO NOTHING;   -- 재실행 안전

    DELETE FROM mm_skill WHERE skill_code = 'mm_meta_type';
    RAISE NOTICE '타입 어휘 1행을 mm_meta_type_vocab 으로 이관했다(vocab_code=default).';
END $$;
