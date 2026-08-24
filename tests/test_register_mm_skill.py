"""085 T107 — 분류 스킬 **등록 CLI**(``scripts/register_mm_skill.py``) 단위 테스트.

무엇을 검증하나: 등록은 두 단계다 — ``--preview N`` 으로 표본 판정을 사람이 눈으로 확인하고,
그 다음 ``--apply`` 로 등록한다. 이 두 단계가 **건너뛸 수 없게** 묶여 있는지가 핵심 검증 대상이다
(spec SC-02 · §3 등록 게이트).

왜 그런 게이트가 필요한가(파일럿 발견 1): 유일하게 의미 있던 오배정이 **정의문 문구** 탓이었다 —
정의문에 "영양 지식"이라고 적어 두자 판정이 그 문구에 충실하게 따라갔다. 즉 **정의문이 곧 분류기**
이고, 등록은 되돌리기 어렵다(행이 쌓이고 색인이 채워지고 화면 축이 생긴다). 그래서 "실제로 이 문구가
어떻게 분류하는지"를 먼저 보게 만든다.

게이트 방식(구현 결정): ``--preview`` 가 스킴 내용에서 파생한 **확인 코드**(sha256 앞 12자)를 출력하고,
``--apply`` 는 ``--confirm <코드>`` 로 그 값을 요구한다. 코드가 내용에서 나오므로 ① 미리보기 없이는
값을 알 수 없고 ② 미리보기 뒤 정의문을 고치면 코드가 달라져 **다시 확인**해야 한다(가장 중요한 성질).
파일 경로·시각·DB 상태와 무관해 재현 가능하다(결정적).

DB·LLM·네트워크 불필요(판정·조회·영속 seam 을 전부 주입한다).
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.register_mm_skill import (
    EXAMPLES_PER_LABEL,
    PreviewGateError,
    confirm_code,
    format_apply_lines,
    format_preview_lines,
    load_skill_file,
    main,
    require_confirm,
    run_apply,
    run_preview,
    sample_report,
)

from src.mm_classify.judge import JudgeFailure, SkillJudgement
from src.mm_classify.model import UNASSIGNED_LABEL_CODE, SkillConfigError, load_skill

_ASSET = "018f0000-0000-7000-8000-0000000000a1"


def _raw(**over: Any) -> dict[str, Any]:
    """정상 스킴 dict(값은 전부 더미 — 실 데이터 문구를 코드 레포에 넣지 않는다).

    Args:
        **over: 최상위 키 덮어쓰기.

    Returns:
        스킬 설정 dict.
    """
    raw: dict[str, Any] = {
        "skill": "샘플 분류",
        "skill_code": "sample_skill",
        "version": 1,
        "policy": {"selection": "multi", "unassigned": "해당없음", "max_labels": 30},
        "labels": [
            {
                "code": "alpha",
                "name": "가라벨",
                "definition": "가에 해당하는 내용이 중심인 자산",
                "not": "나에 해당하는 내용",
            },
            {
                "code": "beta",
                "name": "나라벨",
                "definition": "나에 해당하는 내용이 중심인 자산",
                "not": "가에 해당하는 내용",
            },
        ],
    }
    raw.update(over)
    return raw


def _skill(**over: Any):
    """검증을 통과한 더미 스킬.

    Args:
        **over: 최상위 키 덮어쓰기.

    Returns:
        ``ClassificationSkill``.
    """
    return load_skill(_raw(**over))


@contextlib.contextmanager
def _skill_file(raw: dict[str, Any] | None = None):
    """임시 스킴 JSON 파일을 만들고 경로를 준다(테스트 종료 시 삭제).

    Args:
        raw: 파일에 쓸 dict. ``None`` 이면 정상 스킴.

    Yields:
        파일 경로(``Path``).
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "skill.json"
        path.write_text(
            json.dumps(_raw() if raw is None else raw, ensure_ascii=False), encoding="utf-8"
        )
        yield path


def _ok(*codes: str) -> SkillJudgement:
    """성공 판정(라벨 코드만 지정).

    Args:
        *codes: label_code 들.

    Returns:
        ``ok=True`` 판정.
    """
    return SkillJudgement(ok=True, label_names=codes, label_codes=codes)


def _material(asset_id: str, summary: str = "요약 문장") -> dict[str, Any]:
    """판정 재료 한 건.

    Args:
        asset_id: 자산 id(더미).
        summary: 요약 문장.

    Returns:
        재료 dict.
    """
    return {"asset_id": asset_id, "summary": summary, "keywords": ["가키워드"]}


class _FakeDb:
    """``PostgresUtil`` 형상의 가짜 핸들 — ``connection()``·``transaction()`` 만 흉내낸다."""

    def __init__(self) -> None:
        self.used: list[str] = []

    @contextlib.contextmanager
    def connection(self):
        self.used.append("connection")
        yield object()

    @contextlib.contextmanager
    def transaction(self):
        self.used.append("transaction")
        yield object()


class TestLoadSkillFile(unittest.TestCase):
    """파일 읽기 + 설정 검증 fail-fast(등록 전에 거른다 · spec §3)."""

    def test_정상_파일을_읽는다(self) -> None:
        with _skill_file() as path:
            skill = load_skill_file(path)
        self.assertEqual(skill.skill_code, "sample_skill")
        self.assertEqual(skill.label_codes, ("alpha", "beta"))

    def test_결함_스킴은_거부한다(self) -> None:
        # 정의문 누락 = 라벨명만 등록(spec §1 금지) — 파일 단계에서 끊는다.
        bad = _raw(labels=[{"code": "alpha", "name": "가라벨", "not": "x"}])
        with _skill_file(bad) as path:
            with self.assertRaises(SkillConfigError):
                load_skill_file(path)

    def test_skill_code_없는_파일은_거부한다(self) -> None:
        bad = _raw()
        del bad["skill_code"]
        with _skill_file(bad) as path:
            with self.assertRaises(SkillConfigError):
                load_skill_file(path)


class TestConfirmCode(unittest.TestCase):
    """확인 코드 — **내용에서 파생**되므로 결정적이고, 내용이 바뀌면 값이 바뀐다."""

    def test_같은_스킴이면_같은_코드다(self) -> None:
        self.assertEqual(confirm_code(_skill()), confirm_code(_skill()))

    def test_코드는_12자_16진수다(self) -> None:
        code = confirm_code(_skill())
        self.assertEqual(len(code), 12)
        self.assertRegex(code, r"^[0-9a-f]{12}$")

    def test_정의문을_고치면_코드가_바뀐다(self) -> None:
        # 🔴 게이트의 핵심 — 미리보기 뒤 문구를 고치면 그 미리보기는 무효다(정의문이 곧 분류기).
        other = _skill(
            labels=[
                {
                    "code": "alpha",
                    "name": "가라벨",
                    "definition": "가에 해당하되 좁게 고친 정의",
                    "not": "나에 해당하는 내용",
                },
                {
                    "code": "beta",
                    "name": "나라벨",
                    "definition": "나에 해당하는 내용이 중심인 자산",
                    "not": "가에 해당하는 내용",
                },
            ]
        )
        self.assertNotEqual(confirm_code(_skill()), confirm_code(other))

    def test_정책을_고치면_코드가_바뀐다(self) -> None:
        single = _skill(policy={"selection": "single", "unassigned": "해당없음", "max_labels": 30})
        self.assertNotEqual(confirm_code(_skill()), confirm_code(single))

    def test_skill_code_가_다르면_코드가_바뀐다(self) -> None:
        self.assertNotEqual(confirm_code(_skill()), confirm_code(_skill(skill_code="other_skill")))

    def test_version_숫자만_다르면_같은_코드다(self) -> None:
        # 등록 내용(이름·정책·라벨)이 같으면 upsert 도 '변경 없음'으로 판정한다(persist 와 같은 기준) —
        # 버전 숫자만 고친 것으로 다시 미리보기를 강요하지 않는다.
        self.assertEqual(confirm_code(_skill()), confirm_code(_skill(version=9)))

    def test_공백_표기_차이는_같은_코드다(self) -> None:
        # 파일 서식(앞뒤 공백)은 분류를 바꾸지 않는다 — load_skill 이 strip 한 값으로 계산한다.
        self.assertEqual(confirm_code(_skill()), confirm_code(_skill(skill="  샘플 분류 ")))


class TestRequireConfirm(unittest.TestCase):
    """게이트 판정 — 미리보기를 건너뛴 apply 를 막는다(SC-02)."""

    def test_일치하면_통과한다(self) -> None:
        skill = _skill()
        require_confirm(skill, confirm_code(skill))  # 예외 없음

    def test_불일치는_거부한다(self) -> None:
        with self.assertRaises(PreviewGateError):
            require_confirm(_skill(), "deadbeef1234")

    def test_빈_값은_거부한다(self) -> None:
        # --confirm 없이 --apply = 미리보기를 안 본 것.
        with self.assertRaises(PreviewGateError):
            require_confirm(_skill(), "")

    def test_대소문자_공백은_흡수한다(self) -> None:
        # 콘솔에서 복사할 때 흔한 잡음만 흡수한다(값 자체를 느슨하게 보는 것이 아니다).
        skill = _skill()
        require_confirm(skill, f"  {confirm_code(skill).upper()} ")

    def test_사유에_다시_미리보기_안내가_있다(self) -> None:
        with self.assertRaises(PreviewGateError) as cm:
            require_confirm(_skill(), "nope")
        self.assertIn("--preview", str(cm.exception))


class TestSampleReport(unittest.TestCase):
    """표본 판정 요약 — 라벨 분포·해당없음 비율·실패 집계(결정적)."""

    def _report(self, verdicts: list[SkillJudgement], n: int | None = None) -> dict[str, Any]:
        """판정 결과를 순서대로 돌려주는 가짜 판정기로 요약을 만든다.

        Args:
            verdicts: 표본 순서대로 돌려줄 판정 목록.
            n: 표본 수. ``None`` 이면 판정 수와 같다.

        Returns:
            요약 dict.
        """
        materials = [_material(f"{_ASSET[:-1]}{i}") for i in range(n or len(verdicts))]
        seq = iter(verdicts)
        return sample_report(
            _skill(), materials, judge_fn=lambda _s, _sm, _kw: next(seq)
        )

    def test_라벨_건수를_센다(self) -> None:
        rep = self._report([_ok("alpha"), _ok("alpha", "beta"), _ok("beta")])
        self.assertEqual(rep["n_sample"], 3)
        self.assertEqual(rep["n_ok"], 3)
        self.assertEqual(rep["by_label"]["alpha"], 2)
        self.assertEqual(rep["by_label"]["beta"], 2)

    def test_라벨_분포는_설정_순서에_0건까지_담는다(self) -> None:
        # 한 건도 안 나온 라벨을 보여주는 것이 커버리지 갭 신호다(파일럿 발견 2).
        rep = self._report([_ok("alpha")])
        self.assertEqual(list(rep["by_label"]), ["alpha", "beta", UNASSIGNED_LABEL_CODE])
        self.assertEqual(rep["by_label"]["beta"], 0)

    def test_해당없음_비율은_성공_판정_기준이다(self) -> None:
        # 실패(판정 못 함)는 분모에서 뺀다 — 실패는 "해당 없음"이 아니라 재대상이다.
        rep = self._report(
            [
                _ok(UNASSIGNED_LABEL_CODE),
                _ok("alpha"),
                SkillJudgement(ok=False, failure=JudgeFailure.RESPONSE_SHAPE, detail="x"),
            ]
        )
        self.assertEqual(rep["n_unassigned"], 1)
        self.assertEqual(rep["n_ok"], 2)
        self.assertEqual(rep["n_failed"], 1)
        self.assertAlmostEqual(rep["unassigned_ratio"], 0.5)

    def test_실패_사유를_사유별로_센다(self) -> None:
        rep = self._report(
            [
                SkillJudgement(ok=False, failure=JudgeFailure.OUT_OF_VOCAB, detail="a"),
                SkillJudgement(ok=False, failure=JudgeFailure.OUT_OF_VOCAB, detail="b"),
                SkillJudgement(ok=False, failure=JudgeFailure.LABELS_EMPTY, detail="c"),
            ]
        )
        self.assertEqual(rep["failures"], {"out_of_vocab": 2, "labels_empty": 1})
        self.assertEqual(rep["by_label"]["alpha"], 0)

    def test_라벨별_예시를_상한까지_담는다(self) -> None:
        rep = self._report([_ok("alpha")] * (EXAMPLES_PER_LABEL + 2))
        self.assertEqual(len(rep["examples"]["alpha"]), EXAMPLES_PER_LABEL)
        first = rep["examples"]["alpha"][0]
        self.assertIn("asset_id", first)
        self.assertIn("summary", first)

    def test_표본이_없으면_0으로_요약한다(self) -> None:
        # 빈 DB·잘못된 --preview 값에서 0으로 나누지 않는다.
        rep = self._report([], n=0)
        self.assertEqual(rep["n_sample"], 0)
        self.assertEqual(rep["unassigned_ratio"], 0.0)
        self.assertEqual(rep["failures"], {})

    def test_같은_입력_두번이면_같은_요약(self) -> None:
        # 결정성(헌법 3조) — 같은 표본·같은 판정이면 같은 리포트.
        self.assertEqual(
            self._report([_ok("alpha"), _ok("beta")]),
            self._report([_ok("alpha"), _ok("beta")]),
        )

    def test_판정기에_스킬과_재료를_넘긴다(self) -> None:
        seen: list[tuple[Any, Any, Any]] = []

        def _spy(skill: Any, summary: Any, keywords: Any) -> SkillJudgement:
            seen.append((skill.skill_code, summary, keywords))
            return _ok("alpha")

        sample_report(_skill(), [_material(_ASSET, "특정 요약")], judge_fn=_spy)
        self.assertEqual(seen, [("sample_skill", "특정 요약", ["가키워드"])])


class TestFormatLines(unittest.TestCase):
    """콘솔 출력(순수) — 사람이 판단에 쓰는 정보와 다음 명령이 함께 나온다."""

    def _lines(self, *, batch_enabled: bool = True) -> list[str]:
        """미리보기 출력 줄을 만든다.

        Args:
            batch_enabled: 분류 배치 토글 상태(꺼져 있으면 경고 줄이 붙는다).

        Returns:
            출력 줄 목록.
        """
        skill = _skill()
        materials = [_material(_ASSET), _material(_ASSET[:-1] + "2")]
        seq = iter([_ok("alpha"), _ok(UNASSIGNED_LABEL_CODE)])
        rep = sample_report(skill, materials, judge_fn=lambda _s, _sm, _kw: next(seq))
        return format_preview_lines(
            skill, rep, confirm=confirm_code(skill), batch_enabled=batch_enabled
        )

    def test_확인_코드와_다음_명령을_보여준다(self) -> None:
        text = "\n".join(self._lines())
        code = confirm_code(_skill())
        self.assertIn(code, text)
        self.assertIn("--apply", text)
        self.assertIn(f"--confirm {code}", text)

    def test_라벨_분포와_해당없음_비율을_보여준다(self) -> None:
        text = "\n".join(self._lines())
        self.assertIn("가라벨", text)
        self.assertIn("나라벨", text)  # 0건 라벨도 노출(커버리지 갭)
        self.assertIn("해당없음", text)
        self.assertIn("50.0%", text)

    def test_배치_토글이_꺼졌으면_경고한다(self) -> None:
        # 등록은 되지만 판정이 돌지 않는다 — 조용히 두면 "패싯이 왜 비었나"를 다시 조사하게 된다.
        self.assertIn("MM_CLASSIFY_ENABLED", "\n".join(self._lines(batch_enabled=False)))
        self.assertNotIn("MM_CLASSIFY_ENABLED", "\n".join(self._lines(batch_enabled=True)))

    def test_등록_결과를_행동별로_보여준다(self) -> None:
        registered = format_apply_lines(
            {
                "action": "registered",
                "skill_id": "sid",
                "skill_code": "sample_skill",
                "version": 1,
                "previous_version": None,
                "declared_version": 1,
            }
        )
        self.assertIn("등록", "\n".join(registered))
        revised = format_apply_lines(
            {
                "action": "revised",
                "skill_id": "sid",
                "skill_code": "sample_skill",
                "version": 5,
                "previous_version": 4,
                "declared_version": 2,
            }
        )
        text = "\n".join(revised)
        self.assertIn("개정", text)
        self.assertIn("4", text)
        self.assertIn("5", text)
        # 파일 선언 버전과 DB 버전이 다르면 그 사실을 알린다(정본은 DB 카운터).
        self.assertIn("2", text)
        unchanged = format_apply_lines(
            {
                "action": "unchanged",
                "skill_id": "sid",
                "skill_code": "sample_skill",
                "version": 3,
                "previous_version": 3,
                "declared_version": 3,
            }
        )
        self.assertIn("변경 없음", "\n".join(unchanged))


class TestRunPreview(unittest.TestCase):
    """미리보기 배선 — 표본 조회·판정 seam 주입(DB·LLM 0)."""

    def test_표본_수를_조회에_그대로_넘긴다(self) -> None:
        seen: dict[str, Any] = {}

        def _materials(conn: Any, *, limit: int) -> list[dict[str, Any]]:
            seen["limit"] = limit
            return [_material(_ASSET)]

        db = _FakeDb()
        rep = run_preview(
            db,
            _skill(),
            limit=25,
            judge_fn=lambda _s, _sm, _kw: _ok("alpha"),
            materials_fn=_materials,
        )
        self.assertEqual(seen["limit"], 25)
        self.assertEqual(rep["n_sample"], 1)
        self.assertEqual(db.used, ["connection"])  # 읽기 전용 경로


class TestRunApply(unittest.TestCase):
    """등록 배선 — 게이트를 통과해야 트랜잭션을 연다."""

    def test_코드가_맞으면_upsert_한다(self) -> None:
        skill = _skill()
        db = _FakeDb()
        calls: list[Any] = []

        def _upsert(conn: Any, s: Any) -> dict[str, Any]:
            calls.append(s.skill_code)
            return {"action": "registered", "skill_id": "sid", "skill_code": s.skill_code,
                    "version": 1, "previous_version": None, "declared_version": 1}

        out = run_apply(db, skill, confirm=confirm_code(skill), upsert_fn=_upsert)
        self.assertEqual(out["action"], "registered")
        self.assertEqual(calls, ["sample_skill"])
        self.assertEqual(db.used, ["transaction"])  # 한 트랜잭션에서 커밋

    def test_코드가_틀리면_DB_를_열지_않는다(self) -> None:
        db = _FakeDb()
        with self.assertRaises(PreviewGateError):
            run_apply(db, _skill(), confirm="deadbeef1234", upsert_fn=lambda *_a: {})
        self.assertEqual(db.used, [])


class TestMainGate(unittest.TestCase):
    """CLI 종료 코드 — 게이트 위반은 **환경·DB 를 건드리기 전에** 끊는다.

    순서가 중요하다: 확인 코드 검사가 ``init_settings``·DB 접속보다 뒤에 있으면, 게이트에 걸릴 호출이
    먼저 커넥션을 열고 설정을 요구한다(테스트도 DB 없이는 못 돈다).
    """

    def _main(self, argv: list[str]) -> int:
        """콘솔 출력을 삼키고 main 을 돌린다(테스트 로그를 더럽히지 않게).

        Args:
            argv: CLI 인자 목록.

        Returns:
            종료 코드.
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return main(argv)

    def test_confirm_없는_apply_는_2를_준다(self) -> None:
        with _skill_file() as path:
            self.assertEqual(self._main([str(path), "--apply"]), 2)

    def test_confirm_이_틀리면_2를_준다(self) -> None:
        with _skill_file() as path:
            self.assertEqual(self._main([str(path), "--apply", "--confirm", "deadbeef1234"]), 2)

    def test_결함_스킴은_1을_준다(self) -> None:
        bad = _raw(policy={"selection": "many", "unassigned": "해당없음"})
        with _skill_file(bad) as path:
            self.assertEqual(self._main([str(path), "--preview", "5"]), 1)

    def test_모드를_안_주면_argparse_가_막는다(self) -> None:
        with _skill_file() as path:
            with self.assertRaises(SystemExit):
                self._main([str(path)])

    def test_두_모드를_함께_주면_막는다(self) -> None:
        with _skill_file() as path:
            with self.assertRaises(SystemExit):
                self._main([str(path), "--preview", "5", "--apply"])

    def test_표본_0건_미리보기는_막는다(self) -> None:
        # 표본 0건은 "분포가 비었다"를 정상처럼 보여 준다 — 게이트의 뜻이 사라진다.
        with _skill_file() as path:
            with self.assertRaises(SystemExit):
                self._main([str(path), "--preview", "0"])

    def test_표본_수가_정수가_아니면_막는다(self) -> None:
        with _skill_file() as path:
            with self.assertRaises(SystemExit):
                self._main([str(path), "--preview", "백건"])


if __name__ == "__main__":
    unittest.main()
