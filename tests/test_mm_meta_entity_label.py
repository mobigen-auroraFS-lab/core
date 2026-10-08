"""개체 갈래 — 증분 재판정 선별·상태 읽기·지문 저장 단위 테스트 (spec 104 T003~T006).

무엇을 봉인하나: 개체 갈래 배치가 **「아직 판정 안 된 개체」와 「판정 재료가 바뀐 개체」만**
판정하게 하는 코어 계약이다. 틀리면 조용히 LLM 비용을 태우거나(헛일), 일부 개체를 영영 판정하지
않는다(굶주림). 비유: 사서가 표지가 바뀐 책과 스티커가 없는 책에만 분류 스티커를 붙이는 규칙이다.

    ① 지문(``label_material_hash``) — 개체 임베딩과 **같은 지문**이어야 한다. 그리고 이 모듈을
       불러오는 것만으로 임베딩 라이브러리(torch)가 딸려오면 안 된다.
    ② 선별(``select_label_work``) — (개체, 스킬) 짝마다 사유를 정하고, **상한은 걸러낸 뒤에**
       건다. 상한을 먼저 걸면 구성원 많은 상위만 매번 다시 판정되고 나머지는 굶는다.
    ③ 상태 읽기(``fetch_label_state``) — 같은 (개체, 스킬)의 행들에 값이 섞이면 「혼합」으로 본다.
    ④ 저장(``replace_entity_labels``) — 모든 라벨 행에 같은 지문을 쓴다. 옛 호출(지문 없음)은 NULL.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from src.mm_classify.judge import JudgeFailure, SkillJudgement
from src.mm_classify.model import UNASSIGNED_LABEL_CODE, load_skill
from src.mm_meta.entity_label import (
    REASON_HASH_CHANGED,
    REASON_HASH_NULL,
    REASON_MIXED,
    REASON_NEW,
    REASON_PROMPT_VERSION,
    REASON_SKILL_VERSION,
    EntityLabelError,
    LabelState,
    LabelWork,
    fetch_label_state,
    label_material_hash,
    replace_entity_labels,
    select_label_work,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 판정 문안 판(파이프 배치가 쓰는 값과 같은 모양의 더미).
_PV = "entity_label.v1"

# 재료 더미와 그 지문. 선별은 후보의 재료에서 지문을 **직접 계산**하므로(W3) 「지금 지문」을 바꾸려면
# 재료를 바꿔야 한다 — 지문 칸을 따로 넣어 흉내 낼 수 없다.
_M1 = "아이유(인물). 재료 1"
_M2 = "아이유(인물). 재료 2"
_H1 = label_material_hash(_M1)
_H2 = label_material_hash(_M2)


# ── 공용 더미 ────────────────────────────────────────────────────────────────


def _skill(code: str, version: int = 1) -> SimpleNamespace:
    """선별 함수가 읽는 두 속성(``skill_code``·``version``)만 가진 가짜 스킬.

    Args:
        code: 스킬 코드.
        version: DB 스킬 판.

    Returns:
        가짜 스킬 객체.
    """
    return SimpleNamespace(skill_code=code, version=version)


def _cand(
    uid: str,
    members: int,
    *,
    m: str = _M1,
    etype: str = "인물",
) -> dict[str, Any]:
    """후보 개체 한 행(파이프가 재료를 조립한 모양 — 지문은 선별 함수가 재료에서 계산한다).

    Args:
        uid: 개체 표기 키.
        members: 구성 자산 수.
        m: 지금 판정 재료. 기본 ``_M1``(지문 ``_H1``) — 저장 상태 ``_state()`` 기본값과 같은 재료다.
        etype: 개체 타입.

    Returns:
        후보 dict.
    """
    return {
        "entity_type": etype,
        "entity_uid": uid,
        "members": members,
        "material": m,
    }


def _state(
    h: str | None = _H1,
    *,
    sv: int = 1,
    pv: str = _PV,
    mixed: bool = False,
) -> LabelState:
    """저장된 (개체, 스킬) 상태 하나.

    Args:
        h: 저장된 지문(``None`` = 지문 없는 옛 판정).
        sv: 저장된 스킬 판.
        pv: 저장된 문안 판.
        mixed: 같은 짝 안에서 값이 섞였는가.

    Returns:
        ``LabelState``.
    """
    return LabelState(material_hash=h, skill_version=sv, prompt_version=pv, mixed=mixed)


def _uids(works: list[LabelWork]) -> list[str]:
    """작업 목록의 개체 키만 순서대로 뽑는다.

    Args:
        works: 선별 결과.

    Returns:
        ``entity_uid`` 목록.
    """
    return [w.entity_uid for w in works]


# ── ① 지문 ──────────────────────────────────────────────────────────────────


class TestLabelMaterialHash(unittest.TestCase):
    """판정 재료 지문 — 개체 임베딩과 같은 SHA-256 64자."""

    def test_같은_입력은_같은_지문(self) -> None:
        self.assertEqual(label_material_hash("아이유(인물). 가수"),
                         label_material_hash("아이유(인물). 가수"))

    def test_지문은_16진_64자(self) -> None:
        # DB 칸 CHAR(64) 와 폭이 같아야 한다 — 짧으면 공백으로 채워져 영영 같다고 안 나온다.
        got = label_material_hash("김치(음식). 발효 식품")
        self.assertEqual(len(got), 64)
        self.assertRegex(got, r"^[0-9a-f]{64}$")

    def test_입력이_다르면_지문이_다르다(self) -> None:
        self.assertNotEqual(label_material_hash("김치(음식)."), label_material_hash("김치(작품)."))

    def test_개체_임베딩_지문과_같은_값(self) -> None:
        # 계획상 「같은 함수」 — 두 표의 지문이 다른 규칙으로 갈리면 비교·진단이 꼬인다.
        from src.mm_meta.entity_embedding import material_hash

        for text in ("", "아이유(인물).", "김치(음식). 발효 식품이다 구성 자료: 가 / 나", "a\nb"):
            with self.subTest(text=text):
                self.assertEqual(label_material_hash(text), material_hash(text))

    def test_모듈을_불러와도_임베딩_라이브러리가_딸려오지_않는다(self) -> None:
        # 🔴 이 모듈은 패키지(src.mm_meta)가 즉시 import 한다. 지문 하나 때문에 임베딩 모듈을
        #    최상단에서 불러오면 sentence_transformers(torch)가 딸려와 패키지 import 가 수 초로
        #    무거워진다. 다른 테스트가 이미 불러 놓았을 수 있어 깨끗한 하위 프로세스로 본다.
        code = (
            "import sys; import src.mm_meta.entity_label as m; "
            "m.label_material_hash('x'); "
            "print(int('sentence_transformers' in sys.modules))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "0", out.stderr)


# ── ② 선별 ──────────────────────────────────────────────────────────────────


class TestSelectLabelWorkReasons(unittest.TestCase):
    """(개체, 스킬) 짝마다 사유 판정."""

    def _one(self, st: LabelState | None, *, skill_version: int = 1,
             m: str = _M1) -> tuple[list[LabelWork], dict[str, int]]:
        """개체 1 × 스킬 1 로 선별을 돌린다.

        Args:
            st: 저장 상태(``None`` = 행 없음).
            skill_version: 지금 스킬 판.
            m: 지금 판정 재료(지문은 선별 함수가 이 재료에서 계산한다).

        Returns:
            ``select_label_work`` 결과.
        """
        state = {} if st is None else {("인물", "아이유", "genre"): st}
        return select_label_work(
            [_cand("아이유", 10, m=m)], state, [_skill("genre", skill_version)],
            prompt_version=_PV,
        )

    def test_상태가_없으면_new(self) -> None:
        # 미판정·지난 실패(실패는 행을 남기지 않는다) 모두 여기로 온다.
        works, stats = self._one(None)
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0].skill_codes, ("genre",))
        self.assertEqual(works[0].reasons, (REASON_NEW,))
        self.assertEqual(stats["pairs_new"], 1)

    def test_모두_같으면_건너뛴다(self) -> None:
        # SC-01 — 재료·스킬 판·문안 판이 그대로면 LLM 을 부르지 않는다.
        works, stats = self._one(_state())
        self.assertEqual(works, [])
        self.assertEqual(stats["skipped_unchanged"], 1)
        self.assertEqual(stats["need"], 0)

    def test_지문이_다르면_hash_changed(self) -> None:
        # SC-03 — 설명문·구성 요약이 바뀌면 재료 지문이 바뀐다.
        works, stats = self._one(_state(_H2))
        self.assertEqual(works[0].reasons, (REASON_HASH_CHANGED,))
        self.assertEqual(stats["pairs_hash_changed"], 1)

    def test_지문이_null이면_hash_null(self) -> None:
        # 지문 칸이 생기기 전 행 — 어떤 재료로 판정했는지 모르므로 한 번 다시 판정한다.
        works, stats = self._one(_state(None))
        self.assertEqual(works[0].reasons, (REASON_HASH_NULL,))
        self.assertEqual(stats["pairs_hash_null"], 1)

    def test_스킬_판이_다르면_skill_version(self) -> None:
        # SC-04 — 스킬 정의가 개정되면(DB 판 카운터 증가) 다시 판정한다.
        works, stats = self._one(_state(sv=1), skill_version=2)
        self.assertEqual(works[0].reasons, (REASON_SKILL_VERSION,))
        self.assertEqual(stats["pairs_skill_version"], 1)

    def test_문안_판이_다르면_prompt_version(self) -> None:
        # SC-04 — 판정 문안이 바뀌면 다시 판정한다.
        works, stats = self._one(_state(pv="entity_label.v0"))
        self.assertEqual(works[0].reasons, (REASON_PROMPT_VERSION,))
        self.assertEqual(stats["pairs_prompt_version"], 1)

    def test_혼합이면_다른_값이_같아도_mixed(self) -> None:
        # 같은 짝의 행들 값이 섞였다면(부분 실패·옛 코드 혼입) 대표값을 믿을 수 없다.
        works, stats = self._one(_state(mixed=True))
        self.assertEqual(works[0].reasons, (REASON_MIXED,))
        self.assertEqual(stats["pairs_mixed"], 1)

    def test_혼합이_지문_null보다_먼저다(self) -> None:
        works, _ = self._one(_state(None, mixed=True))
        self.assertEqual(works[0].reasons, (REASON_MIXED,))

    def test_지문과_판이_함께_바뀌면_지문_사유(self) -> None:
        # 사유는 짝마다 하나 — 우선순위 첫 번째(지문)로 센다(집계가 이중으로 세지 않게).
        works, stats = self._one(_state(_H2, sv=1, pv="x"), skill_version=2)
        self.assertEqual(works[0].reasons, (REASON_HASH_CHANGED,))
        self.assertEqual(stats["pairs_hash_changed"], 1)
        self.assertEqual(stats["pairs_skill_version"], 0)
        self.assertEqual(stats["pairs_prompt_version"], 0)


class TestSelectLabelWorkGrouping(unittest.TestCase):
    """개체 단위 묶음 — 판정할 스킬만 싣는다."""

    def test_한_개체에서_일부_스킬만_판정한다(self) -> None:
        # 스킬 genre 는 그대로, topic 은 미판정 → topic 만 판정(재료 조립은 개체당 1회).
        state = {("인물", "아이유", "genre"): _state()}
        works, stats = select_label_work(
            [_cand("아이유", 10)], state, [_skill("genre"), _skill("topic")],
            prompt_version=_PV,
        )
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0].skill_codes, ("topic",))
        self.assertEqual(works[0].reasons, (REASON_NEW,))
        self.assertEqual(stats["need"], 1)
        self.assertEqual(stats["skipped_unchanged"], 0)

    def test_스킬_코드는_오름차순이고_사유와_자리가_맞는다(self) -> None:
        state = {("인물", "아이유", "alpha"): _state(None)}
        works, _ = select_label_work(
            [_cand("아이유", 10)], state,
            [_skill("zeta"), _skill("alpha"), _skill("mid")],
            prompt_version=_PV,
        )
        self.assertEqual(works[0].skill_codes, ("alpha", "mid", "zeta"))
        self.assertEqual(works[0].reasons, (REASON_HASH_NULL, REASON_NEW, REASON_NEW))

    def test_작업에_재료와_지문이_실린다(self) -> None:
        # 파이프가 재료를 다시 조립하지 않고 그대로 판정·저장에 쓴다.
        c = _cand("아이유", 7, m=_M2)
        works, _ = select_label_work([c], {}, [_skill("genre")], prompt_version=_PV)
        w = works[0]
        self.assertEqual((w.entity_type, w.entity_uid, w.members), ("인물", "아이유", 7))
        self.assertEqual(w.material, c["material"])
        self.assertEqual(w.material_hash, _H2)

    def test_후보의_지문_칸은_읽지_않고_재료에서_계산한다(self) -> None:
        # 🔴 W3 — 후보가 다른 재료의 지문을 들고 와도 저장할 지문은 **판정할 재료** 기준이어야 한다.
        #    후보 칸을 믿으면 「A 재료로 판정하고 B 지문을 저장」해 다음 배치가 거짓으로 건너뛴다.
        c = {**_cand("아이유", 7, m=_M2), "material_hash": _H1}   # 틀린 지문(다른 재료의 것)
        works, _ = select_label_work([c], {}, [_skill("genre")], prompt_version=_PV)
        self.assertEqual(works[0].material_hash, label_material_hash(_M2))
        self.assertEqual(works[0].material_hash, _H2)
        self.assertEqual(works[0].material, _M2)

    def test_후보의_틀린_지문이_건너뜀_판단을_흔들지_않는다(self) -> None:
        # 비교도 재료 기준 — 후보 칸이 「그대로」라고 주장해도 재료가 바뀌었으면 다시 판정하고,
        #  후보 칸이 엉뚱해도 재료가 그대로면 건너뛴다.
        state = {("인물", "아이유", "genre"): _state(_H1)}
        changed = {**_cand("아이유", 7, m=_M2), "material_hash": _H1}
        works, stats = select_label_work([changed], state, [_skill("genre")], prompt_version=_PV)
        self.assertEqual(works[0].reasons, (REASON_HASH_CHANGED,))
        self.assertEqual(stats["pairs_hash_changed"], 1)

        same = {**_cand("아이유", 7, m=_M1), "material_hash": "0" * 64}
        works, stats = select_label_work([same], state, [_skill("genre")], prompt_version=_PV)
        self.assertEqual(works, [])
        self.assertEqual(stats["skipped_unchanged"], 1)

    def test_비활성_스킬의_상태는_무시한다(self) -> None:
        # 상태 표에는 지금 돌리지 않는 스킬의 행이 있을 수 있다 — 판정 대상이 아니다.
        state = {("인물", "아이유", "genre"): _state(),
                 ("인물", "아이유", "retired"): _state(_H2)}
        works, stats = select_label_work(
            [_cand("아이유", 10)], state, [_skill("genre")], prompt_version=_PV,
        )
        self.assertEqual(works, [])
        self.assertEqual(stats["pairs_hash_changed"], 0)

    def test_같은_개체가_두_번_오면_거부한다(self) -> None:
        # 후보는 개체당 1행이어야 한다 — 두 번 오면 같은 개체를 두 번 판정하고, 어느 재료가
        #  이기는지가 입력 순서에 달려 결정성이 깨진다.
        with self.assertRaises(ValueError):
            select_label_work(
                [_cand("아이유", 10), _cand("아이유", 10, m=_M2)], {}, [_skill("genre")],
                prompt_version=_PV,
            )

    def test_타입이_다르면_다른_개체다(self) -> None:
        # 같은 표기라도 타입이 다르면 다른 대상(김밥 음식/작품) — 중복으로 보지 않는다.
        works, _ = select_label_work(
            [_cand("김밥", 5, etype="음식"), _cand("김밥", 5, etype="작품")], {},
            [_skill("genre")], prompt_version=_PV,
        )
        self.assertEqual([(w.entity_type, w.entity_uid) for w in works],
                         [("음식", "김밥"), ("작품", "김밥")])


class TestSelectLabelWorkOrderAndLimit(unittest.TestCase):
    """정렬·상한 — 굶주림 해소와 결정성."""

    def test_상한은_걸러낸_뒤에_건다_굶주림_해소(self) -> None:
        # 🔴 SC-02 회귀 — 구성원 많은 A·B 는 이미 같은 재료로 판정됐고, 작은 C 는 미판정이다.
        #    상한을 먼저 걸면(옛 방식: 구성원 순 위에서 1개) 매번 A 만 다시 판정하고 C 는 굶는다.
        state = {("인물", "A", "genre"): _state(), ("인물", "B", "genre"): _state()}
        cands = [_cand("A", 100), _cand("B", 50), _cand("C", 3)]
        works, stats = select_label_work(cands, state, [_skill("genre")],
                                         prompt_version=_PV, limit=1)
        self.assertEqual(_uids(works), ["C"])
        self.assertEqual(stats["visible"], 3)
        self.assertEqual(stats["skipped_unchanged"], 2)
        self.assertEqual(stats["need"], 1)
        self.assertEqual(stats["selected"], 1)

    def test_상한_반복으로_전량이_판정된다(self) -> None:
        # SC-02 — 상한 2 로 「판정 → 저장 상태 갱신」을 반복하면 결국 전부 판정되고 그 뒤는 0.
        cands = [_cand(f"e{i:02d}", 10 - i) for i in range(5)]
        state: dict[tuple[str, str, str], LabelState] = {}
        judged: list[str] = []
        for _ in range(5):
            works, _ = select_label_work(cands, state, [_skill("genre")],
                                         prompt_version=_PV, limit=2)
            if not works:
                break
            for w in works:
                judged.append(w.entity_uid)
                state[(w.entity_type, w.entity_uid, "genre")] = _state(w.material_hash)
        self.assertEqual(sorted(judged), sorted(c["entity_uid"] for c in cands))
        self.assertEqual(len(judged), len(set(judged)))   # 같은 개체를 두 번 하지 않는다

    def test_미판정이_먼저다(self) -> None:
        # D 는 구성원이 많지만 재료만 바뀌었고, E 는 작지만 미판정이다 → E 먼저.
        state = {("인물", "D", "genre"): _state(_H2)}
        works, _ = select_label_work([_cand("D", 100), _cand("E", 3)], state,
                                     [_skill("genre")], prompt_version=_PV)
        self.assertEqual(_uids(works), ["E", "D"])

    def test_한_스킬이라도_미판정이면_미판정_무리(self) -> None:
        # F(구성원 1) = genre 바뀜 + topic 미판정 → 미판정 무리.
        # G(구성원 100) = genre·topic 모두 바뀜 → 바뀐 무리. 구성원이 적어도 F 가 앞선다.
        state = {("인물", "F", "genre"): _state(_H2),
                 ("인물", "G", "genre"): _state(_H2), ("인물", "G", "topic"): _state(_H2)}
        works, _ = select_label_work([_cand("G", 100), _cand("F", 1)], state,
                                     [_skill("genre"), _skill("topic")], prompt_version=_PV)
        self.assertEqual(_uids(works), ["F", "G"])
        self.assertEqual(works[0].reasons, (REASON_HASH_CHANGED, REASON_NEW))

    def test_같은_무리_안에서는_구성원_내림차순_그다음_키_오름차순(self) -> None:
        cands = [_cand("나", 5), _cand("가", 5), _cand("다", 9)]
        works, _ = select_label_work(cands, {}, [_skill("genre")], prompt_version=_PV)
        self.assertEqual(_uids(works), ["다", "가", "나"])

    def test_키가_같으면_타입_오름차순(self) -> None:
        cands = [_cand("김밥", 5, etype="작품"), _cand("김밥", 5, etype="음식")]
        works, _ = select_label_work(cands, {}, [_skill("genre")], prompt_version=_PV)
        self.assertEqual([w.entity_type for w in works], ["음식", "작품"])

    def test_입력_순서를_섞어도_결과가_같다(self) -> None:
        # 헌법 3조(결정성) — 후보·스킬 입력 순서가 달라도 같은 작업 목록·같은 집계.
        state = {("인물", "A", "genre"): _state(), ("인물", "B", "genre"): _state(_H2),
                 ("인물", "C", "topic"): _state(None), ("장소", "A", "genre"): _state(sv=0)}
        cands = [_cand("A", 4), _cand("B", 4), _cand("C", 9), _cand("A", 4, etype="장소"),
                 _cand("D", 1)]
        skills = [_skill("genre"), _skill("topic")]
        base = select_label_work(cands, state, skills, prompt_version=_PV, limit=3)
        for cperm in itertools.permutations(cands):
            for sperm in itertools.permutations(skills):
                with self.subTest(cands=[c["entity_uid"] for c in cperm]):
                    self.assertEqual(
                        select_label_work(list(cperm), state, list(sperm),
                                          prompt_version=_PV, limit=3),
                        base,
                    )

    def test_상한_none이면_전부(self) -> None:
        cands = [_cand(f"e{i}", i) for i in range(4)]
        works, stats = select_label_work(cands, {}, [_skill("genre")],
                                         prompt_version=_PV, limit=None)
        self.assertEqual(len(works), 4)
        self.assertEqual(stats["selected"], 4)

    def test_상한_0이하면_빈_목록(self) -> None:
        cands = [_cand(f"e{i}", i) for i in range(4)]
        for limit in (0, -1):
            with self.subTest(limit=limit):
                works, stats = select_label_work(cands, {}, [_skill("genre")],
                                                 prompt_version=_PV, limit=limit)
                self.assertEqual(works, [])
                self.assertEqual(stats["need"], 4)
                self.assertEqual(stats["selected"], 0)

    def test_상한이_후보보다_크면_전부(self) -> None:
        works, _ = select_label_work([_cand("A", 1)], {}, [_skill("genre")],
                                     prompt_version=_PV, limit=500)
        self.assertEqual(_uids(works), ["A"])


class TestSelectLabelWorkStats(unittest.TestCase):
    """집계 — 리포트·``--plan`` 이 읽는 칸."""

    def test_집계_칸이_모두_있다(self) -> None:
        _, stats = select_label_work([], {}, [_skill("genre")], prompt_version=_PV)
        self.assertEqual(
            set(stats),
            {"visible", "skipped_unchanged", "need", "selected", "pairs_new", "pairs_hash_null",
             "pairs_hash_changed", "pairs_skill_version", "pairs_prompt_version", "pairs_mixed"},
        )
        self.assertTrue(all(v == 0 for v in stats.values()))

    def test_짝_집계는_상한_전_밀린_일_전체다(self) -> None:
        # 상한으로 이번에 못 한 일도 짝 집계에 남는다 — 「얼마나 밀렸나」를 보이기 위해서다.
        state = {("인물", "B", "genre"): _state(None), ("인물", "C", "genre"): _state(_H2),
                 ("인물", "D", "genre"): _state()}
        cands = [_cand("A", 1), _cand("B", 2), _cand("C", 3), _cand("D", 4)]
        _, stats = select_label_work(cands, state, [_skill("genre"), _skill("topic")],
                                     prompt_version=_PV, limit=1)
        self.assertEqual(stats["visible"], 4)
        self.assertEqual(stats["need"], 4)        # D 도 topic 이 미판정
        self.assertEqual(stats["selected"], 1)
        self.assertEqual(stats["skipped_unchanged"], 0)
        self.assertEqual(stats["pairs_new"], 5)   # A×2 + B·C·D 의 topic
        self.assertEqual(stats["pairs_hash_null"], 1)
        self.assertEqual(stats["pairs_hash_changed"], 1)

    def test_집계_키는_사유_상수를_빠짐없이_덮는다(self) -> None:
        # ``_REASONS`` 는 집계 키 목록이다(우선순위 정본은 ``_pair_reason``). 사유 상수를 새로 만들고
        #  여기 빠뜨리면 선별이 ``pairs_<사유>`` 칸을 못 찾아 KeyError 로 배치가 멈춘다.
        from src.mm_meta import entity_label as mod

        constants = {v for k, v in vars(mod).items() if k.startswith("REASON_")}
        self.assertEqual(set(mod._REASONS), constants)
        self.assertEqual(len(mod._REASONS), len(set(mod._REASONS)))

    def test_스킬이_없으면_모두_건너뜀(self) -> None:
        works, stats = select_label_work([_cand("A", 1)], {}, [], prompt_version=_PV)
        self.assertEqual(works, [])
        self.assertEqual(stats["skipped_unchanged"], 1)


# ── ③ 상태 읽기 ──────────────────────────────────────────────────────────────


class _Cursor:
    """execute 를 기록하고 정해 둔 결과를 돌려주는 가짜 커서."""

    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self._sql = ""

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.executed.append((sql, params))
        self._sql = sql

    def fetchone(self) -> Any:
        if "FROM node" in self._sql:
            return (1,) if self.conn.entity_exists else None
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.conn.rows)


class _Conn:
    """커서·트랜잭션만 흉내 내는 가짜 커넥션(실 DB 없이 SQL 호출 형태를 본다)."""

    def __init__(self, *, rows: list[tuple[Any, ...]] | None = None,
                 entity_exists: bool = True) -> None:
        self.rows = rows or []
        self.entity_exists = entity_exists
        self.executed: list[tuple[str, Any]] = []
        self.transactions = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):  # noqa: ANN201 - 테스트 더미
        self.transactions += 1
        yield


def _state_row(uid: str, skill: str, *, hash_kinds: int = 1, has_null: bool = False,
               h: str | None = _H1, sv_kinds: int = 1, sv: int = 1, pv_kinds: int = 1,
               pv: str = _PV) -> tuple[Any, ...]:
    """``fetch_label_state`` 의 집계 SQL 한 행(열 순서는 SQL SELECT 목록과 같다).

    Args:
        uid: 개체 표기 키(타입은 ``인물`` 고정).
        skill: 스킬 코드.
        hash_kinds: 같은 짝 안 지문 종류 수(NULL 도 한 종류로 센다).
        has_null: 지문 NULL 행이 하나라도 있는가.
        h: 지문 최댓값(NULL 아닌 것 중).
        sv_kinds/pv_kinds: 스킬 판·문안 판 종류 수.
        sv/pv: 스킬 판·문안 판 최댓값.

    Returns:
        행 튜플.
    """
    return ("인물", uid, skill, hash_kinds, has_null, h, sv_kinds, sv, pv_kinds, pv)


class TestFetchLabelState(unittest.TestCase):
    """(개체, 스킬)별 집계 — 대표값과 혼합 감지."""

    def test_한_번_읽고_묶어_집계한다(self) -> None:
        conn = _Conn(rows=[_state_row("아이유", "genre")])
        got = fetch_label_state(conn)
        self.assertEqual(len(conn.executed), 1)
        sql = " ".join(conn.executed[0][0].split()).lower()
        self.assertIn("from entity_mm_skill_label", sql)
        self.assertIn("group by", sql)
        # 조회 전용 — 상태 읽기가 쓰기를 하면 안 된다.
        for verb in ("insert", "update", "delete"):
            self.assertNotIn(verb, sql)
        self.assertEqual(got, {("인물", "아이유", "genre"): _state(_H1)})

    def test_행이_없으면_빈_dict(self) -> None:
        self.assertEqual(fetch_label_state(_Conn(rows=[])), {})

    def test_지문이_모두_null이면_none_혼합_아님(self) -> None:
        # 옛 판정(지문 칸 이전) — 선별에서 hash_null 사유가 나와야 한다.
        conn = _Conn(rows=[_state_row("아이유", "genre", has_null=True, h=None)])
        st = fetch_label_state(conn)[("인물", "아이유", "genre")]
        self.assertIsNone(st.material_hash)
        self.assertFalse(st.mixed)

    def test_null과_값이_섞이면_none이고_혼합(self) -> None:
        # max() 는 NULL 을 건너뛰어 값을 돌려주지만, NULL 이 하나라도 있으면 지문을 믿지 않는다.
        conn = _Conn(rows=[_state_row("아이유", "genre", hash_kinds=2, has_null=True, h=_H1)])
        st = fetch_label_state(conn)[("인물", "아이유", "genre")]
        self.assertIsNone(st.material_hash)
        self.assertTrue(st.mixed)

    def test_지문이_둘이면_혼합(self) -> None:
        conn = _Conn(rows=[_state_row("아이유", "genre", hash_kinds=2, h=_H2)])
        self.assertTrue(fetch_label_state(conn)[("인물", "아이유", "genre")].mixed)

    def test_스킬_판이_둘이면_혼합(self) -> None:
        conn = _Conn(rows=[_state_row("아이유", "genre", sv_kinds=2, sv=3)])
        st = fetch_label_state(conn)[("인물", "아이유", "genre")]
        self.assertTrue(st.mixed)
        self.assertEqual(st.skill_version, 3)

    def test_문안_판이_둘이면_혼합(self) -> None:
        conn = _Conn(rows=[_state_row("아이유", "genre", pv_kinds=2)])
        self.assertTrue(fetch_label_state(conn)[("인물", "아이유", "genre")].mixed)

    def test_여러_짝을_키별로_돌려준다(self) -> None:
        conn = _Conn(rows=[_state_row("아이유", "genre"), _state_row("아이유", "topic", sv=2),
                           _state_row("김치", "genre", h=_H2)])
        got = fetch_label_state(conn)
        self.assertEqual(set(got), {("인물", "아이유", "genre"), ("인물", "아이유", "topic"),
                                    ("인물", "김치", "genre")})
        self.assertEqual(got[("인물", "아이유", "topic")].skill_version, 2)
        self.assertEqual(got[("인물", "김치", "genre")].material_hash, _H2)

    def test_읽은_상태로_선별하면_사유가_이어진다(self) -> None:
        # 상태 읽기 → 선별의 연결: NULL 행은 hash_null, 같은 지문은 건너뜀.
        conn = _Conn(rows=[_state_row("A", "genre", has_null=True, h=None),
                           _state_row("B", "genre", h=_H1)])
        works, stats = select_label_work([_cand("A", 1), _cand("B", 1)], fetch_label_state(conn),
                                         [_skill("genre")], prompt_version=_PV)
        self.assertEqual(_uids(works), ["A"])
        self.assertEqual(works[0].reasons, (REASON_HASH_NULL,))
        self.assertEqual(stats["skipped_unchanged"], 1)


# ── ④ 저장 ──────────────────────────────────────────────────────────────────


def _real_skill() -> Any:
    """검증을 통과한 2라벨 multi 스킬(값은 전부 더미).

    Returns:
        ``ClassificationSkill``.
    """
    return load_skill({
        "skill": "샘플 갈래",
        "skill_code": "genre",
        "version": 3,
        "policy": {"selection": "multi", "unassigned": "해당없음", "max_labels": 30},
        "labels": [
            {"code": "music", "name": "음악", "definition": "음악이 중심인 대상",
             "not": "음식"},
            {"code": "food", "name": "음식", "definition": "음식이 중심인 대상",
             "not": "음악"},
        ],
    })


def _insert_columns(sql: str) -> list[str]:
    """INSERT 문의 칸 목록을 읽는다(칸 ↔ 값 자리 정렬을 검사하려고).

    Args:
        sql: INSERT 문.

    Returns:
        칸 이름 목록(순서 그대로).
    """
    m = re.search(r"\(([^)]*)\)\s*VALUES", sql, re.IGNORECASE)
    assert m is not None, sql
    return [c.strip() for c in m.group(1).split(",")]


class TestReplaceEntityLabelsHash(unittest.TestCase):
    """저장 — 판정 1회의 모든 라벨 행에 같은 지문."""

    def _inserts(self, conn: _Conn) -> list[tuple[str, Any]]:
        """기록된 실행 중 INSERT 만 고른다.

        Args:
            conn: 가짜 커넥션.

        Returns:
            ``(sql, params)`` 목록.
        """
        return [(s, p) for s, p in conn.executed if s.strip().upper().startswith("INSERT")]

    def _call(self, conn: _Conn, judgement: Any, **kw: Any) -> int:
        """기본 인자로 저장을 부른다.

        Args:
            conn: 가짜 커넥션.
            judgement: 판정 결과.
            **kw: 덧붙일 인자(``material_hash`` 등).

        Returns:
            기록 행 수.
        """
        return replace_entity_labels(
            conn, entity_type="인물", entity_uid="아이유", skill=_real_skill(),
            skill_version=3, judgement=judgement, prompt_version=_PV, **kw,
        )

    def test_모든_라벨_행에_같은_지문을_쓴다(self) -> None:
        conn = _Conn()
        n = self._call(conn, SkillJudgement(ok=True, label_names=("음악", "음식"),
                                            label_codes=("music", "food")),
                       material_hash=_H1)
        self.assertEqual(n, 2)
        inserts = self._inserts(conn)
        self.assertEqual(len(inserts), 2)
        for sql, params in inserts:
            cols = _insert_columns(sql)
            self.assertIn("material_hash", cols)
            # 칸 수 = 자리표시자 수 = 값 수 — 하나라도 어긋나면 값이 엉뚱한 칸에 들어간다.
            self.assertEqual(sql.count("%s"), len(cols))
            self.assertEqual(len(params), len(cols))
            self.assertEqual(params[cols.index("material_hash")], _H1)
        self.assertEqual([p[_insert_columns(s).index("label_code")] for s, p in inserts],
                         ["music", "food"])

    def test_지문_미지정은_null이다_옛_호출_호환(self) -> None:
        # 지문을 모르는 옛 호출부도 그대로 돈다 — NULL 은 다음 배치에서 재선별될 뿐이다.
        conn = _Conn()
        self._call(conn, SkillJudgement(ok=True, label_names=("음악",), label_codes=("music",)))
        (sql, params), = self._inserts(conn)
        self.assertIsNone(params[_insert_columns(sql).index("material_hash")])

    def test_지문_형식이_틀리면_쓰기_전에_거부한다(self) -> None:
        # 🔴 W2 — 지문은 ``label_material_hash`` 가 내는 소문자 16진 64자뿐이다. 대문자·짧은 값이
        #    들어가면 다음 배치의 비교가 영영 「다름」이라 매번 다시 판정하고(헛일), CHAR(64) 는 짧은
        #    값을 공백으로 채워 원인을 찾기 어렵게 만든다. 그래서 SQL 을 하나도 치기 전에 막는다.
        bad_values: tuple[Any, ...] = (
            "A" * 64,            # 대문자
            "a" * 63,            # 짧다
            "a" * 65,            # 길다
            "g" * 64,            # 16진 밖 글자
            "",                  # 빈 문자열
            "a" * 64 + "\n",     # 끝 줄바꿈 — `$` 는 이것을 통과시킨다(fullmatch 로 막아야 한다)
            " " + "a" * 63,      # 앞 공백
            123,                 # 문자열이 아님
        )
        for bad in bad_values:
            with self.subTest(bad=bad):
                conn = _Conn()
                with self.assertRaises(EntityLabelError):
                    self._call(conn, SkillJudgement(ok=True, label_names=("음악",),
                                                    label_codes=("music",)),
                               material_hash=bad)
                self.assertEqual(conn.executed, [])
                self.assertEqual(conn.transactions, 0)

    def test_실제_지문은_통과한다(self) -> None:
        # 형식 검사가 정상 지문까지 막으면 안 된다 — 지문 함수가 낸 값 그대로 저장된다.
        real = label_material_hash("아이유(인물). 가수")
        conn = _Conn()
        n = self._call(conn, SkillJudgement(ok=True, label_names=("음악",), label_codes=("music",)),
                       material_hash=real)
        self.assertEqual(n, 1)
        (sql, params), = self._inserts(conn)
        self.assertEqual(params[_insert_columns(sql).index("material_hash")], real)

    def test_지문_none_명시도_null이다(self) -> None:
        conn = _Conn()
        self._call(conn, SkillJudgement(ok=True, label_names=("음악",), label_codes=("music",)),
                   material_hash=None)
        (sql, params), = self._inserts(conn)
        self.assertIsNone(params[_insert_columns(sql).index("material_hash")])

    def test_해당없음_단독행에도_지문을_쓴다(self) -> None:
        # 「해당없음」도 판정 이력이다 — 지문이 없으면 다음 배치가 또 판정한다.
        conn = _Conn()
        self._call(conn, SkillJudgement(ok=True, label_names=("해당없음",),
                                        label_codes=(UNASSIGNED_LABEL_CODE,)),
                   material_hash=_H2)
        (sql, params), = self._inserts(conn)
        cols = _insert_columns(sql)
        self.assertEqual(params[cols.index("label_code")], UNASSIGNED_LABEL_CODE)
        self.assertEqual(params[cols.index("material_hash")], _H2)

    def test_판정_실패면_아무것도_쓰지_않는다(self) -> None:
        # 기존 계약 유지 — 실패를 「판정 완료」로 굳히면 영영 재판정되지 않는다.
        conn = _Conn()
        n = self._call(conn, SkillJudgement(ok=False, failure=JudgeFailure.LABELS_EMPTY),
                       material_hash=_H1)
        self.assertEqual(n, 0)
        self.assertEqual(conn.executed, [])

    def test_개체가_없으면_쓰기_전에_거부한다(self) -> None:
        # 기존 계약 유지 — FK 를 걸 수 없어 앱이 막는다.
        conn = _Conn(entity_exists=False)
        with self.assertRaises(EntityLabelError):
            self._call(conn, SkillJudgement(ok=True, label_names=("음악",),
                                            label_codes=("music",)),
                       material_hash=_H1)
        self.assertEqual(self._inserts(conn), [])
        self.assertFalse(any(s.strip().upper().startswith("DELETE") for s, _ in conn.executed))

    def test_지우고_넣기는_한_트랜잭션이다(self) -> None:
        conn = _Conn()
        self._call(conn, SkillJudgement(ok=True, label_names=("음악",), label_codes=("music",)),
                   material_hash=_H1)
        self.assertEqual(conn.transactions, 1)
        verbs = [s.strip().split()[0].upper() for s, _ in conn.executed]
        self.assertEqual(verbs, ["SELECT", "DELETE", "INSERT"])


if __name__ == "__main__":
    unittest.main()
