"""084 F05 — 타입 어휘 **DB 이관** 단위 테스트(정의문 상수·어휘 로더·등록 CLI·엔진 격리).

무엇을 검증하나: 개체 타입 5종(인물·장소·조직·작품·사건)이 이름뿐인 **코드 상수**에서 정의문을 가진
**등록 행**(``mm_meta_type_vocab`` · ``vocab_code='default'`` · v303 으로 ``mm_skill`` 에서
분리 · spec 086)으로 옮겨 간다(084 spec §10). 그 이관이 지켜야
하는 것 네 가지를 여기서 못 박는다.

    ① **정의문 문안은 측정으로 확정된 값**이다(2026-08-25 파일럿 · 경계 개체 18자산 재판정에서
       흔들림 6종이 전부 해소됐다: 조선=사건·국립중앙박물관=장소·전주시청=장소 …). 그래서 문구를
       테스트가 봉인한다 — 특히 사건의 "왕조명 그대로" 예시는 **표기 부작용 방지용**이라 지우면
       '조선'이 '조선시대'로 나와 묶음이 갈라진다(v1 실측).

    ② **등록 행이 정본이되, 어휘는 코드와 일치해야 한다**. 저장 유니크 키가 ``(entity_type,
       entity_uid)`` 라 5종 밖 이름이 한 번 들어오면 그 오타가 데이터로 굳는다 → 로더가 fail-fast.
       행이 아예 없으면(부트스트랩·미등록 환경) **코드 상수로 폴백**한다 — 배치가 죽는 것보다 낫다.

    ③ 🔴 **085 판정 엔진을 재사용하지 않는다**(spec §10 표). 저장소(``mm_skill``)만 공유하고, 판정은
       개체 추출과 같은 LLM 호출에서 난다. 그런데 이 행이 ``fetch_active_skills`` 에 걸리면 085 분류
       배치가 **모든 자산을 인물/장소/조직/작품/사건으로 분류**해 버린다(LLM 비용·엉뚱한 패싯 축).
       그래서 배치 진입 지점이 이 코드를 건너뛰는지 **행동으로** 확인한다.

    ④ **등록 CLI 는 dry-run 이 기본**이고 프리셋(코드 상수)을 그대로 등록한다 — 사람이 문안을 손으로
       옮겨 적지 않게 한다(옮겨 적으면 코드 폴백과 DB 행이 갈린다).

DB·LLM·네트워크 불필요 — 아래 ``_Conn`` 이 ``mm_skill`` 한 테이블을 흉내내는 작은 가짜 DB 다.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import uuid
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from unittest import mock

import scripts.register_mm_meta_types as cli

from src.mm_classify.model import MM_META_TYPE_SKILL_CODE, NON_CLASSIFY_SKILL_CODES
from src.mm_classify.persist import (
    DefinitionTable,
    SkillPersistError,
    fetch_active_skills,
    skill_declaration,
    upsert_definition_row,
    upsert_skill,
)
from src import mm_meta as _mm_meta_pkg  # noqa: F401  (패키지 초기화 순서 고정)
from src.mm_meta import persist as vocab_persist
from src.mm_meta.persist import (
    DEFAULT_VOCAB_CODE,
    MmMetaPersistError,
    fetch_meta_type_vocab,
)
from src.mm_meta.rules import (
    ENTITY_TYPE_DEFS,
    ENTITY_TYPE_ORDER,
    ENTITY_TYPES,
    EntityTypeDef,
)

# 더미 식별자(실 자산 id 금지 — 형태만 UUIDv7).
_SKILL_ID = "018f0000-0000-7000-8000-0000000000f1"


def _labels(defs: tuple[EntityTypeDef, ...] = ENTITY_TYPE_DEFS) -> list[dict[str, str]]:
    """정의문 목록을 ``mm_skill.labels`` JSONB 모양으로 바꾼다.

    Args:
        defs: 담을 정의문 목록. 기본은 코드 프리셋(정상 행).

    Returns:
        ``[{code,name,definition,not}]`` 목록.
    """
    return [
        {"code": d.code, "name": d.name, "definition": d.definition, "not": d.exclusion}
        for d in defs
    ]


def _row(
    *,
    skill_code: str = DEFAULT_VOCAB_CODE,
    labels: list[dict[str, str]] | None = None,
    version: int = 1,
    status: str = "active",
) -> dict[str, Any]:
    """조회 행 하나(JSONB 는 드라이버가 dict/list 로 준다).

    키 이름이 ``skill_*`` 인 이유: 어휘 조회 SQL 이 ``vocab_id AS skill_id``·
    ``vocab_code AS skill_code``·``types AS labels`` 로 **별칭**을 붙여 085 검증기에 넘긴다
    (spec 086 — 정의문 문서 모양이 같으니 검증기를 공유한다). 드라이버가 주는 키는 별칭 쪽이다.

    Args:
        skill_code: 행의 자연키. 기본은 어휘 코드(``default``) — ``mm_skill`` 격리 테스트는
            ``MM_META_TYPE_SKILL_CODE`` 를 명시로 넘긴다.
        labels: 라벨 JSONB. ``None`` 이면 코드 프리셋 5종.
        version: 행 버전.
        status: 행 상태(``active``·``disabled``).

    Returns:
        조회 행 dict.
    """
    return {
        "skill_id": uuid.UUID(_SKILL_ID),
        "skill_code": skill_code,
        "name": "멀티모달 메타 타입",
        "version": version,
        "policy": {"selection": "single", "unassigned": "해당없음", "max_labels": 30},
        "labels": _labels() if labels is None else labels,
        "status": status,
    }


def _vocab_row(*, version: int = 1, status: str = "active") -> dict[str, Any]:
    """``mm_meta_type_vocab`` 조회 행 하나 — **실제 컬럼 이름**을 쓴다.

    ``_row`` 와 나누는 이유: 읽기 경로는 SQL 별칭(``types AS labels`` …)을 거쳐 085 검증기에
    가므로 별칭 이름의 행을 받고, 쓰기 경로(``upsert_definition_row``)는 별칭 없이 실제 컬럼을
    읽는다. 가짜 커넥션이 별칭까지 흉내내면 그 흉내가 곧 또 하나의 계약이 되어 버린다.

    Args:
        version: 행 버전.
        status: 행 상태.

    Returns:
        조회 행 dict(``vocab_id``·``vocab_code``·``types`` …).
    """
    return {
        "vocab_id": uuid.UUID(_SKILL_ID),
        "vocab_code": DEFAULT_VOCAB_CODE,
        "name": "멀티모달 메타 타입",
        "version": version,
        "policy": {"selection": "single", "unassigned": "해당없음", "max_labels": 30},
        "types": _labels(),
        "status": status,
    }


def _classify_skill() -> Any:
    """분류 스킬 하나(085 경로가 그대로임을 고정하는 데 쓴다).

    Returns:
        검증을 통과한 ``ClassificationSkill``(자산 분류용 · 자연키는 어휘 코드와 다르다).
    """
    from src.mm_classify.model import load_skill as _load

    return _load(
        {
            "skill": "테스트 축",
            "skill_code": "test_axis",
            "version": 1,
            "policy": {"selection": "multi", "unassigned": "해당없음"},
            "labels": [
                {"code": "a", "name": "가", "definition": "가에 관한 자료", "not": "나에 관한 자료"},
                {"code": "b", "name": "나", "definition": "나에 관한 자료", "not": "가에 관한 자료"},
            ],
        }
    )


class _Cur:
    """가짜 커서 — 실행 SQL·파라미터를 로그에 쌓고 가짜 DB 가 고른 행을 돌려준다."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def execute(self, sql: Any, params: Any = None) -> _Cur:
        flat = " ".join(str(sql).split())
        self._conn.log.append((flat, params))
        self._conn.fetched = self._conn.rows_for(flat, params)
        return self

    def fetchone(self) -> Any:
        return self._conn.fetched[0] if self._conn.fetched else None

    def fetchall(self) -> list[Any]:
        return list(self._conn.fetched)

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _Conn:
    """가짜 커넥션 = ``mm_skill`` 한 테이블짜리 인메모리 DB.

    필터(자연키·상태)를 **실제로 적용한다** — "비활성 행은 못 읽는다(=폴백)"를 행동으로 검증하려면
    가짜도 그 조건을 지켜야 한다. 파라미터만 확인하면 구현이 필터를 빠뜨려도 통과한다.

    Args:
        rows: 초기 ``mm_skill`` 행 목록. 비면 미등록 상태에서 시작한다.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.log: list[tuple[str, Any]] = []
        self.fetched: list[Any] = []
        self.rows = list(rows or [])

    def cursor(self, row_factory: Any = None, **_k: Any) -> _Cur:
        return _Cur(self)

    @contextlib.contextmanager
    def transaction(self):  # noqa: ANN201 - 테스트 헬퍼
        yield self

    def rows_for(self, sql: str, params: Any) -> list[dict[str, Any]]:
        """SQL 조각으로 문장을 알아보고 결과를 만든다.

        Args:
            sql: 정규화된 SQL 문자열.
            params: 바인딩 파라미터.

        Returns:
            결과 행 목록(``skill_code`` 오름차순).
        """
        p = tuple(params or ())
        # 두 테이블을 모두 흉내낸다 — 분류 스킬은 ``mm_skill``, 개체 타입 어휘는
        # ``mm_meta_type_vocab``(v303 분리). 자연키 컬럼 이름도 각각 다르다.
        if "FROM mm_skill" not in sql and "FROM mm_meta_type_vocab" not in sql:
            return []
        out = list(self.rows)
        # 자연키 컬럼 이름이 테이블마다 다르다. 행이 어느 모양인지는 행이 가진 키로 안다
        # (읽기 경로는 별칭 행, 쓰기 경로는 실제 컬럼 행 — ``_row`` / ``_vocab_row`` 참조).
        by_code = "skill_code = %s" in sql or "vocab_code = %s" in sql
        if by_code:
            out = [
                r for r in out
                if (r["vocab_code"] if "vocab_code" in r else r["skill_code"]) == p[0]
            ]
        if "status = %s" in sql:
            out = [r for r in out if r["status"] == (p[1] if by_code else p[0])]
        return sorted(
            out, key=lambda r: r["vocab_code"] if "vocab_code" in r else r["skill_code"]
        )

    def sqls(self) -> list[str]:
        """실행된 SQL 문자열만."""
        return [s for s, _p in self.log]


class TestEntityTypeDefs(unittest.TestCase):
    """정의문 프리셋(v2) — **측정으로 확정된 문안**이라 값 자체를 봉인한다(spec §10)."""

    def test_5종이_고정_순서로_있다(self) -> None:
        self.assertEqual(tuple(d.name for d in ENTITY_TYPE_DEFS), ENTITY_TYPE_ORDER)

    def test_이름_집합이_코드_어휘와_같다(self) -> None:
        # 어휘가 갈리면 프롬프트에 정의문이 없는 타입이 생기거나(누락) 저장 못 할 타입이 실린다.
        self.assertEqual({d.name for d in ENTITY_TYPE_DEFS}, set(ENTITY_TYPES))

    def test_코드는_소문자_스네이크_고유값이다(self) -> None:
        codes = [d.code for d in ENTITY_TYPE_DEFS]
        self.assertEqual(len(set(codes)), len(codes))
        for code in codes:
            with self.subTest(code=code):
                self.assertRegex(code, r"^[a-z][a-z0-9_]*$")

    def test_정의문과_경계가_모두_채워져_있다(self) -> None:
        # 라벨명만 있는 어휘로는 판정이 LLM 상식에 맡겨진다(085 파일럿 발견 1 · 흔들림 7건의 원인).
        for d in ENTITY_TYPE_DEFS:
            with self.subTest(name=d.name):
                self.assertTrue(d.definition.strip())
                self.assertTrue(d.exclusion.strip())

    def test_사건_정의문의_왕조명_예시는_지우지_않는다(self) -> None:
        # 🔴 표기 부작용 방지용이다 — v1(예시 없음)에서 '조선'이 '조선시대'로 바뀌어 나왔고
        #    (정의문의 "왕조·시대" 문구를 LLM 이 표기로 따라감) 그러면 묶음이 갈라진다.
        event = next(d for d in ENTITY_TYPE_DEFS if d.name == "사건")
        self.assertIn("왕조", event.definition)
        self.assertIn("조선", event.definition)
        self.assertIn("그대로", event.definition)

    def test_흔들림을_해소한_경계_문구가_남아있다(self) -> None:
        # 파일럿에서 갈렸던 6종(국립중앙박물관·전주시청·조선·통일신라 …)을 통일시킨 문구들이다.
        by_name = {d.name: d for d in ENTITY_TYPE_DEFS}
        self.assertIn("국립중앙박물관", by_name["장소"].definition)  # 시설은 장소
        self.assertIn("조직", by_name["장소"].exclusion)  # 그 안의 단체는 조직
        self.assertIn("사건", by_name["조직"].exclusion)  # 왕조·시대는 사건
        self.assertIn("장소", by_name["조직"].exclusion)  # 건물 자체는 장소

    def test_불변_객체다(self) -> None:
        # 프리셋은 폴백이자 등록 원본이다 — 실행 중에 바뀌면 등록 행과 폴백이 갈린다.
        with self.assertRaises(FrozenInstanceError):
            ENTITY_TYPE_DEFS[0].name = "동물"  # type: ignore[misc]

    def test_빈_값은_거부한다(self) -> None:
        # 손으로 정의문을 지운 채 등록하는 경로를 값 객체가 먼저 막는다(경계 fail-fast).
        for bad in (
            {"code": "", "name": "인물", "definition": "정의", "exclusion": "경계"},
            {"code": "person", "name": "  ", "definition": "정의", "exclusion": "경계"},
            {"code": "person", "name": "인물", "definition": "", "exclusion": "경계"},
            {"code": "person", "name": "인물", "definition": "정의", "exclusion": "   "},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    EntityTypeDef(**bad)


class TestFetchMetaTypeVocab(unittest.TestCase):
    """어휘 로더 — **등록 행이 정본**, 없으면 코드 프리셋 폴백, 어긋나면 fail-fast(spec §10).

    세 갈래를 나눠 두는 이유:
      - **행 있음** → 그 정의문이 프롬프트로 나간다(어휘를 코드 배포 없이 고칠 수 있게 한 목적).
      - **행 없음/비활성** → 폴백. 부트스트랩·새 배포·되돌리기 상황에서 배치가 죽는 것보다, 검증된
        옛 문안으로 도는 편이 낫다(어휘가 사라지면 판정 자체가 불가능하다). 대신 **로그로 알린다** —
        조용히 폴백하면 "등록했는데 왜 그대로인가"를 다시 조사하게 된다.
      - **어휘 불일치** → 예외. 저장 유니크 키가 ``(entity_type, entity_uid)`` 라 5종 밖 이름이 한 번
        들어오면 그 오타가 데이터로 굳는다(되돌리려면 노드·엣지를 손으로 지워야 한다).
    """

    def test_등록_행의_정의문을_행_순서대로_읽는다(self) -> None:
        conn = _Conn([_row()])
        defs = fetch_meta_type_vocab(conn)
        self.assertEqual(tuple(d.name for d in defs), ENTITY_TYPE_ORDER)
        self.assertEqual(defs, ENTITY_TYPE_DEFS)

    def test_행의_문구가_그대로_반영된다(self) -> None:
        # DB 를 정본으로 삼는 목적 자체 — 코드 배포 없이 정의문을 고칠 수 있어야 한다.
        edited = _labels()
        edited[0] = {**edited[0], "definition": "고친 정의", "not": "고친 경계"}
        defs = fetch_meta_type_vocab(_Conn([_row(labels=edited)]))
        self.assertEqual(defs[0].definition, "고친 정의")
        self.assertEqual(defs[0].exclusion, "고친 경계")

    def test_자연키와_상태로_좁혀_한_번만_읽는다(self) -> None:
        conn = _Conn([_row()])
        fetch_meta_type_vocab(conn)
        self.assertEqual(len(conn.log), 1)
        sql, params = conn.log[0]
        self.assertIn("FROM mm_meta_type_vocab", sql)
        self.assertIn("vocab_code = %s", sql)
        # 별칭이 붙어야 085 검증기가 그대로 읽는다(spec 086).
        self.assertIn("types AS labels", sql)
        self.assertEqual(params[0], DEFAULT_VOCAB_CODE)
        self.assertIn("active", params)

    def test_행이_없으면_코드_프리셋으로_폴백한다(self) -> None:
        with self.assertLogs("src.mm_meta.persist", level="WARNING") as logged:
            defs = fetch_meta_type_vocab(_Conn([]))
        self.assertEqual(defs, ENTITY_TYPE_DEFS)
        self.assertIn(DEFAULT_VOCAB_CODE, "\n".join(logged.output))

    def test_다른_스킬만_있어도_폴백한다(self) -> None:
        with self.assertLogs("src.mm_meta.persist", level="WARNING"):
            defs = fetch_meta_type_vocab(_Conn([_row(skill_code="food_axis")]))
        self.assertEqual(defs, ENTITY_TYPE_DEFS)

    def test_비활성_행은_폴백이다(self) -> None:
        # 어휘를 되돌리는 스위치 — 행을 지우지 않고 status 만 내리면 검증된 옛 문안으로 돌아간다.
        with self.assertLogs("src.mm_meta.persist", level="WARNING"):
            defs = fetch_meta_type_vocab(_Conn([_row(status="disabled")]))
        self.assertEqual(defs, ENTITY_TYPE_DEFS)

    def test_어휘_밖_이름이_있으면_거부한다(self) -> None:
        bad = _labels()
        bad[0] = {**bad[0], "code": "animal", "name": "동물"}
        with self.assertRaises(MmMetaPersistError) as ctx:
            fetch_meta_type_vocab(_Conn([_row(labels=bad)]))
        self.assertIn("동물", str(ctx.exception))

    def test_5종에_모자라면_거부한다(self) -> None:
        # 빠진 타입은 정의문 없이 판정된다(어휘 줄에는 남는다) — 조용히 통과시키면 그 타입만
        # 옛 흔들림 상태로 되돌아간다.
        with self.assertRaises(MmMetaPersistError) as ctx:
            fetch_meta_type_vocab(_Conn([_row(labels=_labels()[:4])]))
        self.assertIn("사건", str(ctx.exception))

    def test_같은_이름이_두_번이면_거부한다(self) -> None:
        dup = [*_labels(), {"code": "person2", "name": "인물", "definition": "정의", "not": "경계"}]
        with self.assertRaises(MmMetaPersistError):
            fetch_meta_type_vocab(_Conn([_row(labels=dup)]))

    def test_모양이_깨진_행은_거부한다(self) -> None:
        # 손 SQL·구버전 행이 조용히 쓰이지 않게 한다(정의문 누락은 곧 이름뿐인 어휘로의 퇴행).
        broken = _labels()
        broken[1] = {"code": "place", "name": "장소"}  # definition·not 없음
        with self.assertRaises(MmMetaPersistError):
            fetch_meta_type_vocab(_Conn([_row(labels=broken)]))

    def test_반환은_불변_튜플이다(self) -> None:
        defs = fetch_meta_type_vocab(_Conn([_row()]))
        self.assertIsInstance(defs, tuple)
        self.assertTrue(all(isinstance(d, EntityTypeDef) for d in defs))


class TestClassifyEngineIsolation(unittest.TestCase):
    """🔴 저장소만 공유하고 **판정 엔진은 공유하지 않는다**(spec §10 표).

    무슨 사고를 막나: 타입 어휘 행은 ``mm_skill`` 에 들어가는데, 085 분류 배치는 그 테이블의 active
    행을 전부 집어 **자산마다 LLM 판정**을 돈다. 아무 방어가 없으면 등록하는 순간 전 자산이
    "인물/장소/조직/작품/사건"으로 분류되기 시작한다 — 쓰지도 않을 판정에 LLM 비용이 들고, 패싯 축에
    엉뚱한 분류가 생긴다. 관계 카탈로그에서 ``mm_member`` 를 프롬프트 목록에서 빼 둔 것과 같은 결의
    방어다(``PROMPT_EXCLUDED_KIND_CODES`` 선례).

    ⚠️ 현재 DB 에는 이 행이 없으므로 **동작 변화는 0** 이다(등록 이후를 대비한 사전 방어).
    """

    def test_예약_코드가_제외_집합에_있다(self) -> None:
        self.assertEqual(MM_META_TYPE_SKILL_CODE, "mm_meta_type")
        self.assertIn(MM_META_TYPE_SKILL_CODE, NON_CLASSIFY_SKILL_CODES)

    def test_분류_배치_대상에서_빠진다(self) -> None:
        # v303 이후 이 행은 ``mm_skill`` 에 없다. 그래도 **되돌리기(downgrade)가 복원**하므로
        # 격리 가드는 계속 필요하다 — 그 상태를 흉내내 명시로 넘긴다.
        conn = _Conn([_row(skill_code=MM_META_TYPE_SKILL_CODE), _row(skill_code="food_axis")])
        codes = [r["skill_code"] for r in fetch_active_skills(conn)]
        self.assertEqual(codes, ["food_axis"])

    def test_다른_스킬은_그대로_읽힌다(self) -> None:
        # 기존 경로 회귀 0 — 제외는 예약 코드에만 걸린다.
        conn = _Conn([_row(skill_code="food_axis"), _row(skill_code="place_axis")])
        codes = [r["skill_code"] for r in fetch_active_skills(conn)]
        self.assertEqual(codes, ["food_axis", "place_axis"])

    def test_분류_등록_CLI_는_예약_코드를_거부한다(self) -> None:
        # 분류 스킬 등록 경로로 이 코드를 쓰면 어휘 행이 자산 분류표로 덮인다 → DB 열기 전에 끊는다.
        import scripts.register_mm_skill as skill_cli

        raw = {
            "skill": "잘못된 등록",
            "skill_code": MM_META_TYPE_SKILL_CODE,
            "version": 1,
            "policy": {"selection": "multi", "unassigned": "해당없음"},
            "labels": [
                {"code": "alpha", "name": "가라벨", "definition": "가정의", "not": "가경계"}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(skill_cli, "_init_env") as init:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = skill_cli.main([str(path), "--preview", "5"])
        self.assertEqual(rc, 1)
        init.assert_not_called()
        self.assertIn(MM_META_TYPE_SKILL_CODE, out.getvalue())


class _FakeDb:
    """``PostgresUtil`` 의 최소 형상 — 어느 통로가 열렸는지만 기록한다(register_mm_meta 테스트 관례)."""

    def __init__(self, conn: _Conn | None = None) -> None:
        self.opened: list[str] = []
        self.conn = conn if conn is not None else _Conn()

    @contextlib.contextmanager
    def _ctx(self, tag: str):  # noqa: ANN202 - 테스트 헬퍼
        self.opened.append(tag)
        yield self.conn

    def connection(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._ctx("connection")

    def transaction(self):  # noqa: ANN201 - 테스트 헬퍼
        return self._ctx("transaction")


class TestBuildTypeSkill(unittest.TestCase):
    """등록 프리셋 조립 — 코드 상수 → ``mm_skill`` 선언(순수 · 사람이 문안을 옮겨 적지 않는다)."""

    def test_예약_코드와_single_정책으로_조립한다(self) -> None:
        skill = cli.build_type_skill()
        self.assertEqual(skill.skill_code, DEFAULT_VOCAB_CODE)
        # 개체 하나에 타입 하나다(085 의 multi 기본과 다르다 — 대상이 자산이 아니라 개체다).
        self.assertEqual(skill.policy.selection, "single")

    def test_라벨이_코드_프리셋_5종_그대로다(self) -> None:
        labels = skill_declaration(cli.build_type_skill())["labels"]
        self.assertEqual(labels, _labels())

    def test_저장_선언을_로더가_그대로_되읽는다(self) -> None:
        # 🔴 왕복 봉인 — 등록 CLI 가 쓰는 모양과 로더가 읽는 모양이 갈리면, 등록은 성공하는데
        #    배치는 폴백으로 도는 상태가 된다(둘 다 조용하다).
        declaration = skill_declaration(cli.build_type_skill())
        conn = _Conn([_row(labels=declaration["labels"])])
        self.assertEqual(fetch_meta_type_vocab(conn), ENTITY_TYPE_DEFS)


class TestRegisterTypesCli(unittest.TestCase):
    """등록 CLI — **dry-run 이 기본**이고, 등록은 주입 seam 으로 DB 없이 검증한다."""

    def _main(self, argv: list[str], **kw: Any) -> tuple[int, str]:
        """콘솔 출력을 삼키고 main 을 돌린다.

        Args:
            argv: CLI 인자 목록.
            **kw: ``mock.patch.object`` 로 갈아 끼울 것이 없을 때를 위한 여분(사용하지 않음).

        Returns:
            ``(종료 코드, 표준 출력)``.
        """
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(argv, **kw)
        return rc, out.getvalue()

    def test_기본은_dry_run_이고_DB_를_열지_않는다(self) -> None:
        with mock.patch.object(cli, "_init_env") as init:
            rc, out = self._main([])
        self.assertEqual(rc, 0)
        init.assert_not_called()
        for name in ENTITY_TYPE_ORDER:
            with self.subTest(name=name):
                self.assertIn(name, out)

    def test_dry_run_은_등록_명령을_안내한다(self) -> None:
        with mock.patch.object(cli, "_init_env"):
            _rc, out = self._main([])
        self.assertIn("--apply", out)

    def test_apply_는_트랜잭션에서_등록한다(self) -> None:
        seen: list[Any] = []

        def _upsert(conn: Any, skill: Any) -> dict[str, Any]:
            """등록 주입 seam(모의).

            Args:
                conn: 커넥션(가짜).
                skill: 등록 요청 스킬.

            Returns:
                ``upsert_skill`` 반환 모양.
            """
            seen.append(skill)
            return {"action": "registered", "skill_id": _SKILL_ID,
                    "skill_code": skill.skill_code, "version": 1,
                    "previous_version": None, "declared_version": 1}

        db = _FakeDb()
        result = cli.run_apply(db, cli.build_type_skill(), upsert_fn=_upsert)
        self.assertEqual(db.opened, ["transaction"])
        self.assertEqual(result["action"], "registered")
        self.assertEqual(seen[0].skill_code, DEFAULT_VOCAB_CODE)

    def test_show_는_읽기만_한다(self) -> None:
        # 지금 무엇이 쓰이는지(등록분인지 폴백인지) 확인하는 경로 — 쓰기 통로를 열면 안 된다.
        db = _FakeDb(_Conn([_row()]))
        defs = cli.run_show(db)
        self.assertEqual(db.opened, ["connection"])
        self.assertEqual(defs, ENTITY_TYPE_DEFS)

    def test_show_출력에_정의문이_그대로_실린다(self) -> None:
        # 사람이 확인할 값은 타입 이름이 아니라 그 뒤의 정의·경계 문장이다(정의문이 곧 분류 기준).
        text = "\n".join(cli.format_show_lines(ENTITY_TYPE_DEFS))
        for d in ENTITY_TYPE_DEFS:
            with self.subTest(name=d.name):
                self.assertIn(d.definition, text)
                self.assertIn(d.exclusion, text)

    def test_변경_없음이면_그렇게_알린다(self) -> None:
        # 같은 프리셋을 두 번 등록해도 version 이 오르지 않는다(085 영속 계약 · 헛 백필 방지).
        lines = cli.format_apply_lines(
            {"action": "unchanged", "skill_id": _SKILL_ID,
             "skill_code": MM_META_TYPE_SKILL_CODE, "version": 2,
             "previous_version": 2, "declared_version": 1}
        )
        self.assertIn("변경 없음", "\n".join(lines))

    def test_개정이면_재판정이_필요함을_알린다(self) -> None:
        # 🔴 정의문을 고치면 기존 판정이 낡는다 — 그 사실을 사람이 있는 자리에서 알려야 한다
        #    (문안 판 상수는 그대로라 스탬프만으로는 드러나지 않는다).
        lines = "\n".join(cli.format_apply_lines(
            {"action": "revised", "skill_id": _SKILL_ID,
             "skill_code": MM_META_TYPE_SKILL_CODE, "version": 2,
             "previous_version": 1, "declared_version": 1}
        ))
        self.assertIn("재판정", lines)


class TestVocabTableSeparation(unittest.TestCase):
    """v303 분리가 지켜야 하는 것 — **어느 테이블에 쓰는가**와 **규칙은 하나인가**(spec 086).

    이 클래스는 "타입 어휘가 자산 분류표와 다른 테이블에 산다"를 코드 수준에서 고정한다. 두 저장이
    다시 섞이면(예: 어휘 쓰기가 ``mm_skill`` 로 되돌아가면) 화면·API 가 다시 `<> 'mm_meta_type'`
    같은 필터를 달아야 하고, 그 필터를 한 곳이라도 빠뜨리면 자산 분류 축에 개체 타입이 노출된다.
    """

    def _capture(self, spec: DefinitionTable) -> list[str]:
        """빈 테이블에 등록했을 때 실행된 SQL 문자열 목록.

        Args:
            spec: 대상 테이블 서술.

        Returns:
            실행된 SQL 문자열 목록(조회 → INSERT 순).
        """
        conn = _Conn([])
        upsert_definition_row(conn, cli.build_type_skill(), spec=spec)
        return conn.sqls()

    def test_어휘_쓰기는_어휘_테이블로_간다(self) -> None:
        sqls = "\n".join(self._capture(vocab_persist._VOCAB_TABLE))
        self.assertIn("mm_meta_type_vocab", sqls)
        self.assertIn("vocab_code", sqls)
        self.assertIn("types", sqls)
        # 🔴 핵심 — 어휘 쓰기가 자산 분류표를 건드리지 않는다.
        self.assertNotIn("mm_skill", sqls)

    def test_분류_쓰기는_분류_테이블로_간다(self) -> None:
        # 반대 방향도 고정한다(회귀 0 — 085 경로는 그대로다).
        conn = _Conn([])
        upsert_skill(conn, _classify_skill())
        sqls = "\n".join(conn.sqls())
        self.assertIn("mm_skill", sqls)
        self.assertNotIn("mm_meta_type_vocab", sqls)

    def test_등록_개정_멱등_규칙은_한_벌이다(self) -> None:
        # 두 저장이 **같은 함수**를 쓴다 — 사본이 생기면 언젠가 한쪽만 고쳐진다.
        self.assertIs(
            vocab_persist.upsert_meta_type_vocab.__wrapped__
            if hasattr(vocab_persist.upsert_meta_type_vocab, "__wrapped__")
            else upsert_definition_row,
            upsert_definition_row,
        )
        conn = _Conn([_vocab_row(version=3)])
        got = vocab_persist.upsert_meta_type_vocab(conn, cli.build_type_skill())
        # 프리셋과 같은 선언이므로 쓰기 0(멱등) — 어휘 쪽도 같은 규칙이다.
        self.assertEqual(got["action"], "unchanged")
        self.assertEqual(got["version"], 3)

    def test_테이블_이름은_식별자_문법을_통과해야_한다(self) -> None:
        # SQL 에 문자열로 박히는 값이라 문법 가드가 있다(외부 입력이 들어올 자리가 아니다).
        for bad in ("mm skill", "mm_skill; DROP TABLE node", "MmSkill", ""):
            with self.subTest(bad=bad), self.assertRaises(SkillPersistError):
                DefinitionTable(
                    table=bad, id_col="a", code_col="b", defs_col="c"
                )

    def test_예약_코드는_어휘_자연키가_아니다(self) -> None:
        # v303 이후 두 값의 역할이 다르다 — 섞어 쓰면 존재하지 않는 어휘 행을 새로 만든다.
        self.assertNotEqual(DEFAULT_VOCAB_CODE, MM_META_TYPE_SKILL_CODE)
        self.assertEqual(cli.build_type_skill().skill_code, DEFAULT_VOCAB_CODE)


if __name__ == "__main__":
    unittest.main()
