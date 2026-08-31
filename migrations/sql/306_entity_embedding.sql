-- =============================================================================
-- 개체 의미 검색 — entity_embedding 신설 (spec 090 T007 · v306).
--
-- 배경: 개체 검색이 **글자가 겹칠 때만** 찾힌다. 089(질의 토큰 분리)로 다어절 질의가
--   0% → 43.3% 가 됐지만 거기서 멈췄다 — 남은 실패 30건은 매칭이 아니라 **어휘 불일치**다
--   (`발효`→김치 0건 · `도자기`→고려청자 0건 · 개체 텍스트에 그 낱말이 아예 없다).
--   개체마다 벡터를 두면 같은 뜻의 다른 낱말로도 찾을 수 있다.
--   착수 전 측정(090 G0 · 코드 0줄)에서 예측 C1 80% · C2 83.3% · C3 100%.
--
-- 🔴 **왜 DB 에 두는가.** 자산은 `asset_embedding`(DB) + OpenSearch 이중 구조인데 **DB 가 정본**
--   이라 재색인 때 다시 임베딩하지 않는다. 개체는 그 이유가 더 크다 — 개체는 배치가 다시 만들
--   때마다 바뀌고(087 재판정에서 925→1,065), 매번 전량 재임베딩하면 비용이 그만큼 든다.
--
-- 🔴 **`material_hash` 가 이 설계의 핵심이다.** 개체가 다시 만들어져도 재료 텍스트가 같으면
--   임베딩을 재사용한다. 이것이 없으면 배치마다 1,000+ 회 임베딩 호출이 돈다(합격선 C6:
--   재실행 시 재사용 ≥95%).
--
-- ⚠️ **FK 를 걸 수 없다.** 참조 대상 `node(entity_type, entity_uid)` 의 유니크가
--   `uq_node_entity ... WHERE node_kind='entity'` 라는 **부분 인덱스**이고, PostgreSQL 은 부분
--   유니크를 FK 대상으로 받지 않는다. v304(entity_mm_skill_label)가 같은 이유로 앱 검증을
--   택했다 — **같은 방식을 따른다**(새로 판단하지 않는다).
--
-- 컬럼 타입은 **참조 대상과 정확히 맞춘다**(v304 에서 네 컬럼이 어긋나 고친 전례가 있다):
--   entity_type VARCHAR(40) · entity_uid VARCHAR(255) = `node` 와 같은 폭
--   embedding VECTOR(1536) · model_name VARCHAR(200) · model_version VARCHAR(100)
--     = `asset_embedding` 과 같은 폭 — 같은 뜻에 다른 타입을 쓰면 나중에 조사거리가 된다.
--
-- 🔴 **저장 차원은 1536 이고 모델 출력은 1024 다.** bge-m3 raw 가 1024D 이므로 앱이
--   `pad_embedding_to_storage_dim` 을 거쳐 저장한다(헌법: 1536D 통일 · 자산 임베딩과 같은 함수).
--   0 패딩은 코사인 유사도를 바꾸지 않는다.
--
-- 개체 하나에 벡터 하나 — PK(entity_type, entity_uid). 자산과 달리 청크·채널이 없다:
--   개체 재료는 짧고(G0 실측 중위 68자) 한 덩어리로 임베딩하면 충분하다.
--
-- 🔴 **가역이다** — downgrade = `DROP TABLE` 하나. 다른 테이블은 무접촉(신규 1개만 추가).
-- 멱등: IF NOT EXISTS — 재실행 안전. 적용 순서: v305 이후.
-- =============================================================================

CREATE TABLE IF NOT EXISTS entity_embedding (
    entity_type    VARCHAR(40)  NOT NULL,   -- node.entity_type 과 같은 폭
    entity_uid     VARCHAR(255) NOT NULL,   -- node.entity_uid 와 같은 폭(표기 키)
    embedding      VECTOR(1536) NOT NULL,   -- asset_embedding 과 같은 차원(헌법 1536D)
    model_name     VARCHAR(200) NOT NULL,   -- asset_embedding 과 같은 폭
    model_version  VARCHAR(100),            -- 선택(asset_embedding 관례)
    material_hash  CHAR(64)     NOT NULL,   -- 재료 텍스트의 SHA-256 — 같으면 재임베딩하지 않는다
    material_chars INTEGER      NOT NULL,   -- 재료 길이(품질 진단용 · G0 에서 중위 68자였다)
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_uid)
);

-- 재사용 판단이 이 축으로 돈다 — 배치가 "이 재료 그대로면 건너뛴다"를 물을 때 쓴다(C6).
CREATE INDEX IF NOT EXISTS idx_entity_embedding_hash
    ON entity_embedding (material_hash);

-- 🔴 벡터 인덱스(HNSW)는 **지금 만들지 않는다.** 개체가 82개(노출 기준)라 순차 스캔이 더 빠르고,
--   pgvector HNSW 는 빌드 비용이 있다. 개체가 1만 건을 넘으면 그때 별도 마이그레이션으로 추가한다
--   (자산 쪽 HNSW 검증이 KPI P1 로 이미 백로그에 있다 · 같은 판단을 재사용하면 된다).

COMMENT ON TABLE entity_embedding IS
    '개체(멀티모달 메타)의 의미 검색용 벡터 — 글자가 겹치지 않아도 찾게 한다(spec 090 · v306). '
    'material_hash 로 재료가 같으면 재임베딩을 건너뛴다. FK 불가 사유는 마이그레이션 SQL 헤더 참조.';
COMMENT ON COLUMN entity_embedding.material_hash  IS
    '임베딩 재료 텍스트의 SHA-256 — 개체가 재생성돼도 재료가 같으면 벡터를 재사용한다.';
COMMENT ON COLUMN entity_embedding.material_chars IS
    '재료 길이(자). 짧으면 임베딩 품질이 떨어진다 — G0 실측 중위 68자.';
