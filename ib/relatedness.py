"""기술끼리 얼마나 가까운지: 글의 의미 + 행동 데이터 세 가지 신호를 합친다.

1. 의미 거리 (semantic) : 글이 얼마나 비슷한가. 지금은 TF-IDF, 나중에 임베딩으로 교체.
2. 계보 (lineage)       : 공통 조상(기반한 주장·자료)이 얼마나 겹치나.
3. 함께 시도 (co_trial) : 같은 사람들이 둘 다 써 봤나. 실제로 함께 쓰이는 기술이라는 신호.

겹침 정도는 자카드 유사도로 잰다: |A ∩ B| / |A ∪ B|  (둘 다 가진 것 ÷ 둘 중 하나라도 가진 것)
최종 점수 = 0.5 × 의미 + 0.3 × 계보 + 0.2 × 함께 시도
가중치는 임시값이다. 사용자가 실제로 클릭·채택한 데이터가 쌓이면 그걸로 조정해야 한다.
"""
from .search import TfidfIndex, doc_text

WEIGHTS = {"semantic": 0.5, "lineage": 0.3, "co_trial": 0.2}
MIN_SCORE = 0.05


def jaccard(a, b):
    return len(a & b) / len(a | b) if (a or b) else 0.0


def ancestor_sets(claims):
    """주장마다 '자기 자신 + 모든 조상' 집합. 자기 자신을 넣어야 부모-자식도 겹침이 생긴다."""
    by_id = {c["id"]: c for c in claims}
    result = {}
    for c in claims:
        seen, stack = {c["id"]}, list(c["derived_from"])
        while stack:
            ref = stack.pop()
            if ref not in seen:
                seen.add(ref)
                if ref in by_id:
                    stack.extend(by_id[ref]["derived_from"])
        result[c["id"]] = seen
    return result


def related(claim_id, claims, limit=5):
    by_id = {c["id"]: c for c in claims}
    target = by_id[claim_id]
    sem = TfidfIndex({c["id"]: doc_text(c) for c in claims}).score(doc_text(target))
    anc = ancestor_sets(claims)
    testers = {c["id"]: {t["by"] for t in c["trials"]} for c in claims}

    out = []
    for c in claims:
        if c["id"] == claim_id:
            continue
        signals = {
            "semantic": round(sem[c["id"]], 3),
            "lineage": round(jaccard(anc[claim_id], anc[c["id"]]), 3),
            "co_trial": round(jaccard(testers[claim_id], testers[c["id"]]), 3),
        }
        score = sum(WEIGHTS[k] * v for k, v in signals.items())
        if score >= MIN_SCORE:
            out.append({
                "id": c["id"], "claim": c["claim"], "status": c["status"],
                "domain": c["context"]["domain"],
                "cross_domain": c["context"]["domain"] != target["context"]["domain"],
                "score": round(score, 3), "signals": signals,
            })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]
