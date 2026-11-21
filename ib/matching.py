"""매칭: 문제(또는 주장)에 맞는 사람을 찾는다. 검증자 추천도 같은 원리다.

점수 = 역량 유사도(TF-IDF) + 분야 일치 가산점 + 활동 이력 가산점
- 역량 유사도: 그 사람의 skills 텍스트와 문제 텍스트가 얼마나 비슷한가
- 분야 일치: 같은 domain을 다뤄본 사람이면 +0.2
- 활동 이력: 시도(검증) 기록이 많을수록 조금 더 (최대 +0.1)
'다른 분야 사람'을 일부러 섞어 보여주는 것이 이종 역량 매칭의 핵심이다.
"""
import math

from .search import TfidfIndex


def _person_text(p):
    return " ".join(p["skills"] + [d.replace("-", " ") for d in p["domains"]])


def match_people(people, query, domain=None, trial_counts=None, exclude=(), limit=5):
    trial_counts = trial_counts or {}
    candidates = [p for p in people if p["id"] not in exclude]
    if not candidates:
        return {"same_domain": [], "cross_domain": []}
    index = TfidfIndex({p["id"]: _person_text(p) for p in candidates})
    sims = index.score(query)

    results = []
    for p in candidates:
        same = bool(domain and domain in p["domains"])
        activity = min(0.1, 0.03 * math.log1p(trial_counts.get(p["id"], 0)))
        total = sims[p["id"]] + (0.2 if same else 0.0) + activity
        if sims[p["id"]] <= 0 and not same:
            continue
        results.append({
            "person": p,
            "score": round(total, 3),
            "why": {
                "skill_similarity": round(sims[p["id"]], 3),
                "same_domain": same,
                "activity_bonus": round(activity, 3),
            },
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    same_domain = [r for r in results if r["why"]["same_domain"]][:limit]
    cross_domain = [r for r in results if not r["why"]["same_domain"]][:limit]
    return {"same_domain": same_domain, "cross_domain": cross_domain}


def suggest_verifiers(claim, people, trial_counts=None, limit=5):
    """이 주장을 검증해 볼 만한 사람. 작성자와 이미 시도한 사람은 뺀다."""
    exclude = {claim["contributors"][0]["person"]} if claim["contributors"] else set()
    exclude |= {t["by"] for t in claim["trials"]}
    text = claim["claim"] + " " + " ".join(claim["context"].get("conditions", []))
    return match_people(people, text, claim["context"]["domain"], trial_counts, exclude, limit)
