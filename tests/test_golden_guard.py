"""025 G3 / 099 T031 — 코퍼스-골든 정합 가드(순수) 단위.

"코퍼스에 자산이 추가되면 골든 질의도 추가되어야 한다"(운영 규칙)를 순수 함수로 강제한다.
가드는 두 세대가 있다:

- **현행** `uncovered_assets(registered_ids, golden_ids)` — 자산 id 집합 뺄셈. "적재됐는데 어느
  골든 질의의 정답도 아닌 자산"을 센다. 골든이 자산 id 를 직접 들고 있으니 파싱이 없다.
- **레거시** `topic_of_filename` / `uncovered_topics` — 파일명에서 주제를 읽어 토픽 단위로 비교.
  현 코퍼스에서 96.8% 퇴화해(099 T031 실측) 가드로는 죽었지만, 옛 골든 전용 도구와 골든 빌더의
  `topics` 태그가 아직 쓰므로 함수는 남겨 두고 테스트도 그대로 지킨다(회귀 방지).
"""

from __future__ import annotations

import unittest

from src.search.golden_guard import topic_of_filename, uncovered_assets, uncovered_topics


class TopicOfFilenameTest(unittest.TestCase):
    def test_first_token_of_slug(self) -> None:
        # 구형 <주제>_<11자ID>_<제목> → 첫 토큰이 주제.
        self.assertEqual(topic_of_filename("등산_입문_TUWlGnSstVI_제목.mp4"), "등산")
        self.assertEqual(topic_of_filename("무선_충전기_x.jpg"), "무선")

    def test_source_prefix_uses_second_token(self) -> None:
        # 신규 youtube_/wikipedia_ 출처-prefix → 2번째 토큰이 진짜 주제(prefix 는 주제 아님).
        self.assertEqual(topic_of_filename("youtube_사막_3bTA2c2n2QI.jpg"), "사막")
        self.assertEqual(topic_of_filename("wikipedia_고려청자_1031019.txt"), "고려청자")
        # 주제에 공백이 있어도 한 필드(밑줄로만 분리).
        self.assertEqual(topic_of_filename("youtube_기후 변화_3CHPt7zk5fE.mp4"), "기후 변화")

    def test_trailing_paren_topic(self) -> None:
        # 재수집 명명 <uuid>__<제목>_(주제).ext → 끝 괄호가 주제(토큰 위치 불안정 → 표식 우선).
        self.assertEqual(
            topic_of_filename("018f0000-0000-7000-8000-000000000273__Yoke_and_Arrows_(전통주).svg"),
            "전통주",
        )
        self.assertEqual(
            topic_of_filename("018f0000-0000-7000-8000-000000000276__서귀포 열대우림 🌴_(열대우림).mp4"),
            "열대우림",
        )
        # 괄호 없는 신규 youtube(+uuid 접두)는 접두 제거 후 2번째 토큰.
        self.assertEqual(
            topic_of_filename("018f0000-0000-7000-8000-000000000274__youtube_빙하_6uWBi3GrRYM.mp3"),
            "빙하",
        )

    def test_no_underscore_returns_stem(self) -> None:
        self.assertEqual(topic_of_filename("manifest.json"), "manifest")

    def test_empty_safe(self) -> None:
        self.assertEqual(topic_of_filename(""), "")


class UncoveredTopicsTest(unittest.TestCase):
    def test_full_coverage_returns_empty(self) -> None:
        self.assertEqual(uncovered_topics({"등산", "주식"}, {"등산", "주식", "수영"}), [])

    def test_new_topic_detected(self) -> None:
        # 코퍼스에 '겨울낚시' 토픽 자산이 새로 들어왔는데 골든 질의가 없다 → 검출(정렬·결정적).
        self.assertEqual(
            uncovered_topics({"등산", "겨울낚시", "주식"}, {"등산", "주식"}), ["겨울낚시"]
        )

    def test_deterministic_sorted(self) -> None:
        self.assertEqual(uncovered_topics({"c", "a", "b"}, set()), ["a", "b", "c"])


class UncoveredAssetsTest(unittest.TestCase):
    """099 T031 — 파일명 파싱을 버리고 **자산 id 집합 뺄셈**으로 커버리지를 본다.

    비유: 학급 명부(적재된 자산)와 시험 답안지에 이름이 적힌 학생(골든 정답)을 맞대 보고,
    답안지에 한 번도 안 나온 학생을 뽑아내는 일이다. 이름 규칙을 해석할 필요가 없다.

    id 는 **합성값**이다 — 실 자산 id 는 공개 레포에 두지 않는다(문서 정책).
    """

    A1 = "018f0000-0000-7000-8000-000000000001"
    A2 = "018f0000-0000-7000-8000-000000000002"
    A3 = "018f0000-0000-7000-8000-000000000003"

    def test_full_coverage_returns_empty(self) -> None:
        # 적재 자산이 모두 어떤 골든 질의의 정답이면 미커버 없음.
        self.assertEqual(uncovered_assets({self.A1, self.A2}, {self.A1, self.A2, self.A3}), [])

    def test_uncovered_asset_detected(self) -> None:
        # A3 가 새로 적재됐는데 어느 골든 질의의 정답도 아니다 → 검출.
        self.assertEqual(
            uncovered_assets({self.A1, self.A2, self.A3}, {self.A1, self.A2}), [self.A3]
        )

    def test_golden_only_ids_ignored(self) -> None:
        # 방향은 한쪽이다 — 골든에만 있고 적재되지 않은 id(삭제된 자산 등)는 이 가드의 관심사가 아니다.
        self.assertEqual(uncovered_assets({self.A1}, {self.A1, self.A2, self.A3}), [])

    def test_empty_inputs_safe(self) -> None:
        # 빈 코퍼스·빈 골든이어도 예외 없이 빈 목록(부트스트랩 시점 안전).
        self.assertEqual(uncovered_assets(set(), set()), [])
        self.assertEqual(uncovered_assets(set(), {self.A1}), [])

    def test_all_uncovered_when_golden_empty(self) -> None:
        # 골든이 비면 적재 자산 전량이 미커버.
        self.assertEqual(
            uncovered_assets({self.A2, self.A1}, set()), [self.A1, self.A2]
        )

    def test_deterministic_sorted(self) -> None:
        # 집합은 순서가 없다 — 정렬해 돌려줘야 실행마다 같은 보고가 나온다(헌법 3조 결정성).
        self.assertEqual(uncovered_assets({"c", "a", "b"}, set()), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
