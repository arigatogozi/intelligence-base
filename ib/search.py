"""검색: 질문과 비슷한 주장을 찾아 '검증됨 / 유사 맥락 / 미검증 최신' 세 칸으로 나눈다.

유사도는 TF-IDF + 코사인 유사도로 계산한다.
- TF-IDF: 어떤 단어가 이 글에 자주 나오면서(TF) 다른 글엔 드물면(IDF) 중요한 단어로 본다.
- 코사인 유사도: 두 글을 '단어 점수 벡터'로 만들고 방향이 얼마나 비슷한지 0~1로 잰다.
한국어는 조사(은/는/을/를)가 붙어 단어가 달라 보이므로 두 글자씩 자른 조각(바이그램)도 함께 쓴다.
나중에는 이 부분을 임베딩 모델(의미 기반 검색)로 바꾸는 게 1순위 개선점이다.
"""
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

WORD_RE = re.compile(r"[0-9a-zA-Z]+|[가-힣]+")
HANGUL_RE = re.compile(r"[가-힣]")

SIM_THRESHOLD = 0.08     # 이보다 덜 비슷하면 관련 없다고 본다
CROSS_THRESHOLD = 0.03   # 다른 분야 해법은 '탐색용'이라 기준을 느슨하게 둔다
FRESH_DAYS = 30          # '최신' 칸에 넣을 기간
TIER_LIMIT = 5           # 칸당 최대 개수


def tokenize(text):
    tokens = []
    for w in WORD_RE.findall(text.lower()):
        if HANGUL_RE.match(w):
            if len(w) == 1:
                tokens.append(w)
            tokens.extend(w[i:i + 2] for i in range(len(w) - 1))
        elif len(w) > 1:
            tokens.append(w)
    return tokens


def doc_text(claim):
    ctx = claim["context"]
    return " ".join([claim["claim"], ctx["domain"].replace("-", " "), " ".join(ctx.get("conditions", [])),
                     " ".join(claim.get("tags", []))])


class TfidfIndex:
    def __init__(self, docs):
        """docs: {id: 텍스트}"""
        self.ids = list(docs)
        token_lists = {i: tokenize(t) for i, t in docs.items()}
        n = max(len(docs), 1)
        df = Counter()
        for toks in token_lists.values():
            df.update(set(toks))
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = {i: self._vec(toks) for i, toks in token_lists.items()}

    def _vec(self, tokens):
        tf = Counter(tokens)
        v = {t: c * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def score(self, query):
        q = self._vec(tokenize(query))
        return {i: sum(q.get(t, 0.0) * w for t, w in v.items()) for i, v in self.vecs.items()}


def tiered_search(claims, query, domain=None, now=None):
    """세 칸으로 나눈 검색 결과를 돌려준다.

    verified        : 재현된 주장 중 관련 있는 것 (먼저 믿고 쓸 것)
    similar_context : 다른 분야(domain)에서 나온 비슷한 해법 (융합의 씨앗). domain을 안 주면 '검증 중'인 것
    fresh           : 최근 올라온 미검증·검증 중 아이디어 (아직 아무도 확인 안 한 최전선)
    반박된 주장은 기본 결과에서 빼고, 개수만 알려준다.
    """
    now = now or datetime.now(timezone.utc)
    index = TfidfIndex({c["id"]: doc_text(c) for c in claims})
    scores = index.score(query + " " + (domain or "").replace("-", " "))
    by_id = {c["id"]: c for c in claims}

    loose = [(scores[i], by_id[i]) for i in index.ids if scores[i] >= CROSS_THRESHOLD]
    loose.sort(key=lambda x: x[0], reverse=True)
    relevant = [(s, c) for s, c in loose if s >= SIM_THRESHOLD]

    def pack(score, c):
        return {**c, "score": round(score, 3)}

    verified, similar, fresh, refuted_count, used = [], [], [], 0, set()
    for s, c in relevant:
        if c["status"] == "refuted":
            refuted_count += 1
        elif c["status"] == "reproduced" and len(verified) < TIER_LIMIT:
            verified.append(pack(s, c)); used.add(c["id"])

    for s, c in loose:
        if c["id"] in used or c["status"] == "refuted" or len(similar) >= TIER_LIMIT:
            continue
        if domain:
            ok = c["context"]["domain"] != domain          # 다른 분야의 해법 (상태 무관, 상태는 표시됨)
        else:
            ok = s >= SIM_THRESHOLD and c["status"] == "in_review"
        if ok:
            similar.append(pack(s, c)); used.add(c["id"])

    cutoff = now - timedelta(days=FRESH_DAYS)
    fresh_candidates = [
        (s, c) for s, c in relevant
        if c["id"] not in used and c["status"] in ("unverified", "in_review")
        and datetime.fromisoformat(c["created_at"]) >= cutoff
    ]
    fresh_candidates.sort(key=lambda x: x[1]["created_at"], reverse=True)  # 최신순
    fresh = [pack(s, c) for s, c in fresh_candidates[:TIER_LIMIT]]

    return {
        "query": query,
        "domain": domain,
        "tiers": {"verified": verified, "similar_context": similar, "fresh": fresh},
        "refuted_hidden": refuted_count,
    }
