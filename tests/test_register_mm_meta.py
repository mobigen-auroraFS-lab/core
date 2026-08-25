"""084 T013 — 멀티모달 메타 **등록 CLI**(`scripts/register_mm_meta.py`) 단위 테스트.

무엇을 검증하나: 사람이 메타를 손으로 등록하는 유일한 경로다(spec §6-1). 발굴 모드 기본값이
``propose`` 라 **등록된 메타에만** 배치가 자산을 붙이므로, 이 CLI 가 곧 후보 승인 수단이다.

여기서 지키는 것 넷:

    ① **어휘·표기 검사는 DB 보다 먼저**. 타입이 닫힌 5종 밖이거나 표기가 비면 커넥션을 잡기 전에
       종료코드 1 로 끝난다(잘못된 요청이 DB 를 열 이유가 없다).
    ② **dry-run 은 DB 를 아예 열지 않는다**. 정규화 결과만 보여 주므로 설정·DB 없이도 "무엇이
       등록될지"를 확인할 수 있다(``--apply`` 없이 실행하는 것이 기본 습관이 되게).
    ③ **표기가 유지된 사실을 알린다**. 기존 노드가 있으면 대표 표기는 안 바뀌는데(계약 ①), 조용히
       넘기면 "등록했는데 이름이 그대로다"를 다시 조사하게 된다.
    ④ 등록 뒤 **다음 배치가 채운다**는 안내 — 등록 직후 소속 0건은 정상이다(빈 메타 허용).

DB·네트워크 불필요 — 영속 함수는 주입 seam 으로 갈아 끼우고, 커넥션은 아래 ``_FakeDb`` 가 흉내낸다.
"""

from __future__ import annotations

import contextlib
import unittest
from typing import Any
from unittest import mock

import scripts.register_mm_meta as cli

from src.mm_meta.persist import MM_META_SOURCE_AUTO, MM_META_SOURCE_USER, normalize_registration


class _FakeDb:
    """``PostgresUtil`` 의 최소 형상 — 어느 통로가 열렸는지만 기록한다."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    @contextlib.contextmanager
    def _ctx(self, tag: str):  # noqa: ANN202 - 테스트 헬퍼
        self.opened.append(tag)
        yield object()

    def connection(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._ctx("connection")

    def transaction(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._ctx("transaction")


def _result(**over: Any) -> dict[str, Any]:
    """``register_mm_meta`` 반환 형상(더미).

    Args:
        **over: 덮어쓸 필드.

    Returns:
        결과 dict.
    """
    out = {"action": "registered", "node_id": "018f0000-0000-7000-8000-0000000000e1",
           "entity_type": "인물", "entity_uid": "아이유", "name": "아이유",
           "requested_name": "아이유", "aliases": ["이지은"], "added_aliases": ["이지은"],
           "source": MM_META_SOURCE_USER, "name_kept": False}
    out.update(over)
    return out


class TestParseArgs(unittest.TestCase):
    def test_등록_인자를_읽는다(self):
        args = cli._parse_args(["--type", "인물", "--name", "아이유",
                                "--alias", "이지은", "--alias", "IU", "--apply"])
        self.assertEqual((args.type, args.name), ("인물", "아이유"))
        self.assertEqual(args.alias, ["이지은", "IU"])
        self.assertTrue(args.apply)

    def test_별칭은_없어도_된다(self):
        args = cli._parse_args(["--type", "장소", "--name", "울릉도"])
        self.assertEqual(args.alias, [])
        self.assertFalse(args.apply)

    def test_조회_인자를_읽는다(self):
        args = cli._parse_args(["--list", "--source", "user", "--limit", "10"])
        self.assertTrue(args.list)
        self.assertEqual((args.source, args.limit), ("user", 10))

    def test_등록과_조회는_동시에_쓸_수_없다(self):
        with self.assertRaises(SystemExit):
            cli._parse_args(["--list", "--name", "아이유"])

    def test_모드를_주지_않으면_사용법과_함께_종료한다(self):
        with self.assertRaises(SystemExit):
            cli._parse_args(["--env", "dev"])


class TestMainValidation(unittest.TestCase):
    """① 검사는 DB 보다 먼저 · ② dry-run 은 DB 를 열지 않는다."""

    def test_어휘_밖_타입은_DB_없이_거부된다(self):
        # 닫힌 5종은 ``--type`` 의 choices 로 못 박혀 있어 argparse 가 사용법과 함께 즉시 끝낸다
        # (허용 값이 그 자리에서 보이는 편이 낫다). 계약 검사는 persist 가 이중으로 막는다.
        with mock.patch.object(cli, "_init_env") as init:
            with self.assertRaises(SystemExit):
                cli.main(["--type", "동물", "--name", "진돗개", "--apply"])
        init.assert_not_called()

    def test_빈_표기는_DB_없이_거부된다(self):
        with mock.patch.object(cli, "_init_env") as init:
            rc = cli.main(["--type", "장소", "--name", "   ", "--apply"])
        self.assertEqual(rc, 1)
        init.assert_not_called()

    def test_dry_run_은_DB_를_열지_않는다(self):
        with mock.patch.object(cli, "_init_env") as init:
            rc = cli.main(["--type", "인물", "--name", "아이유", "--alias", "이지은"])
        self.assertEqual(rc, 0)
        init.assert_not_called()

    def test_등록_모드에_타입이_없으면_거부된다(self):
        with mock.patch.object(cli, "_init_env") as init:
            rc = cli.main(["--name", "아이유"])
        self.assertEqual(rc, 1)
        init.assert_not_called()


class TestRunRegister(unittest.TestCase):
    def test_트랜잭션_안에서_영속을_부른다(self):
        db = _FakeDb()
        calls: list[dict[str, Any]] = []

        def fake_register(conn: Any, entity_type: str, name: str, *,
                          aliases: Any = ()) -> dict[str, Any]:
            calls.append({"entity_type": entity_type, "name": name, "aliases": list(aliases)})
            return _result()

        plan = normalize_registration("인물", "아이유", ["이지은"])
        out = cli.run_register(db, plan, register_fn=fake_register)

        self.assertEqual(out["action"], "registered")
        self.assertEqual(db.opened, ["transaction"])  # 쓰기이므로 커밋되는 통로여야 한다
        self.assertEqual(calls, [{"entity_type": "인물", "name": "아이유",
                                  "aliases": ["이지은"]}])


class TestRunList(unittest.TestCase):
    def test_읽기_통로만_쓴다(self):
        db = _FakeDb()
        rows = [{"entity_type": "인물", "entity_uid": "아이유", "name": "아이유",
                 "source": MM_META_SOURCE_USER, "aliases": ["이지은"]}]
        out = cli.run_list(db, source="user", limit=10, list_fn=lambda _c, **_k: rows)
        self.assertEqual(out, rows)
        self.assertEqual(db.opened, ["connection"])

    def test_필터와_상한을_그대로_넘긴다(self):
        db = _FakeDb()
        seen: dict[str, Any] = {}

        def fake_list(_conn: Any, **kw: Any) -> list[dict[str, Any]]:
            seen.update(kw)
            return []

        cli.run_list(db, source=None, limit=5, list_fn=fake_list)
        self.assertEqual(seen, {"source": None, "limit": 5})


class TestFormatting(unittest.TestCase):
    """출력 줄(순수) — 사람이 다음에 무엇을 할지 알 수 있어야 한다."""

    def test_dry_run_출력에_정규화_결과와_다음_명령이_있다(self):
        plan = normalize_registration("인물", " 아이유 ", ["이지은", "이 지 은"])
        text = "\n".join(cli.format_plan_lines(plan))
        self.assertIn("아이유", text)
        self.assertIn("이지은", text)
        self.assertIn("--apply", text)

    def test_등록_결과에_다음_배치_안내가_있다(self):
        text = "\n".join(cli.format_result_lines(_result()))
        self.assertIn("아이유", text)
        # 등록 직후 소속 0건은 정상이며 다음 배치가 채운다 — 안 적으면 "왜 비었나"를 다시 묻는다.
        self.assertIn("배치", text)

    def test_표기가_유지되면_그_사실을_알린다(self):
        text = "\n".join(cli.format_result_lines(
            _result(action="updated", name="아이유", requested_name="아이 유", name_kept=True)))
        self.assertIn("아이 유", text)
        self.assertIn("아이유", text)
        self.assertTrue("유지" in text or "그대로" in text, text)

    def test_변경_없음도_그대로_알린다(self):
        text = "\n".join(cli.format_result_lines(_result(action="unchanged", added_aliases=[])))
        self.assertIn("변경 없음", text)

    def test_목록_출력에_출처와_별칭이_있다(self):
        rows = [
            {"entity_type": "인물", "entity_uid": "아이유", "name": "아이유",
             "source": MM_META_SOURCE_USER, "aliases": ["이지은"]},
            {"entity_type": "장소", "entity_uid": "제주도", "name": "제주도",
             "source": MM_META_SOURCE_AUTO, "aliases": []},
        ]
        text = "\n".join(cli.format_list_lines(rows, source=None))
        self.assertIn(MM_META_SOURCE_USER, text)
        self.assertIn(MM_META_SOURCE_AUTO, text)
        self.assertIn("이지은", text)

    def test_목록이_비면_등록_방법을_알린다(self):
        text = "\n".join(cli.format_list_lines([], source="user"))
        self.assertIn("--name", text)


if __name__ == "__main__":
    unittest.main()
