-- =============================================================================
-- ext_meta 키 등록 — title_match · title_only (spec 114 제목 참고 요약).
--
-- 배경: 요약기가 자료 제목(파일명)을 참고할 때 같은 호출에서 「제목이 내용과 맞는가」를 판단해 돌려준다
--   (title_match: yes · no · unsure). 빈 STT 음성을 제목만으로 요약했으면 title_only=true 가 붙는다.
--   ext_meta 는 쓰기 경로에서 이 표에 등록된 키만 받으므로(validate_ext_meta — 미등록 키 거부), 등록하지
--   않으면 제목 참고를 켜는 순간 적재가 ExtMetaValidationError 로 막힌다.
-- 처방: general · medical 두 도메인에 두 키를 등록한다. 열람 등급은 그 도메인의 summary 와 같게 —
--   general = authenticated · medical = regulated(v290 관례 · 도메인 하한과 무관하게 등급표 자체가 맞도록).
-- DDL 없음. 멱등 INSERT(ON CONFLICT (domain, meta_key) DO NOTHING). field_id 는 이 표의 정적 시드 관례
--   (gen_random_uuid · v291 주석)를 따른다.
-- 적용 순서: v307 이후.
-- =============================================================================

INSERT INTO ext_meta_field_registry (field_id, domain, meta_key, json_schema, description, status, access_tier)
SELECT gen_random_uuid(), d.domain, k.meta_key, k.json_schema::jsonb, k.description, 'active', d.tier
FROM (VALUES ('general', 'authenticated'), ('medical', 'regulated')) AS d(domain, tier)
CROSS JOIN (VALUES
    ('title_match', '{"type":"string","enum":["yes","no","unsure"]}',
     '요약기가 같은 호출에서 판단한 제목(파일명)-내용 일치 — yes 일 때만 제목을 요약 · 주제에 반영(spec 114)'),
    ('title_only', '{"type":"boolean"}',
     '내용(STT 등)이 비어 제목만으로 요약했는지 — 참이면 요약이 제목에서 나왔다(spec 114)')
) AS k(meta_key, json_schema, description)
ON CONFLICT (domain, meta_key) DO NOTHING;
