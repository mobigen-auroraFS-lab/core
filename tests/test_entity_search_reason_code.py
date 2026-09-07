"""095 FR-3 — ``match_entity_reason``(이유 코드) 과 ``match_entity``(종전 문구) 의 일치.

봉인: ① 코드는 이름 > 근거 키워드 > 설명 우선이고 걸린 토큰은 질의 순서상 첫 것 ② 종전 함수의 문구는
코드에서 조립되며 **한 글자도 달라지지 않았다**(무작위 입력 전수) ③ 이름 매칭은 토큰을 싣지 않는다.
"""

from __future__ import annotations

import random
import unittest

from src.mm_meta import entity_search as es


class TestReasonCode(unittest.TestCase):
    def test_priority_name_over_keyword_over_description(self) -> None:
        item = {"name": "제주도", "keywords": ["해녀", "감귤"], "description": "섬이다"}
        self.assertEqual(es.match_entity_reason(item, ("제주",)), (1, es.REASON_CODE_NAME, None))
        self.assertEqual(es.match_entity_reason(item, ("해녀",)), (1, es.REASON_CODE_KEYWORD, "해녀"))
        self.assertEqual(es.match_entity_reason(item, ("섬",)), (1, es.REASON_CODE_DESCRIPTION, "섬"))
        # 두 축에 걸리면 높은 축 하나 · 토큰 수는 맞은 토큰 전부
        self.assertEqual(es.match_entity_reason(item, ("섬", "해녀")), (2, es.REASON_CODE_KEYWORD, "해녀"))
        self.assertEqual(es.match_entity_reason(item, ("없다",)), (0, None, None))
        self.assertEqual(es.match_entity_reason(item, ()), (0, None, None))

    def test_first_token_in_query_order_is_reported(self) -> None:
        item = {"name": "x", "keywords": ["감귤", "해녀"], "description": ""}
        self.assertEqual(es.match_entity_reason(item, ("해녀", "감귤"))[2], "해녀")
        self.assertEqual(es.match_entity_reason(item, ("감귤", "해녀"))[2], "감귤")

    def test_legacy_wording_is_assembled_from_code_identically(self) -> None:
        rng = random.Random(95)
        vocab = ["제주", "제주도", "해녀", "감귤", "섬", "바다", "x", "", "서울"]
        for _ in range(500):
            item = {
                "name": rng.choice(vocab), "description": " ".join(rng.sample(vocab, 2)),
                "keywords": [rng.choice(vocab) for _ in range(rng.randint(0, 3))],
            }
            tokens = tuple(rng.choice(vocab) for _ in range(rng.randint(0, 3)))
            hit, code, token = es.match_entity_reason(item, tokens)
            hit2, wording = es.match_entity(item, tokens)
            self.assertEqual(hit, hit2)
            if code == es.REASON_CODE_KEYWORD:
                self.assertEqual(wording, f"{es.REASON_KEYWORD}: {token}")
            elif code == es.REASON_CODE_DESCRIPTION:
                self.assertEqual(wording, f"{es.REASON_DESCRIPTION}: {token}")
            else:
                self.assertIsNone(wording)


if __name__ == "__main__":
    unittest.main()
