"""요약기에 붙이는 「제목(참고)」 블록 · ``title_match`` 해석 — 문서 · 사진 · 영상 · 음성 요약기가 함께 쓴다(spec 114 · 순수).

요약기는 원래 내용(본문 · 그림 · 장면 분석 · STT)만 보고 파일명을 보지 않았다. 그래서 영화 리뷰 영상이 출연자
인터뷰처럼 요약되고, STT 가 빈 노래가 빈 요약으로 남았다. 제목을 참고로 주되, **같은
호출 안에서** 제목이 내용과 맞는지 판단하게 해 틀린 제목은 스스로 버리게 한다 — 따로 묻는 호출을 더하지
않고도 틀린 제목 대부분을 막는다.

문구를 여기 한 곳에 두는 이유: 네 요약기가 같은 규칙을 말해야 결과를 같은 잣대로 비교할 수 있고, 문구가
바뀌면 결과가 통째로 달라지므로 판(``TITLE_HINT_VERSION``)과 함께 테스트로 못 박는다.
설계 배경: `specs/114-title-aware-summary`
"""

from __future__ import annotations

from typing import Any, TypedDict

# 문구가 바뀌면 올린다 — 저장된 요약이 어느 문구로 나왔는지 되짚을 단서다.
TITLE_HINT_VERSION = "title_hint.v1"
TITLE_MATCH_VALUES = ("yes", "no", "unsure")


class TitleMatchField(TypedDict, total=False):
    """요약 결과의 **선택 키** — 제목을 줬을 때만 채운다(사진 · 영상 · 문서 · 음성 결과가 상속한다).

    선택 키를 기반 클래스(``total=False``)로 따로 두는 이유: 요약기 모듈들이 ``from __future__ import
    annotations`` 를 써서 ``NotRequired[...]`` 표시가 문자열로 남아 TypedDict 가 선택 키로 알아보지 못한다.
    """

    title_match: str  # yes · no · unsure

_BLOCK = (
    "\n참고 — 자료 제목(파일명): {title}\n"
    '- 먼저 이 제목이 내용과 맞는지 판단해 JSON 에 "title_match": "yes|no|unsure" 를 함께 출력한다'
    "(yes: 제목이 가리키는 대상 · 작품 · 인물 · 장소 · 행사가 내용과 맞거나 모순이 없다 / no: 분명히 다르다 / "
    "unsure: 판단 근거가 없다).\n"
    "- title_match 가 yes 일 때만 제목의 고유명(작품 · 프로그램 · 문화재 · 인물 · 곡 · 장소)을 summary 와 keywords 에 "
    "반영하고, 제목이 알려 주는 형식(리뷰 · 해설 · 예고편 · 클립 · 라이브 · 인터뷰 등)을 summary 에 적는다.\n"
    "- no 나 unsure 면 제목을 완전히 무시하고 내용만으로 쓴다. 어느 경우든 제목에만 있고 내용으로 확인되지 않는 "
    "사실은 지어내지 않는다.\n"
)
# 사진 · 영상 전용 — 제목의 이름이 요약 글자 수를 차지하면 외형 묘사가 줄어든다. 생김새로 찾는 검색이 이 묘사를
# 쓰므로 줄이지 말라고 못 박는다.
_APPEARANCE = (
    "- 외형 묘사(대상 · 재질 · 형태 · 색 · 구도 · 장면)는 제목을 반영하더라도 줄이지 않는다 — "
    "생김새로 찾는 검색이 이 묘사를 쓴다.\n"
)


def build_title_block(title: str, *, keep_appearance: bool) -> str:
    """요약 프롬프트에 끼울 「제목(참고)」 블록을 만든다(순수).

    Args:
        title: 참고로 줄 제목(``summary_title`` 을 통과한 값).
        keep_appearance: 외형 묘사 유지 줄을 붙일지 — 사진 · 영상 요약기만 참.

    Returns:
        프롬프트에 그대로 끼울 블록 문자열(앞뒤 개행 포함).
    """
    return _BLOCK.format(title=title) + (_APPEARANCE if keep_appearance else "")


def insert_title_block(prompt: str, marker: str, block: str) -> str:
    """블록을 ``marker`` 첫 등장 바로 앞에 끼운다. 없으면 끝에 붙인다(순수).

    왜 「내용 앞」인가: 지시 · 형식 줄 뒤, 본문(텍스트 · 장면 결과) 앞에 두어야 모델이 블록을 규칙으로 읽는다.

    Args:
        prompt: 원래 프롬프트.
        marker: 블록을 끼울 자리 표지(예: ``"\\n텍스트:"``).
        block: ``build_title_block`` 결과.

    Returns:
        블록이 끼워진 프롬프트.
    """
    i = prompt.find(marker)
    return prompt[:i] + block + prompt[i:] if i >= 0 else prompt + block


def build_title_only_prompt(title: str, *, summary_max_chars: int, top_k_keywords: int) -> str:
    """내용이 비어 있을 때(빈 STT 등) **제목만으로** 최소 요약을 만드는 프롬프트(순수).

    무엇을 위한가: STT 가 빈 노래 음원은 지금 요약 · 주제 · 개체가 모두 비어, 파일명에 있는 가수 · 곡명조차
    메타에 남지 않는다. 제목에 드러난 사실만 한 문장으로 남긴다 — 지어내지 않게 못 박는다.

    Args:
        title: ``summary_title`` 을 통과한 제목.
        summary_max_chars: 요약 상한(설정값). 실제 지시는 100자 이내로 더 짧게 준다.
        top_k_keywords: 키워드 상한.

    Returns:
        LLM 에 그대로 넣을 프롬프트.
    """
    limit = min(100, int(summary_max_chars))
    return (
        "아래는 자료의 제목이다. 내용 텍스트(음성 인식 결과 등)가 없어 제목만으로 최소한의 정보를 반드시 JSON만 출력해.\n"
        '형식: { "summary": "요약", "keywords": ["키워드1"] }\n'
        f"- summary 는 제목에 드러난 사실(누구의 어떤 곡 · 방송 · 장면인지)만 한 문장, {limit}자 이내. 제목에 없는 내용 금지.\n"
        f"- keywords 는 제목의 고유명 위주 최대 {int(top_k_keywords)}개.\n"
        f"제목: {title}"
    )


def parse_title_match(value: Any) -> str:
    """LLM 응답의 ``title_match`` 를 yes · no · unsure 중 하나로 접는다(순수).

    그 밖의 값(빠짐 · 오타 · 다른 형)은 ``unsure`` — 제목을 반영하지 않는 쪽이라 안전하다.

    Args:
        value: 응답 dict 의 ``title_match`` 값(아무 형이나).

    Returns:
        ``"yes"`` · ``"no"`` · ``"unsure"``.
    """
    v = value.strip().lower() if isinstance(value, str) else ""
    return v if v in TITLE_MATCH_VALUES else "unsure"
