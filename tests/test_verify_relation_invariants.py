"""081 SC-08 ② 전수 기계 불변식 검사의 순수부 — 검사 목록·읽기전용성·판정.

이 도구가 지키는 것: 전량 재실행 뒤 "게이트가 실제로 지켜졌나"를 **표본이 아니라 전 행**에서
기계적으로 확인한다. 내용(관계가 타당한가)은 골든·층화 표본이 보고, 여기서는 **규칙 위반 0**만 본다.
"""
from __future__ import annotations

import unittest

from scripts.verify_relation_invariants import (
    CHECK_NAMES,
    MM_MEMBER_CHECK_NAMES,
    build_checks,
    run_verify,
)

from src.relations.schema import MM_MEMBER_KIND_CODE

_EXCLUDE = frozenset({"same_domain"})

# 084 전 검사 목록 — 기존 검사 이름·순서가 **그대로 앞에** 남아야 한다(운영 보고 줄·비교 대상).
_LEGACY_CHECK_NAMES = (
    "유사도_계열_저신뢰_잔존",
    "자동승인_꺼졌는데_기계승인_active",
    "자동승인_제외_kind가_active",
    "자기참조_엣지",
    "닫힌_status_어휘_위반",
    "대칭_중복행",
    "대칭_캐논순서_위반",
    "비활성_kind_엣지",
    "비-asset_노드_참조",
)


class TestCheckList(unittest.TestCase):
    def test_선언된_검사를_모두_만든다(self):
        names = [c["name"] for c in build_checks(min_conf_similarity=0.75,
                                                 exclude_kinds=_EXCLUDE,
                                                 auto_approve_min=1.01)]
        self.assertEqual(names, list(CHECK_NAMES))

    def test_검사_이름이_중복되지_않는다(self):
        names = [c["name"] for c in build_checks(min_conf_similarity=0.75,
                                                 exclude_kinds=_EXCLUDE,
                                                 auto_approve_min=1.01)]
        self.assertEqual(len(names), len(set(names)))

    def test_모든_검사가_읽기_전용이다(self):
        # 검증 도구가 DB 를 바꾸면 "검증했더니 통과"가 자기충족이 된다.
        for c in build_checks(min_conf_similarity=0.75, exclude_kinds=_EXCLUDE,
                                 auto_approve_min=1.01):
            with self.subTest(c["name"]):
                head = c["sql"].strip().upper()
                self.assertTrue(head.startswith(("SELECT", "WITH")), head[:40])
                for w in ("INSERT", "UPDATE ", "DELETE", "DROP ", "ALTER ", "TRUNCATE"):
                    self.assertNotIn(w, c["sql"].upper(), f"{c['name']} 에 쓰기 키워드")

    def test_모든_검사가_카운트_하나를_돌려준다(self):
        # run_verify 가 행 모양을 가정하므로 계약을 고정한다.
        for c in build_checks(min_conf_similarity=0.75, exclude_kinds=_EXCLUDE,
                                 auto_approve_min=1.01):
            with self.subTest(c["name"]):
                self.assertIn("AS n", c["sql"])

    def test_노드_조인에_asset_가드가_있다(self):
        # entity 노드는 asset_id 가 NULL 이라 가드 없이 조인하면 None 이 섞인다(레포 관례).
        for c in build_checks(min_conf_similarity=0.75, exclude_kinds=_EXCLUDE,
                                 auto_approve_min=1.01):
            if "JOIN node" in c["sql"] or "node n" in c["sql"]:
                with self.subTest(c["name"]):
                    self.assertIn("node_kind", c["sql"])

    def test_임계와_제외목록이_SQL_에_반영된다(self):
        checks = {c["name"]: c for c in build_checks(min_conf_similarity=0.5,
                                                     exclude_kinds=frozenset({"references"}))}
        self.assertIn(0.5, checks["유사도_계열_저신뢰_잔존"]["params"])
        self.assertIn(["references"], checks["자동승인_제외_kind가_active"]["params"])

    def test_제외목록이_비면_그_검사는_건너뛴다(self):
        # 게이트를 끈 설정에서 "위반"을 보고하면 거짓 경보다.
        names = [c["name"] for c in build_checks(min_conf_similarity=0.0,
                                                 exclude_kinds=frozenset())]
        self.assertNotIn("자동승인_제외_kind가_active", names)
        self.assertNotIn("유사도_계열_저신뢰_잔존", names)

    def test_제외목록은_정렬돼_바인딩된다(self):
        checks = {c["name"]: c for c in build_checks(
            min_conf_similarity=0.75, exclude_kinds=frozenset({"same_domain", "duplicate_near"}))}
        self.assertIn(["duplicate_near", "same_domain"],
                      checks["자동승인_제외_kind가_active"]["params"])


class TestMmMemberAxis(unittest.TestCase):
    """084 착수 전 결정 ① — 소속 엣지(``mm_member``)는 **별도 검사 축**이다.

    왜 축을 갈랐나: 기존 ``비-asset_노드_참조`` 는 "양 끝이 asset 이어야 한다"를 전수로 셌다.
    084 가 만드는 소속 엣지는 **dst 가 entity 노드**라서 그 검사에 걸리면 저장 즉시 게이트가
    빨간불이 된다 — 그렇다고 검사를 지우면 자산↔자산 엣지에 entity 가 섞이는 실제 결함을 놓친다.
    그래서 기존 검사는 **asset↔asset 한정**으로 좁히고, 소속 엣지에는 그 엣지에 맞는 불변식
    (방향 고정·kind 등록 형태)을 새 축으로 세운다.
    """

    _KW = {"min_conf_similarity": 0.75, "exclude_kinds": _EXCLUDE, "auto_approve_min": 1.01}

    def _checks(self) -> dict[str, dict]:
        return {c["name"]: c for c in build_checks(**self._KW)}

    def test_기존_검사_이름과_순서가_앞에_그대로_남는다(self):
        names = [c["name"] for c in build_checks(**self._KW)]
        self.assertEqual(names[: len(_LEGACY_CHECK_NAMES)], list(_LEGACY_CHECK_NAMES))

    def test_소속_검사축이_레거시_바로_뒤에_온다(self):
        # 🔴 이 테스트가 봉인하는 것은 **기존 이름·순서가 흔들리지 않는다**는 것이다(운영 보고
        #    줄과 과거 실행 결과를 그대로 비교하기 위해). 새 축이 뒤에 더 붙는 것은 정상적인
        #    확장이므로, "레거시 다음이 정확히 소속뿐"이 아니라 **레거시 다음이 소속으로 시작**
        #    한다고 본다(2026-08-31 · 090 이 개체임베딩 축을 뒤에 더했다).
        names = [c["name"] for c in build_checks(**self._KW)]
        head = len(_LEGACY_CHECK_NAMES)
        self.assertEqual(names[head:head + len(MM_MEMBER_CHECK_NAMES)],
                         list(MM_MEMBER_CHECK_NAMES))
        self.assertEqual(list(CHECK_NAMES), names)

    def test_개체임베딩_고아_검사가_맨_뒤에_있다(self):
        # FK 를 걸 수 없어(부분 유니크) 감지가 유일한 방어선이다 — v306.
        names = [c["name"] for c in build_checks(**self._KW)]
        self.assertEqual(names[-1], "개체임베딩_고아")

    def test_개체임베딩_고아_검사는_테이블_부재를_견딘다(self):
        # 🔴 v306 이 적용되지 않은 DB 에서도 게이트가 돌아야 한다 — 없으면 0 을 낸다.
        check = {c["name"]: c for c in build_checks(**self._KW)}["개체임베딩_고아"]
        self.assertIn("to_regclass('entity_embedding')", check["sql"])
        self.assertIn("NOT EXISTS", check["sql"])
        self.assertEqual(check["params"], [])

    def test_비asset_노드_참조는_소속_엣지를_제외한다(self):
        # 소속 엣지는 정의상 dst 가 entity 다 — 이 검사에 걸리면 게이트가 영구히 빨간불이 된다.
        check = self._checks()["비-asset_노드_참조"]
        self.assertIn("kind_code <> %s", check["sql"])
        self.assertIn(MM_MEMBER_KIND_CODE, check["params"])

    def test_비asset_노드_참조는_소속_밖_엣지는_여전히_잡는다(self):
        # 좁히기가 "검사 무력화"로 새지 않았는지 — 양 끝 asset 조건 자체는 남아 있어야 한다.
        sql = self._checks()["비-asset_노드_참조"]["sql"]
        self.assertIn("n1.node_kind <> 'asset'", sql)
        self.assertIn("n2.node_kind <> 'asset'", sql)

    def test_소속_엣지_방향_검사는_kind와_양끝을_고정한다(self):
        check = self._checks()["소속_엣지_방향_위반"]
        self.assertIn("kind_code = %s", check["sql"])
        self.assertIn(MM_MEMBER_KIND_CODE, check["params"])
        # src=asset · dst=entity 고정 → 역방향(entity→asset)도 이 조건에서 위반으로 잡힌다.
        self.assertIn("sn.node_kind <> 'asset'", check["sql"])
        self.assertIn("dn.node_kind <> 'entity'", check["sql"])

    def test_소속_kind_등록_검사는_비대칭_active를_요구한다(self):
        check = self._checks()["소속_kind_등록_위반"]
        self.assertIn(MM_MEMBER_KIND_CODE, check["params"])
        self.assertIn("is_symmetric", check["sql"])
        self.assertIn("status <> 'active'", check["sql"])

    def test_소속_검사도_kind_코드를_상수로_바인딩한다(self):
        # 문자열 하드코딩 금지 — 영속(persist)과 같은 정본 상수를 쓴다.
        for name in MM_MEMBER_CHECK_NAMES:
            with self.subTest(name):
                check = self._checks()[name]
                self.assertEqual(check["params"], [MM_MEMBER_KIND_CODE])
                self.assertNotIn(f"'{MM_MEMBER_KIND_CODE}'", check["sql"])

    def test_게이트를_끈_설정에서도_소속_검사는_남는다(self):
        # 소속 엣지 불변식은 관계 게이트 설정과 무관하다(끄고 켤 축이 아니다).
        names = [c["name"] for c in build_checks(min_conf_similarity=0.0,
                                                 exclude_kinds=frozenset())]
        for name in MM_MEMBER_CHECK_NAMES:
            self.assertIn(name, names)


class _FakeDb:
    """`run_verify` 가 쓰는 최소 인터페이스만 흉내(PostgresUtil.transaction → conn.cursor)."""

    def __init__(self, counts: dict[str, int]):
        self._counts = counts
        self.executed: list[str] = []

    def transaction(self):
        outer = self

        class _Cur:
            def execute(self, sql, params=None):
                outer.executed.append(sql)
                self._sql = sql

            def fetchone(self):
                # 검사 이름을 SQL 주석으로 심어 두고 그걸로 조작값을 고른다.
                for name, n in outer._counts.items():
                    if f"-- check:{name}" in self._sql:
                        return {"n": n}
                return {"n": 0}

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        class _Conn:
            def cursor(self, **_k):
                return _Cur()

        class _Ctx:
            def __enter__(self):
                return _Conn()

            def __exit__(self, *_a):
                return False

        return _Ctx()


class TestRunVerify(unittest.TestCase):
    _CHECKS_KW = {"min_conf_similarity": 0.75, "exclude_kinds": _EXCLUDE}

    def test_전부_0이면_통과다(self):
        rep = run_verify(_FakeDb({}), checks=build_checks(**self._CHECKS_KW))
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["violations"], 0)

    def test_위반이_있으면_실패다(self):
        rep = run_verify(_FakeDb({"자기참조_엣지": 3}), checks=build_checks(**self._CHECKS_KW))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["violations"], 3)

    def test_위반_항목만_따로_보고한다(self):
        rep = run_verify(_FakeDb({"자기참조_엣지": 3, "대칭_중복행": 1}),
                         checks=build_checks(**self._CHECKS_KW))
        self.assertEqual({v["name"] for v in rep["failed"]}, {"자기참조_엣지", "대칭_중복행"})
        self.assertEqual(rep["violations"], 4)

    def test_모든_검사를_실행한다(self):
        # 하나라도 건너뛰면 "통과"가 거짓이 된다.
        db = _FakeDb({})
        checks = build_checks(**self._CHECKS_KW)
        run_verify(db, checks=checks)
        self.assertEqual(len(db.executed), len(checks))

    def test_결과가_검사_순서를_지킨다(self):
        checks = build_checks(**self._CHECKS_KW)
        rep = run_verify(_FakeDb({}), checks=checks)
        self.assertEqual([r["name"] for r in rep["results"]], [c["name"] for c in checks])

    def test_소속_엣지_위반도_실패로_집계된다(self):
        # 새 축이 목록에만 있고 집계에서 빠지면 "통과"가 거짓이 된다.
        rep = run_verify(_FakeDb({"소속_엣지_방향_위반": 2, "소속_kind_등록_위반": 1}),
                         checks=build_checks(**self._CHECKS_KW))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["violations"], 3)
        self.assertEqual({v["name"] for v in rep["failed"]},
                         {"소속_엣지_방향_위반", "소속_kind_등록_위반"})


if __name__ == "__main__":
    unittest.main()
