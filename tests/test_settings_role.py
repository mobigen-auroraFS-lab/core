"""093 5단계 — 설정 초기화 **역할**(processing / serving) 단위 테스트.

무엇을 봉인하나: ① ``serving`` 은 적재 전용 필수 env 5개가 없어도 자리값으로 조립된다 ② ``processing``(기본)
은 종전과 같이 그 5개가 없으면 즉시 실패한다 ③ 서빙이라도 값이 주어지면 그 값을 읽는다(형식 검증 포함)
④ 서빙도 나머지 필수값(LLM 접속·임베딩 모델)은 요구한다 ⑤ 자리값은 ``.env.example`` 의 기본과 같다
⑥ 모르는 역할은 예외.

왜 자리값이 ``.env.example`` 과 같아야 하나: 두 곳이 어긋나면 "템플릿대로 채운 적재"와 "값 없이 뜬 서빙"이
같은 필드에서 다른 값을 보게 된다. 서빙은 그 값을 읽지 않으므로 실해는 없지만, 문서와 코드가 다른 숫자를
말하는 상태를 만들지 않는다.

DB·LLM 없음(순수 · env 만 조작).
"""

from __future__ import annotations

import contextlib
import inspect
import os
import re
import unittest
from pathlib import Path

from src.config import settings as settings_mod
from src.config.settings import (
    _FIELD_SPECS,
    _SERVING_EXEMPT_DEFAULTS,
    _build_settings,
    init_settings,
)

_FULL_REQUIRED = {
    "META_MODEL": "gemma",
    "OPENAI_BASE_URL": "http://localhost:1234/v1",
    "OPENAI_API_KEY": "sk-test",
    "SUMMARY_MAX_CHARS": "500",
    "TOP_K_KEYWORDS": "10",
    "CHUNK_SIZE": "1000",
    "OVERLAP_SIZE": "100",
    "ENCODING": "utf-8",
    "TEXT_EMBED_MODEL": "bge-m3",
    "TEXT_EMBED_CHUNK_SIZE": "512",
    "TEXT_EMBED_NORMALIZE": "true",
}
_INGEST_ONLY_ENV = ("ENCODING", "SUMMARY_MAX_CHARS", "TOP_K_KEYWORDS", "CHUNK_SIZE", "OVERLAP_SIZE")
_OPTIONAL_ENV = tuple(s.env for s in _FIELD_SPECS if not s.required)


@contextlib.contextmanager
def _env(present: dict[str, str]):
    """필수·선택 env 를 전부 걷어낸 뒤 ``present`` 만 넣고, 끝나면 원상복구한다."""
    touched = set(_FULL_REQUIRED) | set(_OPTIONAL_ENV) | set(present)
    saved = {k: os.environ.get(k) for k in touched}
    try:
        for k in touched:
            os.environ.pop(k, None)
        os.environ.update(present)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _without(keys: tuple[str, ...]) -> dict[str, str]:
    return {k: v for k, v in _FULL_REQUIRED.items() if k not in keys}


class TestServingRole(unittest.TestCase):
    """서빙 역할 — 적재 전용 5개 없이도 뜬다. 나머지 필수는 그대로."""

    def test_serving_builds_without_ingest_only_values(self) -> None:
        with _env(_without(_INGEST_ONLY_ENV)):
            s = _build_settings("dev", "serving")
        self.assertEqual(s.encoding, "utf-8")
        self.assertEqual(s.summary_max_chars, 500)
        self.assertEqual(s.top_k_keywords, 10)
        self.assertEqual(s.chunk_size, 1000)
        self.assertEqual(s.overlap_size, 100)
        self.assertEqual(s.meta_model, "gemma")  # 나머지는 env 에서 읽음

    def test_processing_still_fails_without_them(self) -> None:
        # 기본 역할은 종전과 같다 — 조용히 자리값을 넣지 않는다.
        with _env(_without(("CHUNK_SIZE",))), self.assertRaises(ValueError) as ctx:
            _build_settings("dev")
        self.assertIn("CHUNK_SIZE", str(ctx.exception))

    def test_serving_reads_values_when_present(self) -> None:
        # 서빙이라도 값이 있으면 자리값이 아니라 그 값(형식 검증도 종전과 같다).
        with _env({**_FULL_REQUIRED, "CHUNK_SIZE": "777", "ENCODING": "cp949"}):
            s = _build_settings("dev", "serving")
        self.assertEqual(s.chunk_size, 777)
        self.assertEqual(s.encoding, "cp949")
        with _env({**_FULL_REQUIRED, "CHUNK_SIZE": "abc"}), self.assertRaises(ValueError):
            _build_settings("dev", "serving")

    def test_serving_still_requires_llm_and_embedding_values(self) -> None:
        # 질의 임베딩·LLM 검증은 서빙도 쓴다 — 면제 대상이 아니다.
        for key in ("META_MODEL", "OPENAI_BASE_URL", "TEXT_EMBED_MODEL"):
            with self.subTest(key=key), _env(_without((key,))), self.assertRaises(ValueError) as ctx:
                _build_settings("dev", "serving")
            self.assertIn(key, str(ctx.exception))

    def test_unknown_role_is_rejected(self) -> None:
        with _env(_FULL_REQUIRED), self.assertRaises(ValueError):
            _build_settings("dev", "batch")  # type: ignore[arg-type]

    def test_serving_and_processing_agree_when_all_values_present(self) -> None:
        # 값이 전부 있으면 역할은 결과에 영향이 없다 — 백엔드가 명시적으로 serving 을 줘도 응답이 같은 이유.
        with _env(_FULL_REQUIRED):
            self.assertEqual(_build_settings("dev", "serving"), _build_settings("dev", "processing"))


class TestContracts(unittest.TestCase):
    """계약 모양 — 자리값 = .env.example 기본, init_settings 는 role 키워드 전용."""

    def test_placeholders_match_env_example(self) -> None:
        text = (Path(settings_mod.__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
        env_key = {s.attr: s.env for s in _FIELD_SPECS if s.group == ""}
        for attr, value in _SERVING_EXEMPT_DEFAULTS.items():
            m = re.search(rf"^{env_key[attr]}=(\S+)", text, flags=re.M)
            self.assertIsNotNone(m, f".env.example 에 {env_key[attr]} 가 없다")
            self.assertEqual(str(value), m.group(1), f"{env_key[attr]}: 자리값과 .env.example 기본이 다르다")

    def test_exempt_fields_are_required_common_specs(self) -> None:
        # 면제 목록은 상위 공통(group="")의 필수 필드 이름만 — 오타·그룹 필드 혼입을 막는다.
        common_required = {s.attr for s in _FIELD_SPECS if s.group == "" and s.required}
        self.assertTrue(set(_SERVING_EXEMPT_DEFAULTS) <= common_required)

    def test_init_settings_role_is_keyword_only_with_processing_default(self) -> None:
        p = inspect.signature(init_settings).parameters["role"]
        self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(p.default, "processing")


if __name__ == "__main__":
    unittest.main()
