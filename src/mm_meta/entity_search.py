"""089 — 멀티모달 메타 **개체 검색**(질의 토큰 분리) 순수 함수. DB·서버·색인 불필요.

무엇을 하나: 개체(묶음) 목록을 검색어로 좁히고 줄을 세운다. 재료는 이미 저장된 세 가지뿐이다 —
**이름 · 근거 키워드 · 설명문**. 새 색인도 임베딩도 만들지 않는다.

**왜 토큰으로 쪼개나**(착수 전 실측 2026-08-31 · 노출 개체 82개 · `fixtures/mm_meta_search/`):
현행은 검색어를 정규화한 뒤 **통짜 부분 문자열**로 찾았다. 우편물에 적힌 주소 전체가 글자 그대로
들어 있어야만 배달하는 셈이라, `"여자 솔로 가수"` 같은 여러 낱말 질의는 그 글자가 **연속으로**
있을 리 없어 구조적으로 전멸했다(다어절 질의 30개 중 29개가 0건 · 재현율 0%). 반면 낱말 하나로
내리면 35%가 걸렸다(`펭귄`→남극 · `해녀`→제주도). 그래서 **질의를 낱말로 쪼개** 낱말 하나만
맞아도 남기고(OR), **몇 개나 맞았는지로 줄을 세운다**(예측 재현율 43.3% · spec 089 §2).

**왜 AND 가 아닌가**: 개체 설명문이 평균 42자로 짧아 세 낱말을 다 가진 개체가 없다 — AND 로 하면
현행의 0%가 그대로 재현된다. 넓게 걸고 순위로 가르는 쪽을 택했다. 넓힌 대가는 작았다(평균 결과
2.9건 · 정답 중위 1위).

**왜 형태소 분석(nori)을 안 쓰나**: 072 의 형태소 정규화는 OpenSearch analyzer 를 호출한다. 개체
검색은 지금 DB·파이썬만으로 도는데 검색 엔진 의존을 들이면 "싸게 고친다"는 1단계의 전제가 무너진다.
공백 분리로 얼마나 오르는지 먼저 재고, 부족하면 그때 검토한다(plan 089 §설계 결정).

🔴 **이 접근의 천장은 매칭이 아니라 텍스트 빈약이다.** 검색 대상이 개체당 50~60자뿐이라
「김치」에 `발효` 가, 「고려청자」에 `도자기` 가 아예 없다 — 토큰을 아무리 잘 쪼개도 걸릴 글자가
없는 실패가 절반을 넘는다(17/30). 그쪽은 2단계(개체 의미 검색) 또는 설명문 품질 개선(084) 소관이다.

**왜 코어에 있나**: 지금 이 로직을 부르는 곳은 백엔드 데모 라우트인데 그 화면은 폐기 예정이다.
라우트에 두면 로직이 화면과 함께 사라진다. 표기 정규화 정본(``src.domain.text_norm``)도 코어에
있어, 규칙이 두 벌이 되지 않으려면 이 자리가 맞다(라우트 자신의 docstring 이 적어 둔 그대로다).

설계 배경: `specs/089-mm-meta-search-hybrid`(spec §2 매칭 · plan §설계 결정 · tasks T001~T004)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.config.search_constants import ENTITY_SEMANTIC_GATE_EPS_DEFAULT
from src.domain.text_norm import normalize_text_key
from src.search.fusion import gate_signal, passes_cutoff

# 걸린 이유 문구. 화면이 그대로 찍으므로 문자열이 계약이다(현행 라우트 문구를 그대로 옮겼고,
# 뒤에 ``: 맞은토큰`` 만 덧붙는다 — 필드명·의미는 그대로 두고 내용만 풍부해진다).
REASON_KEYWORD = "근거 키워드 일치"
REASON_DESCRIPTION = "설명 일치"


def split_query(q: str | None) -> tuple[str, ...]:
    """검색어를 공백으로 쪼개 **정규화된 토큰**으로 만든다(빈 토큰은 버린다).

    🔴 정규화는 코어 정본 ``normalize_text_key`` **하나만** 쓴다(083 태그 패싯·084 멀티모달 메타
    공용). 여기서 규칙을 새로 정의하면 표기 해석이 두 벌이 되어, 같은 글자가 색인에서는 걸리고
    필터에서는 안 걸리는 일이 생긴다. 그 규칙은 NFKC → 공백 제거 → casefold 이므로
    ``"FIFA 월드컵"`` 은 ``("fifa", "월드컵")`` 이 된다.

    🔴 **최소 길이 필터를 두지 않는다**(tasks 089 T004 ⑦ · 2026-08-31 실측으로 결정). 한 글자
    토큰(`강`·`큰`)은 여러 개체에 걸려 정답을 밀어낼 수 있어 버리는 안을 검토했고, 같은 질의셋
    80개로 **켠 값·끈 값을 둘 다 쟀다**:

        ┌──────────────────────┬──────────────┬──────────────────┐
        │                      │ 필터 끔(채택) │ 필터 켬(2글자~)  │
        ├──────────────────────┼──────────────┼──────────────────┤
        │ 다어절 재현율        │ 43.3%(13/30) │ 36.7%(11/30)     │
        │ 이름 재현율          │ 100%         │ 100%             │
        │ 평균 결과 건수       │ 2.9건        │ 2.5건            │
        │ 정답 상위 5 안       │ 92.3%        │ 100%             │
        └──────────────────────┴──────────────┴──────────────────┘

    필터를 켜면 **찾던 것을 두 개 잃는다** — 「한강」(`수도를 가로지르는 강`)과 「나일강」
    (`아프리카 큰 강`)은 한 글자 토큰 `강` 으로만 걸리던 것이라 통째로 0건이 된다. 대신 오른 것처럼
    보이는 "상위 5 안 92.3%→100%"는 **못 찾은 질의가 분모에서 빠져서**지 순위가 나아진 것이 아니다.
    한 글자 토큰 자체가 드물기도 하다(기준선 80질의의 토큰 133개 중 6개 · 4.5%).

    🔴 결정타는 따로 있다 — 필터를 켜면 `강`·`산` 같은 **한 글자 단독 질의가 토큰 0개**가 되고,
    아래 ``narrow_entities`` 의 계약("토큰 0개 = 전체")에 따라 **개체 전량**이 쏟아진다. 지금은
    `강` 이 6건(한강 포함)을 준다. 한 글자를 버리는 이득보다 이 사고가 크고, "한 토큰 질의는 현행과
    같아야 한다"는 회귀 기준(B2)도 깨진다. 그래서 **필터 없음**으로 확정한다.

    Args:
        q: 검색어 원문. ``None``·빈 문자열·공백뿐이면 토큰이 없다(호출부가 "전체"로 해석한다).

    Returns:
        정규화된 토큰들(질의에 나온 순서 유지 · 중복도 그대로). 토큰이 없으면 빈 튜플.
    """
    if not q:
        return ()
    # split() 은 연속 공백·탭·개행을 한꺼번에 처리한다. 각 토큰을 정규화한 뒤, 정규화 결과가
    # 빈 문자열이 된 것(전각 공백만 든 조각 등)은 버린다 — 빈 토큰은 어디에나 "포함"되므로
    # 남겨 두면 모든 개체가 걸린다.
    tokens = (normalize_text_key(part) for part in q.split())
    return tuple(t for t in tokens if t)


def match_entity(item: Mapping[str, Any], tokens: Sequence[str]) -> tuple[int, str | None]:
    """개체 하나가 토큰을 **몇 개 맞췄는지**와 **걸린 이유**를 돌려준다.

    찾는 곳은 셋이다 — 이름 · 근거 키워드(묶인 이유가 된 원문 낱말들) · 설명문. 토큰 하나가
    셋 중 **어디든** 있으면 그 토큰은 맞은 것으로 1점이며, 같은 토큰이 두 곳에 있어도 1점이다
    (점수는 "질의의 몇 낱말을 아는 개체인가"를 뜻해야 한다 — 같은 낱말을 여러 칸에 적어 둔 개체가
    이길 이유가 없다).

    이유의 우선순위는 **이름 > 근거 키워드 > 설명문**으로 현행 라우트와 같다. 이름으로 걸렸으면
    ``None`` 인데, 이름은 화면 카드에 이미 크게 보이므로 "왜 나왔는지"를 따로 적을 필요가 없기
    때문이다(현행 계약 유지). 같은 축에 여러 토큰이 걸리면 **질의에 먼저 나온** 토큰을 싣는다
    (같은 입력에 같은 문구가 나와야 한다 · 헌법 3조 결정성).

    Args:
        item: 목록 행. ``name``·``keywords``(리스트)·``description`` 을 읽으며, 없거나 ``None``
            이면 빈 값으로 본다(집계 결과라 설명문이 아직 없는 개체가 있다).
        tokens: ``split_query`` 가 만든 정규화 토큰들. 빈 시퀀스면 0점이다.

    Returns:
        ``(맞은 토큰 수, 걸린 이유)``. 이유는 ``"근거 키워드 일치: 가수"`` 꼴이며, 이름으로
        걸렸거나 아무것도 못 맞췄으면 ``None``.
    """
    name = normalize_text_key(str(item.get("name") or ""))
    keywords = [normalize_text_key(str(k)) for k in (item.get("keywords") or [])]
    description = normalize_text_key(str(item.get("description") or ""))

    hit = 0
    name_hit = False
    keyword_token: str | None = None
    description_token: str | None = None
    for token in tokens:
        if not token:
            # 방어: 호출부가 정규화를 건너뛰고 빈 토큰을 넘기면 모든 개체가 걸린다.
            continue
        in_name = token in name
        in_keyword = any(token in kw for kw in keywords)
        in_description = token in description
        if not (in_name or in_keyword or in_description):
            continue
        hit += 1
        # 이유는 가장 높은 축 하나만 남긴다. 각 축의 **첫 번째** 토큰을 기억한다.
        if in_name:
            name_hit = True
        elif in_keyword:
            if keyword_token is None:
                keyword_token = token
        elif description_token is None:
            description_token = token

    if name_hit:
        return hit, None
    if keyword_token is not None:
        return hit, f"{REASON_KEYWORD}: {keyword_token}"
    if description_token is not None:
        return hit, f"{REASON_DESCRIPTION}: {description_token}"
    return 0, None  # 여기 오는 경우는 맞은 토큰이 하나도 없을 때뿐이다.


def narrow_entities(
    items: Sequence[Mapping[str, Any]],
    q: str | None,
) -> list[dict[str, Any]]:
    """개체 목록을 검색어로 좁히고 **맞은 토큰 수 → 묶음 크기 → 이름** 순으로 줄 세운다.

    남기는 조건은 **맞은 토큰 ≥ 1**(OR)이다. 넓게 걸어 놓고 순위로 가르는 설계이며, 그 대가
    (결과가 늘어나는 것)는 정렬 1순위가 방어한다 — 세 낱말 중 셋을 아는 개체가 하나만 아는 개체보다
    반드시 위에 온다.

    **정렬 tiebreak 이 두 단계인 이유**: 맞은 토큰 수만으로는 동점이 흔하다(대부분 1점). 그다음은
    묶음 크기(``confirmed_count``)로 — 자료가 많은 묶음이 사용자가 찾던 것일 확률이 높다. 그것도
    같으면 이름 오름차순으로 **완전히 고정**한다. 여기를 비워 두면 같은 질의가 실행할 때마다 다른
    순서를 낼 수 있어 재측정 자체가 성립하지 않는다(헌법 3조 결정성 · spec 089 B4).

    **검색어가 없을 때는 전체**를 준다(현행 계약). 이때도 각 행에 ``match_reason: None`` 을 붙여
    응답 모양을 한 가지로 유지한다 — 화면이 이 필드의 유무로 갈라지지 않게 하기 위해서다.
    ⚠️ "전체"가 되는 것은 **토큰이 0개일 때뿐**이다. 기호만 든 질의(``"!!!"``)는 정규화가 기호를
    지우지 않으므로 어엿한 토큰이 되고, 걸리는 개체가 없으면 **0건**이다(전체가 아니다 · spec §5
    "결과 0건과 전체를 가른다"). 공백만 든 질의는 토큰이 0개라 전체이며 이는 현행과 같다.

    순수 함수다 — 입력 행을 고치지 않고 얕은 사본에 ``match_reason`` 을 얹어 돌려준다.

    Args:
        items: 목록 행들(``/mm-meta`` 응답 모양). 검색어가 없으면 **이 순서 그대로** 나간다
            (호출부가 이미 정한 순서 — 지금은 묶음 크기 내림차순 — 를 뒤집지 않는다).
        q: 검색어. ``None``·빈 문자열·공백뿐이면 좁히지 않는다.

    Returns:
        좁혀진 목록(위 순서). 각 행은 입력 행의 사본 + ``match_reason``.
    """
    tokens = split_query(q)
    if not tokens:
        return [{**item, "match_reason": None} for item in items]

    # (정렬 키 3종, 행) 으로 모아 한 번에 정렬한다. key= 를 쓰므로 dict 끼리 비교될 일은 없다.
    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for item in items:
        hit, reason = match_entity(item, tokens)
        if hit < 1:
            continue
        scored.append(
            (
                hit,
                int(item.get("confirmed_count") or 0),
                str(item.get("name") or ""),
                {**item, "match_reason": reason},
            )
        )
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[3] for row in scored]

# 의미 검색으로 걸린 행에 붙일 이유 문구. 문자열 매칭 이유(위 두 상수)와 **구별되게** 둔다 —
# 화면에서 "글자가 맞은 것"과 "뜻이 가까운 것"을 사용자가 갈라 볼 수 있어야 신뢰가 생긴다.
REASON_SEMANTIC = "뜻이 가까움"


def fuse_entity_results(
    items: Sequence[Mapping[str, Any]],
    string_hits: Sequence[Mapping[str, Any]],
    semantic_hits: Sequence[Mapping[str, Any]] = (),
    *,
    max_semantic: int | None = None,
) -> list[dict[str, Any]]:
    """문자열 결과 **위에** 의미 결과를 얹는다(순수 · DB 호출 없음 · spec 090).

    Args:
        items: 목록 행 전체(``/mm-meta`` 응답 모양). 의미 검색은 키만 돌려주므로 화면에 보여줄
            행을 여기서 되살린다.
        string_hits: ``narrow_entities`` 결과. **순서를 그대로 유지**한다.
        semantic_hits: ``find_similar_entities`` 결과(``{entity_type, entity_uid, similarity}``).
            유사도 내림차순으로 이미 정렬돼 있다고 본다. 비우면 문자열 결과가 그대로 나간다.
        max_semantic: 의미 결과를 몇 개까지 더할지. ``None`` 이면 받은 것 전부(상한은 조회
            시점에 두는 것이 기본이다 — 측정과 일치시키려면 조회에서 잘라야 한다).

    Returns:
        ``string_hits`` + (문자열이 못 잡은 의미 결과). 의미로 걸린 행은 ``match_reason`` 이
        ``"뜻이 가까움 (0.53)"`` 형태다.

    🔴 **왜 점수 융합이 아니라 계층인가.** 이름 질의가 089 에서 **100%** 인데(B2·C3) 점수로
    섞으면 그것을 깨뜨릴 위험이 있다. 문자열로 걸린 것은 **확실한 것**이라 위에 두고, 의미
    검색은 **0건이던 자리를 채우는 용도**로 쓴다. 자산 검색(BM25+kNN 융합)과 다른 선택인 이유:
    자산은 결과가 수백 건이라 순위 품질이 관건이고, 개체는 1~10건이라 **놓치지 않는 것**이
    관건이다.

    🔴 **유사도 컷오프가 없다.** G0·G2 측정에서 컷오프 0.45 는 정답 17/67건을 버렸다 —
    재료가 짧아(중위 68자) 절대값이 전반적으로 낮고 1위로 맞춘 것도 0.33~0.44 다.
    **순위는 믿을 수 있고 절대값은 못 믿는다.**

    ⚠️ **끄는 길**: ``semantic_hits`` 를 비워 부르면 089 동작이 그대로 나온다(되돌림의 실질).
    """
    out: list[dict[str, Any]] = [dict(row) for row in string_hits]
    if not semantic_hits:
        return out

    # 문자열이 이미 잡은 것은 다시 넣지 않는다 — 같은 개체가 두 줄로 보이면 건수가 어긋난다.
    seen = {(str(r.get("entity_type")), str(r.get("entity_uid"))) for r in string_hits}
    by_key = {(str(it.get("entity_type")), str(it.get("entity_uid"))): it for it in items}

    added = 0
    for hit in semantic_hits:
        if max_semantic is not None and added >= max_semantic:
            break
        key = (str(hit.get("entity_type")), str(hit.get("entity_uid")))
        if key in seen:
            continue
        row = by_key.get(key)
        if row is None:
            # 목록에 없는 개체(노출 임계 아래로 내려갔거나 목록이 잘린 경우) — 화면에 보여줄
            # 행이 없으므로 건너뛴다. 벡터는 남아 있어도 목록이 정본이다.
            continue
        similarity = hit.get("similarity")
        reason = (f"{REASON_SEMANTIC} ({float(similarity):.2f})"
                  if isinstance(similarity, (int, float)) else REASON_SEMANTIC)
        out.append({**dict(row), "match_reason": reason})
        seen.add(key)
        added += 1
    return out


# 게이트가 절대 하한을 쓰지 않는다는 사실을 호출부가 넘기지 않아도 되게 못 박아 둔다.
# 근거는 ``search_constants.ENTITY_SEMANTIC_GATE_EPS_DEFAULT`` 주석(분포가 겹쳐 절대값으로는
# 가를 수 없다). ``passes_cutoff`` 는 floor 를 요구하므로 무효값 0.0 을 준다.
_GATE_NO_FLOOR = 0.0


def gate_semantic_hits(
    hits: Sequence[Mapping[str, Any]],
    *,
    eps: float = ENTITY_SEMANTIC_GATE_EPS_DEFAULT,
    top_n: int,
    enabled: bool = True,
) -> list[dict[str, Any]]:
    """의미 결과가 **믿을 만한지** 보고, 아니면 통째로 버린다(순수 · DB 호출 없음 · 090 후속).

    무엇을 푸나: 090 은 유사도 컷오프를 폐기했다. 짧은 재료(중위 68자) 탓에 절대값이 전반적으로
    낮아 컷오프 0.45 가 정답 16건을 버렸기 때문이고, 그 판단은 지금도 맞다. 하지만 그 대가로
    **정답이 아예 없는 질의에도 상위 3이 그대로 나갔다** — `전자제품` 을 물으면 NASA·전주한옥
    마을·식혜가 나온다(사용자 지적 2026-08-31). 기준선 질의 80개가 전부 "정답이 있는" 질의라
    이 실패 모드는 측정된 적이 없었다.

    어떻게 푸나: **자산 검색이 쓰는 그 게이트**(``src/search/fusion.py``)를 그대로 쓴다 —
    ``유지 = (top − baseline) ≥ eps``. ``baseline`` 은 받은 유사도의 **하위 절반 평균**이라,
    "1등이 나머지 무리보다 튀어나왔는가"를 묻는 셈이다. 반 전체가 60점인데 1등이 62점이면 그
    1등은 뜻이 없다. 공식을 이 모듈에 베껴 쓰지 않고 import 하는 이유는 한쪽만 고쳐지는 사고를
    막기 위해서다(자산 쪽 재보정이 여기에도 자동으로 반영되지는 않지만, 정의가 갈리지는 않는다).

    🔴 **받은 것 전부를 넘겨야 한다** — 상위 3만 넘기면 ``baseline`` 이 상위권 평균이 되어
    신호가 죽는다. 호출부는 ``find_similar_entities`` 를 노출 개체 전량으로 부른 뒤 그 결과를
    그대로 넘긴다(개체 82개라 전량 조회가 싸다 · 개체가 크게 늘면 표본 크기를 정하고 **재측정**한다).

    Args:
        hits: ``find_similar_entities`` 결과 전량(유사도 내림차순). ``similarity`` 만 읽는다.
        eps: 상대 신호 하한. 기본값은 실측 확정치 0.15(위 상수 주석에 스윕 표 근거).
        top_n: 통과했을 때 몇 개를 돌려줄지(융합이 얹을 개수 · 090 은 3).
        enabled: 끄면 판정 없이 상위 ``top_n`` 을 그대로 돌려준다 — **되돌림의 실질**이다
            (설정 하나로 090 동작이 복원된다).

    Returns:
        통과하면 상위 ``top_n``(입력 순서 유지), 막히면 **빈 목록**. 입력 행은 고치지 않는다.

    ⚠️ 대가를 숨기지 않는다: 임계 0.15 는 `장군`→이순신처럼 **정답 1위인 것도 4건 버린다**
    (실측). 무관 질의 24개 중 22개를 막는 값이고, 대안(1위−3위 격차)은 같은 것을 잃으면서
    67% 밖에 못 막았다. 목록은 ``tests/test_mm_meta_entity_search.TestGateSemanticHits``.
    """
    if not hits:
        return []
    if not enabled:
        return [dict(h) for h in hits[:top_n]]
    top, baseline = gate_signal([h.get("similarity") for h in hits])
    if not passes_cutoff(top, baseline, eps=eps, floor=_GATE_NO_FLOOR):
        return []
    return [dict(h) for h in hits[:top_n]]


def entity_refine_fields(item: Mapping[str, Any]) -> list[str]:
    """개체 행에서 **결과 내 재검색** 대상 필드를 뽑는다(이름 · 근거 키워드 · 설명문 · 091).

    보는 곳은 위 ``match_entity`` 와 같다. 다른 것은 **용도**다 — ``match_entity`` 는 찾아오기라
    한 토큰만 맞아도 남기지만(OR), 결과 내 재검색은 골라내기라 모든 토큰이 맞아야 남긴다(AND).
    그래서 **``match_entity`` 를 고치지 않고** 필드만 뽑아 코어 좁히기 함수
    (``src.search.refine.refine_rows``)에 넘긴다 — 090 이 087 판정 재료를 건드리지 않고 검색용
    재료 함수를 새로 둔 것과 같은 판단이다.

    Args:
        item: 개체 목록 행. ``name``(str)·``keywords``(str 배열)·``description``(str)을 읽으며,
            없거나 타입이 다르면 그 축은 없는 것으로 본다(집계 결과라 설명문이 아직 없는 개체가 있다).

    Returns:
        빈 값을 제외한 필드 문자열 목록(이름 → 근거 키워드 → 설명문 순).
    """
    out: list[str] = []
    name = item.get("name")
    if isinstance(name, str) and name:
        out.append(name)

    keywords = item.get("keywords")
    # 문자열 하나가 오면 글자 단위로 순회돼 쓰레기 값이 생긴다 — 배열만 받는다.
    if isinstance(keywords, (list, tuple)):
        out.extend(k for k in keywords if isinstance(k, str) and k)

    description = item.get("description")
    if isinstance(description, str) and description:
        out.append(description)
    return out


__all__ = [
    "REASON_SEMANTIC",
    "entity_refine_fields",
    "gate_semantic_hits",
    "fuse_entity_results",
    "REASON_DESCRIPTION",
    "REASON_KEYWORD",
    "match_entity",
    "narrow_entities",
    "split_query",
]
