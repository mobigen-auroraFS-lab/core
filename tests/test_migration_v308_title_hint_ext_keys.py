"""v308 title_hint_ext_keys 마이그레이션 테스트 (코어 — DML 파일 · 값 스키마 · 리비전 체인 · DB 불요).

무엇을 봉인하나: 요약기의 제목 판단(``title_match``) · 제목만 요약 표시(``title_only``)를 ext_meta 에 쓸 수 있게
등록 표(``ext_meta_field_registry``)에 키 두 개를 더하는 마이그레이션이 아래 계약을 지키는지 본다.
    ① 일반 · 의료 두 도메인 × 키 두 개 — 하나라도 빠지면 그 도메인 적재가 「미등록 키」로 막힌다.
    ② 멱등 — ``ON CONFLICT (domain, meta_key) DO NOTHING``(재실행 안전) · 문장은 INSERT 하나뿐.
    ③ 값 스키마가 실제로 검증한다 — ``title_match`` 는 yes · no · unsure 만, ``title_only`` 는 참 · 거짓만.
    ④ 가역 — downgrade 는 이 행들만 지운다. 체인은 v307 다음 · 리비전 ID 32자 이하.
v307 테스트와 같은 방식(함수 본문은 ``ast`` · SQL 은 주석을 뺀 허용 목록).
"""
from __future__ import annotations

import ast
import json
import os
import re
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SQL_PATH = os.path.join(_REPO_ROOT, "migrations", "sql", "308_title_hint_ext_keys.sql")
_ALEMBIC_PATH = os.path.join(_REPO_ROOT, "migrations", "alembic", "versions", "v308_title_hint_ext_keys.py")


def _read(path: str) -> str:
    """파일 전문을 읽는다.

    Args:
        path: 읽을 파일 경로.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _statements(sql: str) -> list[str]:
    """주석을 뺀 SQL 을 문장 단위로(공백 정규화 · 소문자).

    Args:
        sql: SQL 원문.
    """
    flat = " ".join("\n".join(line.split("--", 1)[0] for line in sql.splitlines()).lower().split())
    return [st.strip() for st in flat.split(";") if st.strip()]


def _calls(src: str, func: str) -> list[ast.Call]:
    """모듈 소스에서 함수 ``func`` 본문의 호출 노드.

    Args:
        src: 파이썬 소스.
        func: 함수 이름.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    return []


def _name(call: ast.Call) -> str:
    """호출 대상 이름.

    Args:
        call: 호출 노드.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class TestMigrationV308SQL(unittest.TestCase):
    def test_문장은_멱등_INSERT_하나(self) -> None:
        sts = _statements(_read(_SQL_PATH))
        self.assertEqual(1, len(sts))
        st = sts[0]
        self.assertTrue(st.startswith("insert into ext_meta_field_registry"))
        self.assertIn("on conflict (domain, meta_key) do nothing", st)
        for bad in ("delete", "update ", "alter ", "drop ", "truncate"):
            self.assertNotIn(bad, st)

    def test_두_도메인_두_키(self) -> None:
        st = _statements(_read(_SQL_PATH))[0]
        for word in ("'general'", "'medical'", "'title_match'", "'title_only'", "'active'"):
            self.assertIn(word, st)

    def test_열람_등급은_도메인의_summary_와_같다(self) -> None:
        # general = authenticated · medical = regulated(v290 관례) — 의료에 낮은 등급을 넣으면 하한 정책이 바뀔 때 샌다.
        st = _statements(_read(_SQL_PATH))[0]
        self.assertIn("('general', 'authenticated')", st)
        self.assertIn("('medical', 'regulated')", st)
        self.assertIn("d.tier", st)

    def test_값_스키마가_실제로_검증한다(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:  # pragma: no cover
            self.skipTest("jsonschema 미설치")
        sql = _read(_SQL_PATH)
        schemas = {k: json.loads(v) for k, v in re.findall(r"\('(title_match|title_only)',\s*'(\{[^']*\})'", sql)}
        self.assertEqual({"title_match", "title_only"}, set(schemas))
        tm, to = Draft202012Validator(schemas["title_match"]), Draft202012Validator(schemas["title_only"])
        for ok in ("yes", "no", "unsure"):
            self.assertTrue(tm.is_valid(ok))
        for bad in ("maybe", "", 1, None):
            self.assertFalse(tm.is_valid(bad))
        self.assertTrue(to.is_valid(True))
        self.assertTrue(to.is_valid(False))
        for bad in ("true", 1, 0, None):
            self.assertFalse(to.is_valid(bad))


class TestMigrationV308Alembic(unittest.TestCase):
    def test_체인(self) -> None:
        src = _read(_ALEMBIC_PATH)
        self.assertRegex(src, r'(?m)^revision\s*=\s*"v308_title_hint_ext_keys"')
        self.assertRegex(src, r'(?m)^down_revision\s*=\s*"v307_entity_label_hash"')
        rev = re.search(r'(?m)^revision\s*=\s*"([^"]+)"', src).group(1)
        self.assertLessEqual(len(rev), 32)

    def test_upgrade_는_SQL_파일_하나(self) -> None:
        calls = [c for c in _calls(_read(_ALEMBIC_PATH), "upgrade") if _name(c) == "run_sql_file"]
        self.assertEqual(1, len(calls))
        self.assertEqual(["308_title_hint_ext_keys.sql"], [a.value for a in calls[0].args if isinstance(a, ast.Constant)])

    def test_downgrade_는_이_행들만_지운다(self) -> None:
        calls = _calls(_read(_ALEMBIC_PATH), "downgrade")
        self.assertEqual(1, len(calls))
        self.assertEqual("execute", _name(calls[0]))
        sql = " ".join("".join(a.value for a in ast.walk(calls[0].args[0]) if isinstance(a, ast.Constant)).lower().split())
        self.assertEqual("delete from ext_meta_field_registry where domain in ('general', 'medical') "
                         "and meta_key in ('title_match', 'title_only')", sql)


if __name__ == "__main__":
    unittest.main()
