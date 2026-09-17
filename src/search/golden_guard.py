"""코퍼스-골든 정합 가드(025 G3, FR-004 · 099 T031 로 기준 교체) — 순수·결정적.

운영 규칙은 그대로다: **코퍼스에 자산이 추가되면 골든 질의도 추가**되어야 한다(평가 계기판이
코퍼스를 따라가게). 바뀐 것은 "무엇을 단위로 덮였는지 보느냐"다.

- **현행(099 T031)**: `uncovered_assets(registered_ids, golden_ids)` — **자산 id 집합 뺄셈**.
  뜻은 "적재됐는데 어느 골든 질의의 정답도 아닌 자산". 골든이 정답 자산 id 를 직접 들고 있어
  파일명 해석이 전혀 필요 없다. 비유하면 학급 명부와 답안지 이름을 맞대 보는 일이다.
- **레거시(025~098)**: `topic_of_filename` + `uncovered_topics` — 파일명에서 주제를 읽어 토픽
  단위로 비교했다. 아래 ⚠️ 참고 — 현 코퍼스에서 무력화됐다.

DB·OS 미접촉(호출부가 집합을 만들어 넘긴다 — 헌법 6조). 결과는 정렬해 돌려주므로 같은 입력이면
같은 보고가 나온다(헌법 3조).
"""

from __future__ import annotations

import re

# 신규 출처-prefix(주제 아님 — 벗겨야 2번째 토큰이 진짜 주제). build_golden_ko_draft 와 동일 규약.
_SOURCE_PREFIXES = ("youtube", "wikipedia")
# 재수집 명명 `<uuid>__<제목>_(주제).ext` 의 **끝 괄호**가 주제 표식(정본). 제목에 밑줄·공백·특수문자가
# 많아 토큰 위치가 불안정하므로 이 괄호 표식을 토큰 분해보다 우선 신뢰한다.
_TRAILING_PAREN = re.compile(r"\(([^()]+)\)\s*$")


# ── 현행 가드(099 T031) ────────────────────────────────────────────────────────
def uncovered_assets(registered_ids: set[str], golden_ids: set[str]) -> list[str]:
    """골든 질의가 아직 정답으로 품지 못한 **적재 자산**을 찾는다(순수·결정적).

    "적재는 됐는데 어느 골든 질의의 정답도 아닌 자산"이 미커버다. 그런 자산은 검색이 잘 찾든
    못 찾든 점수에 잡히지 않으므로, 많아지면 계기판이 코퍼스를 대표하지 못한다. 비유: 시험
    범위에 없는 단원이 교과서에만 계속 늘어나는 상태.

    집합 뺄셈 한 번이 전부다 — 파일명 규약을 해석하지 않는다. 옛 토픽 가드가 파일명에 기대다
    깨진 원인을 구조적으로 없앤 것이다(아래 `topic_of_filename` ⚠️ 참고).

    Args:
        registered_ids: 코퍼스에 적재된 자산 id 집합(예: `asset.status='registered'` 행의 `asset_id`).
            호출부가 DB 에서 읽어 넘긴다 — 이 함수는 DB 를 건드리지 않는다.
        golden_ids: 골든셋의 모든 질의가 정답(`relevant`)으로 지목한 자산 id 집합.

    Returns:
        미커버 자산 id 목록(오름차순 정렬). 방향은 한쪽이다 — **골든에만 있고 적재되지 않은
        id**(삭제된 자산 등)는 여기 나오지 않는다. 비어 있으면 코퍼스 전량이 골든에 덮인 상태다.
    """
    return sorted(registered_ids - golden_ids)


# ── 레거시: 파일명 주제표식 가드(025~098 · 099 T031 로 대체됨) ──────────────────
def topic_of_filename(file_name: str) -> str:
    """⚠️ **레거시 — 옛 골든 전용.** 파일명에서 토픽 키를 뽑는다(순수·결정적, 명명 규약 4종).

    🔴 **왜 죽었나**(099 T031 실측 · 2026-09-17): 현 코퍼스 16,865건에서 이 함수가 뽑는 고유
    토픽이 **16,569개**다 — 토픽/자산 비율 **0.982**, 자산이 **1건뿐인 토픽이 16,418개(97.3%)**.
    "같은 토픽 = 같은 정답군"이라는 전제가 무너져 정답군을 만들 수 없다. 가장 큰 토픽조차
    `SUB`(30건)·`1991`(12건) 같은 유튜브 제목 파편이라 주제가 아니다. 파일명이 주제를 담던
    수집 규약이 코퍼스 전면 교체(국가유산청 공개 API 수집)로 사라진 결과다.

    🔴 **새 가드는 `uncovered_assets`** — 파일명 대신 자산 id 로 커버리지를 본다(위 함수).

    남겨 둔 이유: 옛 골든 전용 도구(문서 레포 `tools/golden_v3_mech.py` 등)와
    `scripts/build_golden_manifest.py` 의 `topics` 태그 생성이 아직 이 함수를 부른다. 한 번에
    지우면 그 도구들이 조용히 깨진다. **새 코드에서는 쓰지 말 것.**

    - **신규(재수집)** `<uuid>__<제목>_(주제).ext` → **끝의 괄호 안**이 주제(`…_(전통주).svg` → `전통주`).
      제목에 밑줄·공백·특수문자가 많아 토큰 위치가 불안정하므로 괄호 표식을 우선 신뢰한다.
    - `youtube_사막_<id>.jpg`·`wikipedia_고려청자_<id>.txt` → 2번째 토큰(`사막`·`고려청자`).
    - `등산_입문_<id>_<제목>.mp4` → `등산`(구형: 첫 토큰). `<uuid>__` 접두가 있으면 벗기고 적용한다.
    - 언더스코어가 없으면 확장자 뗀 stem 전체가 토픽(예: `manifest.json` → `manifest`) —
      **현 코퍼스는 96.8%가 이 퇴화 경로로 떨어진다**.

    Args:
        file_name: 코퍼스 파일명(경로 아님).

    Returns:
        토픽 키 문자열. 파일명이 비었거나 stem 이 없으면 빈 문자열.
    """
    stem = (file_name.rsplit(".", 1)[0] if "." in file_name else file_name).strip()
    if not stem:
        return ""
    # 신규 명명: 끝의 (주제) 괄호가 정본(UUID 접두·자유 제목 뒤).
    m = _TRAILING_PAREN.search(stem)
    if m:
        return m.group(1).strip()
    # UUID 접두(`<uuid>__`) 제거 후 구형/출처-prefix 규약 적용.
    if "__" in stem:
        stem = stem.split("__", 1)[1]
    parts = stem.split("_")
    if len(parts) >= 2 and parts[0] in _SOURCE_PREFIXES:
        return parts[1]
    return parts[0]


def uncovered_topics(corpus_topics: set[str], golden_topics: set[str]) -> list[str]:
    """⚠️ **레거시 — 옛 골든 전용.** 골든이 덮지 못한 코퍼스 **토픽**을 찾는다(순수·결정적).

    🔴 `topic_of_filename` 이 만든 토픽을 비교하므로 그 함수와 **함께 무력화**됐다(위 ⚠️ 참고 —
    현 코퍼스에서 미커버 토픽 16,569개, 즉 켜면 무조건 빨간불이다). 현행 가드는
    **`uncovered_assets`**(자산 id 집합 뺄셈)이며 e2e·측정 하니스는 그쪽으로 옮겼다.
    옛 골든(`golden_os.json`·`golden_words.v2.json`)을 읽는 도구가 남아 있어 함수만 보존한다.
    **새 코드에서는 쓰지 말 것.**

    Args:
        corpus_topics: 코퍼스 파일명에서 뽑은 토픽 집합.
        golden_topics: 골든 질의가 태그로 달고 있는 토픽 집합.

    Returns:
        덮이지 않은 토픽 목록(정렬).
    """
    return sorted(corpus_topics - golden_topics)
