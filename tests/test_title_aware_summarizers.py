"""spec 114 — 요약기 4종의 ``title=`` 배선 단위 테스트(LLM · settings 는 대역).

무엇을 봉인하나
① ``title`` 을 주지 않으면 LLM 에 가는 프롬프트가 **글자 그대로 지금과 같고** 반환에 ``title_match`` 가 없다 —
   파이프가 넘기기 전까지 어떤 동작도 바뀌지 않는다(하위 호환).
② ``title`` 을 주면 제목 블록이 본문 앞(정해진 표지 앞)에 한 번 들어가고, 응답의 ``title_match`` 가 해석돼 돌아온다.
   사진 · 영상만 「외형 묘사 유지」 줄이 붙는다.
③ 빈 STT 음성 + 제목이면 제목만으로 최소 요약을 만든다(``title_only``) — 제목이 없으면 지금처럼 LLM 을 부르지 않는다.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import src.llm.image_summarizer as IS
import src.llm.text_summarizer as TS
import src.llm.video_summarizer as VS
from src.llm.title_hint import build_title_block, build_title_only_prompt

_TITLE = "[리뷰] 영화 조디악"


def _cfg(*, v2: bool = True) -> mock.Mock:
    cfg = mock.Mock()
    cfg.vlm.summary_prompt_v2 = v2
    cfg.top_k_keywords = 5
    cfg.summary_max_chars = 1000
    cfg.encoding = "utf-8"
    cfg.chunk_size = 10000
    cfg.overlap_size = 0
    return cfg


def _reply(**extra):
    return {"summary": "요약", "keywords": ["가", "나"], "objects": ["물체"], **extra}


class ImageTest(unittest.TestCase):
    def _run(self, title, reply):
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jpg"
            from PIL import Image
            Image.new("RGB", (8, 8)).save(p)
            with mock.patch.object(IS, "get_current_settings", return_value=cfg), \
                    mock.patch.object(IS, "complete_vision_json", return_value=reply) as m:
                out = IS.summarize_image_caption_keywords_objects(p, title=title)
        return out, m.call_args.kwargs["text"], cfg

    def test_제목이_없으면_프롬프트_그대로_title_match_없음(self) -> None:
        out, prompt, cfg = self._run(None, _reply(title_match="yes"))
        self.assertEqual(IS._build_image_caption_prompt(summary_max_chars=1000, top_k_keywords=5, v2=True), prompt)
        self.assertNotIn("title_match", out)

    def test_제목이_있으면_블록_외형유지_title_match(self) -> None:
        out, prompt, _ = self._run(_TITLE, _reply(title_match="YES"))
        block = build_title_block(_TITLE, keep_appearance=True)
        self.assertEqual(1, prompt.count(block))
        self.assertLess(prompt.index(block), prompt.index("개수/비율/합계"))
        self.assertEqual("yes", out["title_match"])
        self.assertEqual("요약", out["summary"])

    def test_title_match_가_이상하면_unsure(self) -> None:
        out, _, _ = self._run(_TITLE, _reply(title_match="아마도"))
        self.assertEqual("unsure", out["title_match"])


class VideoTest(unittest.TestCase):
    _SCENES = [{"scene_index": 0, "start_sec": 0.0, "end_sec": 1.0, "frame_sec": 0.5,
                "summary": {"summary": "스튜디오 대화", "keywords": ["대화"], "objects": ["의자"]}}]

    def _run(self, title, reply):
        with mock.patch.object(VS, "get_current_settings", return_value=_cfg()), \
                mock.patch.object(VS, "complete_json", return_value=reply) as m:
            out = VS.summarize_video_from_scene_results(self._SCENES, title=title)
        return out, m.call_args.args[0]

    def test_제목이_없으면_프롬프트_그대로(self) -> None:
        out, prompt = self._run(None, _reply(title_match="yes"))
        line = "[scene 0] 0.00s~1.00s (대표 0.50s): summary=스튜디오 대화 | keywords=대화 | objects=의자"
        self.assertEqual(VS._build_video_summary_prompt([line], summary_max_chars=1000, top_k_keywords=5, v2=True), prompt)
        self.assertNotIn("title_match", out)

    def test_제목이_있으면_장면_결과_앞에_블록(self) -> None:
        out, prompt = self._run(_TITLE, _reply(title_match="no"))
        block = build_title_block(_TITLE, keep_appearance=True)
        self.assertLess(prompt.index(block), prompt.index("장면 결과:"))
        self.assertEqual("no", out["title_match"])

    def test_장면이_없으면_제목이_있어도_빈_결과(self) -> None:
        with mock.patch.object(VS, "get_current_settings", return_value=_cfg()), \
                mock.patch.object(VS, "complete_json") as m:
            out = VS.summarize_video_from_scene_results([], title=_TITLE)
        m.assert_not_called()
        self.assertEqual("", out["summary"])


class AudioTest(unittest.TestCase):
    _STT = "오늘은 영화 조디악 이야기를 해 보겠습니다 연쇄살인 미제 사건을 다룬 작품입니다"

    def _run(self, stt, title, reply):
        with mock.patch.object(TS, "get_current_settings", return_value=_cfg()), \
                mock.patch.object(TS, "complete_json", return_value=reply) as m:
            out = TS.summarize_and_extract_keywords_from_audio(stt, title=title)
        return out, m

    def test_제목이_없으면_프롬프트_그대로(self) -> None:
        out, m = self._run(self._STT, None, _reply(title_match="yes"))
        self.assertEqual(TS._build_audio_prompt(self._STT, summary_max_chars=1000, top_k_keywords=5), m.call_args.args[0])
        self.assertNotIn("title_match", out)

    def test_제목이_있으면_텍스트_앞에_블록_외형줄_없음(self) -> None:
        out, m = self._run(self._STT, _TITLE, _reply(title_match="yes"))
        prompt = m.call_args.args[0]
        block = build_title_block(_TITLE, keep_appearance=False)
        self.assertLess(prompt.index(block), prompt.index("\n텍스트:"))
        self.assertEqual("yes", out["title_match"])
        self.assertEqual(self._STT, out["stt"])

    def test_빈_STT_와_제목이면_제목만으로_요약(self) -> None:
        out, m = self._run("", "심수봉 - 부부행진곡", {"summary": "심수봉의 부부행진곡", "keywords": ["심수봉"]})
        self.assertEqual(build_title_only_prompt("심수봉 - 부부행진곡", summary_max_chars=1000, top_k_keywords=5),
                         m.call_args.args[0])
        self.assertEqual("심수봉의 부부행진곡", out["summary"])
        self.assertTrue(out["title_only"])
        self.assertEqual("unsure", out["title_match"])
        self.assertEqual("", out["stt"])

    def test_빈_STT_에_제목도_없으면_LLM_을_부르지_않는다(self) -> None:
        out, m = self._run("", None, _reply())
        m.assert_not_called()
        self.assertEqual({"summary": "", "keywords": [], "stt": ""}, out)


class TextDocTest(unittest.TestCase):
    def _run(self, title, replies):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_text("경주 불국사 다보탑은 통일신라 시대의 석탑이다.", encoding="utf-8")
            with mock.patch.object(TS, "get_current_settings", return_value=_cfg()), \
                    mock.patch.object(TS, "complete_json", side_effect=replies) as m:
                out = TS.summarize_and_extract_keywords(p, file_kind="txt", title=title)
        return out, [c.args[0] for c in m.call_args_list]

    def test_제목이_없으면_프롬프트_그대로(self) -> None:
        out, prompts = self._run(None, [{"summary": "청크 요약"}, _reply(title_match="yes")])
        self.assertEqual(TS._build_chunk_summary_prompt("경주 불국사 다보탑은 통일신라 시대의 석탑이다.", summary_max_chars=1000),
                         prompts[0])
        self.assertEqual(TS._build_reduce_prompt("- 청크 요약", summary_max_chars=1000, top_k_keywords=5), prompts[1])
        self.assertNotIn("title_match", out)

    def test_제목이_있으면_청크와_종합_모두_블록_title_match_는_종합에서(self) -> None:
        out, prompts = self._run("경주 불국사 다보탑", [{"summary": "청크 요약", "title_match": "no"},
                                                        _reply(title_match="yes")])
        block = build_title_block("경주 불국사 다보탑", keep_appearance=False)
        self.assertLess(prompts[0].index(block), prompts[0].index("\n텍스트:"))
        self.assertLess(prompts[1].index(block), prompts[1].index("\n청크 요약 목록:"))
        self.assertEqual("yes", out["title_match"])


if __name__ == "__main__":
    unittest.main()
