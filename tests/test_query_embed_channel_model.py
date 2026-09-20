"""질의 임베딩이 **채널의 모델**로 해소되는가 — 문서 쪽과 같은 공간이어야 한다.

왜 이걸 못 박나(2026-09-21 실측): 문서 임베딩(``embed_texts_for``)은 api·로컬 **양쪽 모두**
``model_for_channel(channel)`` 로 모델을 정한다. 그런데 질의 임베딩은 **api 분기에서만** 채널을
따르고, 로컬 분기에서는 ``model_name`` 이 없으면 ``cfg.embed.model``(설정 기본 모델)로 떨어진다.

두 값은 실제로 다르다 — 채널 ``st_bge`` 의 모델은 bge 계열인데 설정 기본 모델은 KoSimCSE 다.
활성 채널이 로컬 bge 인 배치에서는 **문서는 bge, 질의는 KoSimCSE** 가 되어 코사인이 뜻을 잃는다.
오류는 나지 않는다 — 점수가 통째로 낮아져 의미 검색이 조용히 죽는다(실제로 이 함정에 빠져
"벡터가 깨졌다"고 오판할 뻔했다).

지금 운영 채널은 api 라 드러나지 않는다. **지금 맞는 것이 아니라 채널을 바꿔도 맞아야** 한다.
"""
from __future__ import annotations

import contextlib
import os
import unittest
from collections.abc import Iterator
from unittest.mock import patch

from src.config.settings import _build_settings, model_for_channel
from src.search import query_embed

_REQUIRED_ENV = {
    "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_DB": "d",
    "POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p",
    "META_MODEL": "gemma", "OPENAI_BASE_URL": "http://localhost:1234/v1",
    "OPENAI_API_KEY": "sk-test", "SUMMARY_MAX_CHARS": "500", "TOP_K_KEYWORDS": "10",
    "CHUNK_SIZE": "1000", "OVERLAP_SIZE": "100", "ENCODING": "utf-8",
    "TEXT_EMBED_MODEL": "BM-K/KoSimCSE-roberta-multitask",
    "TEXT_EMBED_CHUNK_SIZE": "512", "TEXT_EMBED_NORMALIZE": "true",
}


@contextlib.contextmanager
def _env(**extra: str) -> Iterator[None]:
    """필수 환경변수를 잠깐 세워 설정을 만들 수 있게 한다(끝나면 원복)."""
    touched = list(_REQUIRED_ENV) + list(extra)
    saved = {k: os.environ.get(k) for k in touched}
    try:
        os.environ.update(_REQUIRED_ENV)
        os.environ.update(extra)
        yield
    finally:
        for k in touched:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]


def _settings(**extra: str) -> object:
    """테스트용 설정 객체."""
    with _env(**extra):
        return _build_settings("dev")


class TestQueryModelFollowsChannel(unittest.TestCase):
    def test_로컬_채널이면_그_채널의_모델로_임베딩한다(self) -> None:
        """🔴 핵심 — 설정 기본 모델로 떨어지면 문서와 다른 공간이 된다."""
        cfg = _settings()
        want = model_for_channel("st_bge", cfg)
        self.assertNotEqual(want, cfg.embed.model,
                            "전제가 깨졌다 — 두 값이 같으면 이 시험이 아무것도 막지 못한다")
        with patch.object(query_embed, "get_current_settings", return_value=cfg), \
             patch.object(query_embed, "embed_texts", return_value=[[1.0]]) as local:
            query_embed.embed_query_for_media_search("왕실 무덤", channel="st_bge")
        self.assertEqual(local.call_args.kwargs.get("model_name"), want)

    def test_모델을_직접_주면_그것을_쓴다(self) -> None:
        """명시 전달이 채널 해소보다 우선이다(기존 계약 유지)."""
        cfg = _settings()
        with patch.object(query_embed, "get_current_settings", return_value=cfg), \
             patch.object(query_embed, "embed_texts", return_value=[[1.0]]) as local:
            query_embed.embed_query_for_media_search(
                "왕실 무덤", model_name="X/custom", channel="st_bge")
        self.assertEqual(local.call_args.kwargs.get("model_name"), "X/custom")

    def test_채널이_없으면_종전대로_설정_기본_모델(self) -> None:
        """회귀 — 채널을 안 주는 옛 호출부의 동작은 바뀌지 않는다."""
        cfg = _settings()
        with patch.object(query_embed, "get_current_settings", return_value=cfg), \
             patch.object(query_embed, "embed_texts", return_value=[[1.0]]) as local:
            query_embed.embed_query_for_media_search("왕실 무덤")
        self.assertEqual(local.call_args.kwargs.get("model_name"), cfg.embed.model)

    def test_api_채널은_채널_경로로_간다(self) -> None:
        """회귀 — api 분기는 종전과 같다(모델을 채널이 정한다)."""
        cfg = _settings(EMBED_API_BASE_URL="http://x/v1")
        with patch.object(query_embed, "get_current_settings", return_value=cfg), \
             patch.object(query_embed, "embed_texts_for", return_value=[[1.0]]) as api, \
             patch.object(query_embed, "embed_texts") as local:
            query_embed.embed_query_for_media_search("왕실 무덤", channel="st_api")
        api.assert_called_once()
        local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
