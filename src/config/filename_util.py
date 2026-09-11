"""파일명(basename) 단일 출처 — 검색 색인·검색결과 표시·샘플 API 공용(069 D3·순수·표준 라이브러리만).

경로/URI 에서 파일명만 뽑는 **공통 코어**다. 통합 전에는 ``search_group``·``sample_search_api``·
``opensearch_sync`` 가 각자 ``_basename`` 사본을 두었는데, 코어 로직이 동일해 여기 한 곳으로 모은다
(SSOT). ``src/config/__init__`` 이 비어 있어 heavy 의존(torch 등)을 끌지 않으므로, "표준 라이브러리만
import" 순수 계약인 세 호출처가 모두 안전하게 import 할 수 있다(순환/무거운 import 없음).

**책임 경계**: ``basename_of`` 는 asset_id 프리픽스(``{asset_id}__``)를 **벗기지 않는다**(색인·샘플 경로는
원본 파일명이 그대로 필요). 표시 전용 프리픽스 제거(065 T605)는 ``strip_asset_id_prefix``/``display_file_name``
가 담당한다 — 077 레포 분리에서 백엔드가 쓰는 표시 유틸을 코어(config)로 승격했다(종전 ``ingest.archiver``).
"""

from __future__ import annotations

import os
import re

# registered_dest 가 붙이는 ``{asset_id}__{원본명}`` 프리픽스(UUIDv7 + '__')의 역패턴.
# 표시용 파일명 산출 시 이 프리픽스만 벗겨 원본 파일명을 복원한다(아카이브 이동으로 fs_path 가
# asset_id 프리픽스를 갖게 돼도 프론트·다운로드엔 원본명만 보이게 — 065 T605).
_ASSET_ID_PREFIX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}__"
)


def basename_of(uri: str) -> str:
    """경로/URI 에서 파일명만 추출한다(결정적·순수). URL 일 때만 쿼리(``?``)·프래그먼트(``#``) 제거.

    백슬래시를 ``/`` 로 정규화하고 후행 ``/`` 를 제거한 뒤 마지막 세그먼트를 취한다.
    쿼리·프래그먼트 제거는 **스킴(``://``)이 있는 URL 에 한정**한다 — 로컬 파일명에는 ``#``/``?`` 가
    문자 그대로 들어올 수 있어서다(유튜브 수집 파일명의 해시태그 ``#식혜…`` 를 프래그먼트로 오인해
    잘라내면 재색인 시 file_name 이 통째로 비는 실사고 — 2026-08-24, 9자산). URL 에서 쿼리를 떼고
    남는 게 없으면 마지막 세그먼트로 폴백한다(기존 3벌 공통 ``or tail``).
    asset_id 프리픽스는 벗기지 않는다(표시용 strip 은 별도 책임 — 모듈 docstring 참조).

    Args:
        uri: 로컬 경로 또는 URL. 빈 값이면 빈 문자열을 돌려준다.
    """
    if not uri:
        return ""
    tail = uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if "://" not in uri:
        return tail
    return tail.split("?", 1)[0].split("#", 1)[0] or tail


def strip_asset_id_prefix(name: str) -> str:
    """basename 앞의 ``{asset_id}__`` 프리픽스 제거(순수·결정적). ``registered_dest`` 역함수.

    프리픽스가 없으면(인입 원본 등) 원본 그대로 반환한다. UUID 프리픽스만 정확 매칭하므로
    원본명에 ``__`` 가 있어도(맨 앞이 UUID 형태가 아니면) 건드리지 않는다.
    """
    return _ASSET_ID_PREFIX.sub("", name or "")


def display_file_name(fs_path: str | None) -> str:
    """fs_path → 표시용 파일명 = basename 에서 archiver 프리픽스(``{asset_id}__``) 제거(순수).

    아카이브 이동 후 fs_path 가 ``.../{asset_id}__{원본명}`` 이어도 원본 파일명만 돌려준다.
    프론트 트리·자산 목록·다운로드 파일명이 asset_id 를 노출하지 않게 하는 단일 출처(065 T605).
    """
    if not fs_path:
        return ""
    return strip_asset_id_prefix(os.path.basename(fs_path))


# ── 판정 재료용 "뜻 있는 파일 이름" 게이트 ──────────────────────────────────────
# 기기·앱이 붙이는 상투 접두. 이걸 떼고 나면 숫자·날짜만 남는 이름들이라 통째로 버린다.
_DEVICE_PREFIX = re.compile(
    r"^(?:img|dsc|dscn|dcim|pic|photo|image|vid|video|mov|movie|screen ?shot|capture|"
    r"kakaotalk|download|untitled|new ?file|캡처|사진|스크린샷|제목\s*없음|무제|새\s*파일)"
    r"[ _\-]*",
    re.IGNORECASE,
)
# 뜻이 있다고 볼 최소선: 한글 2자 **또는** 라틴 3자 연속.
#   왜 라틴은 3자인가 — 2자로 두면 ``Screenshot 2024-03-15 at 14.22.31`` 의 ``at`` 같은 찌꺼기가
#   통과한다(실측 표본에서 나온 실패 사례). 한글은 2자로도 뜻을 갖는 말이 흔해 2자로 둔다.
_HANGUL_RUN = re.compile(r"[가-힣]{2,}")
_LATIN_RUN = re.compile(r"[A-Za-z]{3,}")


def meaningful_file_name(fs_path: str | None) -> str | None:
    """판정 재료로 쓸 만한 **뜻 있는 파일 이름**. 쓸 만하지 않으면 ``None``(순수).

    무엇에 쓰나: 개체 판정 프롬프트에 파일 이름을 참고로 실을지 말지를 **코드가 먼저** 정한다.
    LLM 에게 "이 이름이 뜻이 있나"를 묻지 않는 이유는, 뜻 없는 이름이 판정을 흔드는 것 자체를
    막고 싶기 때문이다(사용자 요구 2026-09-11: *"파일명과 상위 폴더명이 의미가 없는 경우 이게
    실제 판정에 큰영향을 주지 않도록"*). 게이트가 순수 함수라 단위 테스트로 봉인된다.

    판정 절차: 표시용 파일명(자산 id 접두 제거) → 확장자 제거 → 기기·앱 상투 접두 1회 제거 →
    남은 글자에 **한글 2자 이상 이어진 곳** 또는 **라틴 3자 이상 이어진 곳**이 있으면 통과.

    실측으로 걸러지는 것: ``1612816.jpg``·``2021042017434700.JPG``(출처 일련번호) ·
    ``IMG_4821.jpg`` · ``Screenshot 2024-03-15 at 14.22.31.png`` · ``KakaoTalk_20240315_1234.jpg``.
    통과하는 것: ``서울 숭례문.txt`` · ``[집중인터뷰] 배우 윤석화와 함께.mp4``.

    ⚠️ **접두 제거는 판정용이고, 돌려주는 값은 자르지 않은 이름(확장자만 뺀 것)이다** — LLM 에는
    맥락이 많을수록 좋고, 접두 제거는 "실을지 말지"를 정하는 데만 쓴다.

    Args:
        fs_path: 자산의 파일 경로(또는 파일명). 비어 있으면 ``None`` 을 돌려준다.

    Returns:
        확장자를 뗀 파일 이름. 뜻이 없다고 판정되면 ``None``.
    """
    name = display_file_name(fs_path)
    if not name:
        return None
    stem = os.path.splitext(name)[0].strip()
    if not stem:
        return None
    probe = _DEVICE_PREFIX.sub("", stem, count=1)
    if _HANGUL_RUN.search(probe) or _LATIN_RUN.search(probe):
        return stem
    return None
