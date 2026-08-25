"""085 T105 — 분류 스킬 **영속**(``src/mm_classify/persist.py``) 단위 테스트(모의 커넥션·네트워크 0).

무엇을 검증하나: 판정 결과를 DB 에 남기는 층이다. 이 층에는 **스키마가 막아 주지 못하는 앱 불변식
2건**이 걸려 있어(spec §2 · migration-reviewer 권고 2026-08-24) 그것을 테스트로 못 박는 것이 이
파일의 핵심 목적이다.

    ① **미부여(해당없음) 단독성** — ``unassigned`` 행은 같은 (자산, 스킬) 의 다른 라벨 행과 공존할
       수 없다. "어디에도 해당 없음"과 "이 라벨에 해당함"은 동시에 참일 수 없기 때문이다. PK 는
       (asset_id, skill_code, label_code) 라서 두 행이 **DB 에서는 정상**으로 들어간다 — 그래서 앱이
       막아야 한다.

    ② **라벨이 같아도 버전은 갱신된다** — 재판정은 자산×스킬 단위 **DELETE→INSERT 전체 교체**다.
       ``ON CONFLICT … DO NOTHING`` 식으로 "이미 같은 라벨이 있으니 넘어간다"를 하면 ``skill_version``
       이 옛 값에 머물고, 배치의 대상 선별 조건(version<현행)에 **영구히** 걸려 매번 같은 자산을
       다시 판정한다(무한 백필). 비유하면 검침원이 계량기를 읽고도 날짜를 안 적어 매달 같은 집을
       다시 방문하는 것이다.

또 하나의 축은 **행의 의미**다(spec §2 · 084 §2 동형): 행이 있으면 "판정했다", 없으면
"아직/실패했다"(재대상)다. 그래서 판정 실패(``ok=False``)는 **행을 만들지 않는다** — 실패를 기록하면
장애가 "판정 완료"로 굳어 영구히 재시도되지 않는다.

DB·LLM·네트워크 불필요(가짜 커넥션이 실행된 SQL 을 모은다). 실 DB 왕복은 사람 몫(T108).
"""

from __future__ import annotations

import contextlib
import unittest
import uuid
from typing import Any

from src.mm_classify.judge import SkillJudgement
from src.mm_classify.model import (
    MM_META_TYPE_SKILL_CODE,
    UNASSIGNED_LABEL_CODE,
    SkillConfigError,
    load_skill,
)
from src.mm_classify.persist import (
    DECIDED_BY_LLM,
    SkillPersistError,
    fetch_active_skills,
    fetch_asset_label_rows,
    fetch_asset_materials,
    fetch_pending_asset_ids,
    replace_asset_labels,
    skill_declaration,
    skill_from_row,
    upsert_skill,
)
from src.mm_classify.prompt import PROMPT_VERSION

# 더미 식별자(실 자산 id 를 코드 레포에 넣지 않는다 — UUIDv7 형태만 맞춘 상수).
_ASSET = "018f0000-0000-7000-8000-0000000000a1"
_ASSET2 = "018f0000-0000-7000-8000-0000000000a2"
_SKILL_ID = "018f0000-0000-7000-8000-0000000000b1"


def _skill(**over: Any):
    """더미 스킬(2라벨·multi) — spec §1 구조·값은 전부 더미.

    Args:
        **over: 최상위 키 덮어쓰기(``version``·``skill_code`` 등).

    Returns:
        검증을 통과한 ``ClassificationSkill``.
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
    return load_skill(raw)


def _row_of(skill, *, version: int | None = None, status: str = "active") -> dict[str, Any]:
    """스킬 객체를 ``mm_skill`` 조회 행 모양으로 바꾼다(JSONB 는 파싱된 dict/list 로 온다).

    Args:
        skill: 원본 스킬 객체.
        version: 행의 version. ``None`` 이면 스킬 선언값.
        status: 행 상태(``active``·``disabled``).

    Returns:
        조회 행 dict.
    """
    decl = skill_declaration(skill)
    return {
        "skill_id": uuid.UUID(_SKILL_ID),
        "skill_code": decl["skill_code"],
        "name": decl["skill"],
        "version": decl["version"] if version is None else version,
        "policy": decl["policy"],
        "labels": decl["labels"],
        "status": status,
    }


class _Cur:
    """가짜 커서 — 실행된 ``(정규화 SQL, 파라미터)`` 를 커넥션 로그에 쌓고 준비된 결과를 돌려준다."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def execute(self, sql: Any, params: Any = None) -> _Cur:
        flat = " ".join(str(sql).split())
        self._conn.log.append((flat, params))
        if self._conn.fail_on and self._conn.fail_on in flat:
            raise RuntimeError("모의 DB 오류")
        self._conn.rows = self._conn.results.pop(0) if self._conn.results else []
        return self

    def fetchall(self) -> list[Any]:
        return self._conn.rows

    def fetchone(self) -> Any:
        return self._conn.rows[0] if self._conn.rows else None

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _Conn:
    """가짜 커넥션 — ``cursor()``·``transaction()`` 만 흉내낸다(psycopg3 형상)."""

    def __init__(
        self, results: list[list[Any]] | None = None, *, fail_on: str | None = None
    ) -> None:
        self.log: list[tuple[str, Any]] = []
        self.results = list(results or [])
        self.rows: list[Any] = []
        self.fail_on = fail_on

    def cursor(self, **_k: Any) -> _Cur:
        return _Cur(self)

    @contextlib.contextmanager
    def transaction(self):
        """트랜잭션 경계를 로그에 남긴다 — 원자성(전부 아니면 전무) 검증용."""
        self.log.append(("BEGIN", None))
        try:
            yield self
        except Exception:
            self.log.append(("ROLLBACK", None))
            raise
        else:
            self.log.append(("COMMIT", None))

    def sqls(self) -> list[str]:
        """실행된 SQL 문자열만."""
        return [s for s, _p in self.log]


class TestSkillDeclaration(unittest.TestCase):
    """선언 dict — DB(JSONB)에 담는 모양이며 **load_skill 로 그대로 되돌아가야** 한다.

    왜 왕복이 계약인가: 정본은 DB 등록 행이므로(spec §1) 배치는 파일이 아니라 **행에서** 스킬을
    복원한다. 되돌아가지 않으면 등록한 스킬과 배치가 쓰는 스킬이 달라진다.
    """

    def test_선언은_load_skill_로_왕복한다(self) -> None:
        skill = _skill()
        self.assertEqual(load_skill(skill_declaration(skill)), skill)

    def test_JSON_키_not_으로_복원된다(self) -> None:
        # 파이썬 필드명은 exclusion(예약어 회피)이지만 저장·입력 형식의 키는 ``not`` 이다.
        labels = skill_declaration(_skill())["labels"]
        self.assertEqual(sorted(labels[0]), ["code", "definition", "name", "not"])
        self.assertEqual(labels[0]["not"], "나에 해당하는 내용")

    def test_정책_기본값이_채워져_저장된다(self) -> None:
        # 파일에서 생략된 값도 선언에는 **실체화**돼야 한다 — 그래야 "선언이 같은가" 비교가
        # 파일 표기 차이(생략/명시)에 흔들리지 않는다.
        skill = _skill(policy={"unassigned": "해당없음"})
        self.assertEqual(
            skill_declaration(skill)["policy"],
            {"selection": "multi", "unassigned": "해당없음", "max_labels": 30},
        )

    def test_라벨_순서를_보존한다(self) -> None:
        decl = skill_declaration(_skill())
        self.assertEqual([lb["code"] for lb in decl["labels"]], ["alpha", "beta"])


class TestSkillFromRow(unittest.TestCase):
    """DB 행 → 스킬 객체(정본 = 등록 행). 행이 깨져 있으면 조용히 쓰지 않고 거부한다."""

    def test_행에서_스킬을_복원한다(self) -> None:
        skill = _skill(version=3)
        restored = skill_from_row(_row_of(skill))
        self.assertEqual(restored, skill)

    def test_행의_version_을_따른다(self) -> None:
        # 백필 재선별 기준은 DB 버전이다 — 행의 값이 정본.
        restored = skill_from_row(_row_of(_skill(), version=7))
        self.assertEqual(restored.version, 7)

    def test_깨진_행은_거부한다(self) -> None:
        row = _row_of(_skill())
        row["labels"] = [{"code": "alpha", "name": "가라벨"}]  # definition·not 누락
        with self.assertRaises(SkillConfigError):
            skill_from_row(row)


class TestUpsertSkillRegister(unittest.TestCase):
    """신규 등록 — skill_id 는 앱 발급 UUIDv7(헌법 6조), 버전은 파일 선언값."""

    def test_없으면_INSERT_한다(self) -> None:
        conn = _Conn([[]])  # SELECT → 행 없음
        out = upsert_skill(conn, _skill())
        self.assertEqual(out["action"], "registered")
        self.assertEqual(out["version"], 1)
        self.assertIsNone(out["previous_version"])
        sqls = conn.sqls()
        self.assertTrue(any(s.startswith("SELECT") for s in sqls))
        self.assertTrue(any("INSERT INTO mm_skill" in s for s in sqls))

    def test_skill_id_는_UUIDv7_이다(self) -> None:
        conn = _Conn([[]])
        out = upsert_skill(conn, _skill())
        self.assertEqual(uuid.UUID(out["skill_id"]).version, 7)

    def test_status_는_active_로_등록한다(self) -> None:
        conn = _Conn([[]])
        upsert_skill(conn, _skill())
        insert = next(p for s, p in conn.log if "INSERT INTO mm_skill" in s)
        self.assertIn("active", [str(v) for v in insert])

    def test_skill_code_없으면_거부한다(self) -> None:
        # 등록의 자기완결성(spec 구현확정 G1) — 코드가 없으면 어느 행인지 정할 수 없다.
        skill = _skill()
        object.__setattr__(skill, "skill_code", None)
        conn = _Conn([[]])
        with self.assertRaises(SkillPersistError):
            upsert_skill(conn, skill)
        self.assertEqual(conn.log, [])  # 쓰기 시도 자체가 없어야 한다


class TestUpsertSkillRevise(unittest.TestCase):
    """개정 — 선언이 달라졌을 때만 version+1(백필 재선별의 방아쇠)."""

    def _revised(self) -> Any:
        """정의문을 고친 스킬(= 파일럿 발견 1: 정의문이 곧 분류)."""
        return _skill(
            labels=[
                {
                    "code": "alpha",
                    "name": "가라벨",
                    "definition": "가에 해당하되 다른 뜻으로 좁힌 정의",
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

    def test_선언이_다르면_version_이_1_오른다(self) -> None:
        conn = _Conn([[_row_of(_skill(), version=4)]])
        out = upsert_skill(conn, self._revised())
        self.assertEqual(out["action"], "revised")
        self.assertEqual(out["previous_version"], 4)
        self.assertEqual(out["version"], 5)
        self.assertTrue(any("UPDATE mm_skill" in s for s in conn.sqls()))

    def test_updated_at_을_갱신한다(self) -> None:
        # 자동 갱신 트리거가 없으므로 SET 으로 직접 적는다(resolution_persist 관례).
        conn = _Conn([[_row_of(_skill(), version=1)]])
        upsert_skill(conn, self._revised())
        update = next(s for s in conn.sqls() if "UPDATE mm_skill" in s)
        self.assertIn("updated_at = now()", update)

    def test_skill_id_는_새로_발급하지_않는다(self) -> None:
        # id 를 바꾸면 같은 스킬이 두 개로 갈라진다(자연키는 skill_code).
        conn = _Conn([[_row_of(_skill(), version=1)]])
        out = upsert_skill(conn, self._revised())
        self.assertEqual(out["skill_id"], _SKILL_ID)

    def test_status_는_건드리지_않는다(self) -> None:
        # 중단한 스킬(disabled)이 개정만으로 되살아나면 안 된다(활성화는 별도 결정).
        conn = _Conn([[_row_of(_skill(), version=1, status="disabled")]])
        upsert_skill(conn, self._revised())
        update = next(s for s in conn.sqls() if "UPDATE mm_skill" in s)
        self.assertNotIn("status", update)


class TestUpsertSkillIdempotent(unittest.TestCase):
    """같은 선언 재등록은 **아무 것도 하지 않는다** — 헛 백필 방지(SC-05).

    왜 중요한가: 개정마다 version 이 오르고, version 이 오르면 배치가 전 자산을 다시 판정한다.
    같은 파일을 두 번 apply 한 것만으로 전량 재분류가 돌면 비용이 그대로 낭비된다.
    """

    def test_동일_선언이면_쓰기가_없다(self) -> None:
        conn = _Conn([[_row_of(_skill(), version=2)]])
        out = upsert_skill(conn, _skill())
        self.assertEqual(out["action"], "unchanged")
        self.assertEqual(out["version"], 2)
        self.assertEqual([s for s in conn.sqls() if not s.startswith("SELECT")], [])

    def test_version_숫자만_다르면_개정이_아니다(self) -> None:
        # 내용(이름·정책·라벨)이 같으면 파일의 version 표기가 달라도 재분류할 이유가 없다.
        conn = _Conn([[_row_of(_skill(), version=2)]])
        out = upsert_skill(conn, _skill(version=9))
        self.assertEqual(out["action"], "unchanged")
        self.assertEqual(out["version"], 2)
        self.assertEqual(out["declared_version"], 9)


class TestReplaceAssetLabelsSuccess(unittest.TestCase):
    """성공 판정 영속 — 자산×스킬 **한 트랜잭션 DELETE→INSERT 전체 교체**(앱 불변식 ②)."""

    def _judgement(self, *codes: str) -> SkillJudgement:
        """라벨 코드만 지정한 성공 판정(이름은 검증에 쓰이지 않는다).

        Args:
            *codes: 저장할 label_code 들.

        Returns:
            ``ok=True`` 인 판정.
        """
        return SkillJudgement(ok=True, label_names=codes, label_codes=codes)

    def _run(self, judgement: SkillJudgement, **kw: Any) -> tuple[_Conn, int]:
        """가짜 커넥션으로 영속을 실행한다.

        Args:
            judgement: 저장할 판정.
            **kw: ``replace_asset_labels`` 추가 인자.

        Returns:
            ``(커넥션, 삽입 행 수)``.
        """
        conn = _Conn()
        n = replace_asset_labels(
            conn,
            asset_id=_ASSET,
            skill_code="sample_skill",
            skill_version=3,
            judgement=judgement,
            **kw,
        )
        return conn, n

    def test_DELETE_후_INSERT_순서로_교체한다(self) -> None:
        conn, n = self._run(self._judgement("alpha", "beta"))
        self.assertEqual(n, 2)
        kinds = [
            s.split()[0] if s not in ("BEGIN", "COMMIT", "ROLLBACK") else s
            for s in conn.sqls()
        ]
        self.assertEqual(kinds, ["BEGIN", "DELETE", "INSERT", "INSERT", "COMMIT"])

    def test_교체는_한_트랜잭션이다(self) -> None:
        # 부분 반영(라벨 절반만 교체)이 남으면 그 자산의 판정이 사실과 어긋난다.
        conn, _ = self._run(self._judgement("alpha"))
        self.assertEqual(conn.sqls()[0], "BEGIN")
        self.assertEqual(conn.sqls()[-1], "COMMIT")

    def test_DELETE_는_그_자산_그_스킬만_지운다(self) -> None:
        conn, _ = self._run(self._judgement("alpha"))
        sql, params = next((s, p) for s, p in conn.log if s.startswith("DELETE"))
        self.assertIn("asset_id = %s", sql)
        self.assertIn("skill_code = %s", sql)
        self.assertEqual(tuple(params), (_ASSET, "sample_skill"))

    def test_INSERT_행에_버전_두_개가_실린다(self) -> None:
        conn, _ = self._run(self._judgement("alpha"))
        _sql, params = next((s, p) for s, p in conn.log if s.startswith("INSERT"))
        self.assertEqual(
            tuple(params),
            (_ASSET, "sample_skill", "alpha", 3, PROMPT_VERSION, DECIDED_BY_LLM),
        )

    def test_decided_by_기본값은_llm_이다(self) -> None:
        self.assertEqual(DECIDED_BY_LLM, "llm")

    def test_라벨이_같아도_새_버전으로_다시_쓴다(self) -> None:
        # 앱 불변식 ② — 기존 행과 라벨이 같아도 version 이 갱신돼야 재선별에서 빠진다.
        conn, n = self._run(self._judgement("alpha"), prompt_version="mm_classify.v9")
        self.assertEqual(n, 1)
        _sql, params = next((s, p) for s, p in conn.log if s.startswith("INSERT"))
        self.assertEqual(params[3], 3)
        self.assertEqual(params[4], "mm_classify.v9")

    def test_DO_NOTHING_금지(self) -> None:
        # ON CONFLICT DO NOTHING 은 version 을 옛 값에 묶어 무한 재선별을 만든다(불변식 ②).
        conn, _ = self._run(self._judgement("alpha"))
        joined = " ".join(conn.sqls()).upper()
        self.assertNotIn("DO NOTHING", joined)
        self.assertNotIn("ON CONFLICT", joined)

    def test_미부여_단독은_1행으로_기록한다(self) -> None:
        # "해당없음"도 판정 이력이다(실패와 구분 — 행 존재 = 판정했다).
        conn, n = self._run(self._judgement(UNASSIGNED_LABEL_CODE))
        self.assertEqual(n, 1)
        _sql, params = next((s, p) for s, p in conn.log if s.startswith("INSERT"))
        self.assertEqual(params[2], UNASSIGNED_LABEL_CODE)

    def test_라벨_순서를_그대로_쓴다(self) -> None:
        # judge 가 설정 순서로 정규화한 순서를 영속 계층이 다시 흔들지 않는다(결정성).
        conn, _ = self._run(self._judgement("beta", "alpha"))
        codes = [p[2] for s, p in conn.log if s.startswith("INSERT")]
        self.assertEqual(codes, ["beta", "alpha"])


class TestReplaceAssetLabelsGuards(unittest.TestCase):
    """실패·모순 입력 — 행을 만들지 않는다(재대상 유지 · 앱 불변식 ①)."""

    def test_실패_판정은_행을_만들지_않는다(self) -> None:
        # ok=False 를 저장하면 LLM 장애가 "판정 완료"로 굳어 영구히 재시도되지 않는다.
        conn = _Conn()
        n = replace_asset_labels(
            conn,
            asset_id=_ASSET,
            skill_code="sample_skill",
            skill_version=1,
            judgement=SkillJudgement(ok=False),
        )
        self.assertEqual(n, 0)
        self.assertEqual(conn.log, [])

    def test_예약_코드로는_쓸_수_없다(self) -> None:
        # 진입점(fetch_active_skills)만 막으면 이 쓰기를 직접 부르는 경로로 우회된다 —
        # FK 는 성립하므로(mm_skill 에 행이 있다) DB 가 막아주지 않는다. 앱이 막아야 한다.
        conn = _Conn()
        with self.assertRaises(SkillPersistError):
            replace_asset_labels(
                conn,
                asset_id=_ASSET,
                skill_code=MM_META_TYPE_SKILL_CODE,
                skill_version=1,
                judgement=SkillJudgement(ok=True, label_names=("인물",), label_codes=("person",)),
            )
        self.assertEqual(conn.log, [])  # 검사가 쓰기보다 먼저다

    def test_예약_코드로는_대상_선별도_못_한다(self) -> None:
        # 여기서 안 막으면 "타입 어휘로 분류할 자산 전량"이라는 무의미한 목록이 나오고
        # 배치가 그것을 그대로 LLM 판정에 태운다(실제 사고 직전까지 갔던 경로).
        conn = _Conn()
        with self.assertRaises(SkillPersistError):
            fetch_pending_asset_ids(conn, skill_code=MM_META_TYPE_SKILL_CODE, skill_version=1)
        self.assertEqual(conn.log, [])

    def test_미부여와_다른_라벨_공존은_거부한다(self) -> None:
        # 앱 불변식 ① — PK 는 (asset,skill,label) 이라 DB 는 두 행을 정상으로 받는다. 앱이 막는다.
        conn = _Conn()
        with self.assertRaises(SkillPersistError):
            replace_asset_labels(
                conn,
                asset_id=_ASSET,
                skill_code="sample_skill",
                skill_version=1,
                judgement=SkillJudgement(
                    ok=True,
                    label_names=("가라벨", "해당없음"),
                    label_codes=("alpha", UNASSIGNED_LABEL_CODE),
                ),
            )
        self.assertEqual(conn.log, [])  # 검사가 쓰기보다 먼저다(부분 반영 없음)

    def test_성공인데_라벨이_비면_거부한다(self) -> None:
        # judge 계약상 나올 수 없는 값 — 조용히 0행을 쓰면 "판정했는데 라벨 없음"이 남는다.
        conn = _Conn()
        with self.assertRaises(SkillPersistError):
            replace_asset_labels(
                conn,
                asset_id=_ASSET,
                skill_code="sample_skill",
                skill_version=1,
                judgement=SkillJudgement(ok=True),
            )
        self.assertEqual(conn.log, [])

    def test_중복_라벨은_거부한다(self) -> None:
        # PK 충돌로 터질 입력을 미리 끊는다(같은 라벨 두 행은 뜻이 없다).
        conn = _Conn()
        with self.assertRaises(SkillPersistError):
            replace_asset_labels(
                conn,
                asset_id=_ASSET,
                skill_code="sample_skill",
                skill_version=1,
                judgement=SkillJudgement(
                    ok=True, label_names=("가라벨", "가라벨"), label_codes=("alpha", "alpha")
                ),
            )
        self.assertEqual(conn.log, [])

    def test_INSERT_실패는_전체_롤백된다(self) -> None:
        # DELETE 만 반영되면 그 자산은 라벨을 잃는다 — 트랜잭션이 그것을 되돌린다.
        conn = _Conn(fail_on="INSERT INTO asset_mm_skill_label")
        with self.assertRaises(RuntimeError):
            replace_asset_labels(
                conn,
                asset_id=_ASSET,
                skill_code="sample_skill",
                skill_version=1,
                judgement=SkillJudgement(ok=True, label_names=("가라벨",), label_codes=("alpha",)),
            )
        self.assertEqual(conn.sqls()[-1], "ROLLBACK")


class TestFetchActiveSkills(unittest.TestCase):
    """배치 대상 스킬 조회 — active 만·결정적 정렬·id 는 str()."""

    def test_active_만_결정적_정렬로_읽는다(self) -> None:
        conn = _Conn([[_row_of(_skill())]])
        rows = fetch_active_skills(conn)
        sql = conn.sqls()[0]
        self.assertIn("FROM mm_skill", sql)
        self.assertIn("status = %s", sql)
        self.assertEqual(conn.log[0][1], ("active",))
        self.assertIn("ORDER BY skill_code", sql)
        self.assertEqual(len(rows), 1)

    def test_조회행_id_는_문자열이다(self) -> None:
        # 조회/행-정형 함수는 id 를 str() 로 통일한다(graph_query 관례).
        conn = _Conn([[_row_of(_skill())]])
        row = fetch_active_skills(conn)[0]
        self.assertIsInstance(row["skill_id"], str)
        self.assertEqual(row["skill_id"], _SKILL_ID)

    def test_행이_스킬로_복원_가능하다(self) -> None:
        conn = _Conn([[_row_of(_skill(), version=2)]])
        skill = skill_from_row(fetch_active_skills(conn)[0])
        self.assertEqual(skill.version, 2)


class TestFetchPendingAssetIds(unittest.TestCase):
    """대상 선별 — registered × (판정 행 없음 또는 구버전). 의료 포함 균일."""

    def _run(self, **kw: Any) -> tuple[_Conn, list[str]]:
        """대상 선별을 실행한다.

        Args:
            **kw: ``fetch_pending_asset_ids`` 추가 인자.

        Returns:
            ``(커넥션, 자산 id 목록)``.
        """
        conn = _Conn([[(uuid.UUID(_ASSET),), (uuid.UUID(_ASSET2),)]])
        ids = fetch_pending_asset_ids(conn, skill_code="sample_skill", skill_version=3, **kw)
        return conn, ids

    def test_registered_자산만_본다(self) -> None:
        conn, _ = self._run()
        self.assertIn("a.status = 'registered'", conn.sqls()[0])

    def test_판정행_없음과_구버전을_함께_집는다(self) -> None:
        # 현행 버전 행이 하나라도 있으면 제외 = (행 없음) OR (전부 구버전) 과 같다.
        # 전체 교체 저장이라 한 자산·한 스킬의 행들은 언제나 같은 버전이다.
        conn, _ = self._run()
        sql = conn.sqls()[0]
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("asset_mm_skill_label", sql)
        self.assertIn("skill_version >= %s", sql)
        self.assertEqual(conn.log[0][1], ("sample_skill", 3))

    def test_LEFT_JOIN_으로_세지_않는다(self) -> None:
        # multi 는 자산당 1..N행이라 LEFT JOIN 은 같은 자산을 여러 번 돌려준다(중복 판정 호출).
        self.assertNotIn("LEFT JOIN", self._run()[0].sqls()[0].upper())

    def test_의료_제외_조건이_없다(self) -> None:
        # 라벨은 닫힌 어휘라 PHI 무관 — 주제 분류와 동일하게 도메인 균일(헌법 4조·2026-07-23 결정).
        sql = self._run()[0].sqls()[0]
        self.assertNotIn("domain_label", sql)
        self.assertNotIn("medical", sql)

    def test_결정적_정렬과_str_반환(self) -> None:
        conn, ids = self._run()
        self.assertIn("ORDER BY a.asset_id", conn.sqls()[0])
        self.assertEqual(ids, [_ASSET, _ASSET2])
        self.assertTrue(all(isinstance(i, str) for i in ids))

    def test_limit_은_절과_파라미터로_붙는다(self) -> None:
        conn, _ = self._run(limit=50)
        self.assertIn("LIMIT %s", conn.sqls()[0])
        self.assertEqual(conn.log[0][1], ("sample_skill", 3, 50))

    def test_limit_없으면_LIMIT_절도_없다(self) -> None:
        self.assertNotIn("LIMIT", self._run()[0].sqls()[0])


class TestFetchAssetLabelRows(unittest.TestCase):
    """자산 하나의 판정 라벨 행 조회 — **OS 부분 업데이트 값의 원천**(spec §5).

    왜 "방금 쓴 것"만으로는 안 되나: OS 의 ``mm_skill_labels`` 는 필드 하나에 **모든 스킬의 라벨**을
    함께 담고, 부분 갱신은 그 필드를 통째로 덮어쓴다. 스킬 A 를 판정한 뒤 A 의 라벨만 실어 보내면
    같은 자산의 스킬 B 라벨이 색인에서 사라진다. 그래서 판정 후 **그 자산의 전 스킬 행을 다시 읽어**
    키를 만든다.

    활성 스킬만 읽는 이유: 중단(``disabled``)한 스킬의 라벨은 패싯 축에 노출하지 않는다(spec §6).
    행은 이력으로 보존되지만 색인에는 싣지 않으므로, 그 자산이 다시 판정될 때 자연히 정리된다.
    """

    def _rows(self) -> list[Any]:
        return [
            {"skill_code": "sk_a", "label_code": "alpha"},
            {"skill_code": "sk_b", "label_code": "beta"},
        ]

    def test_활성_스킬_행만_읽는다(self) -> None:
        conn = _Conn([self._rows()])
        rows = fetch_asset_label_rows(conn, _ASSET)
        sql, params = conn.log[0]
        self.assertIn("FROM asset_mm_skill_label", sql)
        self.assertIn("JOIN mm_skill", sql)
        self.assertIn("s.status = %s", sql)
        self.assertEqual(params, (_ASSET, "active"))
        self.assertEqual(rows, self._rows())

    def test_결정적_정렬로_읽는다(self) -> None:
        # 같은 자산이면 같은 키 순서가 나와야 색인 값이 흔들리지 않는다(결정성).
        conn = _Conn([self._rows()])
        fetch_asset_label_rows(conn, _ASSET)
        self.assertIn("ORDER BY l.skill_code, l.label_code", conn.sqls()[0])

    def test_행이_없으면_빈_목록이다(self) -> None:
        # 판정이 없거나 전부 비활성 스킬이면 빈 목록 → 호출부가 색인을 빈 값으로 덮어 정리한다.
        conn = _Conn([[]])
        self.assertEqual(fetch_asset_label_rows(conn, _ASSET), [])

    def test_코드는_문자열로_정규화한다(self) -> None:
        conn = _Conn([[{"skill_code": "sk_a", "label_code": "alpha"}]])
        row = fetch_asset_label_rows(conn, _ASSET)[0]
        self.assertIsInstance(row["skill_code"], str)
        self.assertIsInstance(row["label_code"], str)


class TestFetchAssetMaterials(unittest.TestCase):
    """판정 재료 조회 — 요약·키워드. 메타가 없는 자산도 **빠뜨리지 않는다**."""

    def _rows(self) -> list[Any]:
        return [
            {
                "asset_id": uuid.UUID(_ASSET),
                "summary": "요약 문장",
                "keywords": ["가키워드", "나키워드"],
            },
            {"asset_id": uuid.UUID(_ASSET2), "summary": None, "keywords": None},
        ]

    def test_LEFT_JOIN_으로_메타_없는_자산도_회수한다(self) -> None:
        # INNER JOIN 이면 메타 없는 자산은 재료가 없어 판정도 못 하고 행도 안 생겨
        # **영구히 재선별**된다(무한 배치). 빈 재료로라도 판정해 끝내야 한다.
        conn = _Conn([self._rows()])
        fetch_asset_materials(conn, limit=2)
        self.assertIn("LEFT JOIN asset_metadata", conn.sqls()[0])

    def test_없는_요약과_키워드를_빈값으로_정규화한다(self) -> None:
        conn = _Conn([self._rows()])
        mats = fetch_asset_materials(conn, limit=2)
        self.assertEqual(mats[0]["summary"], "요약 문장")
        self.assertEqual(mats[0]["keywords"], ["가키워드", "나키워드"])
        self.assertEqual(mats[1]["summary"], "")
        self.assertEqual(mats[1]["keywords"], [])

    def test_asset_id_는_문자열이다(self) -> None:
        conn = _Conn([self._rows()])
        self.assertEqual(fetch_asset_materials(conn, limit=2)[0]["asset_id"], _ASSET)

    def test_registered_와_결정적_정렬(self) -> None:
        conn = _Conn([self._rows()])
        fetch_asset_materials(conn, limit=2)
        sql = conn.sqls()[0]
        self.assertIn("a.status = 'registered'", sql)
        self.assertIn("ORDER BY a.asset_id", sql)

    def test_asset_ids_를_주면_그_자산만_읽는다(self) -> None:
        conn = _Conn([self._rows()[:1]])
        fetch_asset_materials(conn, asset_ids=[_ASSET])
        sql, params = conn.log[0]
        self.assertIn("a.asset_id = ANY(%s)", sql)
        self.assertEqual(params, ([_ASSET],))

    def test_limit_은_파라미터로_붙는다(self) -> None:
        conn = _Conn([self._rows()])
        fetch_asset_materials(conn, limit=7)
        self.assertIn("LIMIT %s", conn.sqls()[0])
        self.assertEqual(conn.log[0][1], (7,))


if __name__ == "__main__":
    unittest.main()
