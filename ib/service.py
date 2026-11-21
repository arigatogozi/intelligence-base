"""서비스 계층: 웹 API와 MCP 서버가 함께 쓰는 '기능 묶음'.

웹(server.py)과 AI 연동(mcp_server.py)이 같은 기능을 따로 구현하면 어긋나기 쉽다.
그래서 실제 동작은 여기 한 곳에 두고, 두 입구는 이 함수들을 부르기만 한다.
"""
from . import matching, models, prior_art, relatedness, search
from .db import NotFound, Store

PRECHECK_THRESHOLD = 0.1   # 사전 확인에서 '비슷한 기존 주장'으로 보여줄 최소 유사도
PRECHECK_LIMIT = 5


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class Conflict(Exception):
    """작성자가 '이미 있는 것과 같다'고 골랐을 때: 새로 만들지 않고 기존 주장으로 안내한다."""
    def __init__(self, existing_ids):
        super().__init__("이미 있는 주장과 같다고 선택했어. 새로 올리는 대신 그 주장에 써 본 결과를 남겨줘.")
        self.existing_ids = existing_ids


class Service:
    def __init__(self, db_path, prior_art_provider=None):
        self.store = Store(db_path)
        self.prior_art = prior_art_provider or prior_art.get_provider()

    def precheck(self, text, domain=None, tags=()):
        """올리기 전 확인: 비슷한 기존 주장(먼저 올라온 순서 정보 포함)과 외부 선행 자료를 보여준다.
        판정은 하지 않는다. 작성자가 각 후보에 대해 same/derived/distinct를 고르게 하는 재료만 준다."""
        if not isinstance(text, str) or len(text.strip()) < 10:
            raise ValidationError(["확인하려면 주장을 10자 이상 적어줘"])
        claims = self.store.list_claims()
        query = " ".join([text, (domain or "").replace("-", " "), *tags])
        scores = search.TfidfIndex({c["id"]: search.doc_text(c) for c in claims}).score(query) if claims else {}
        similar = sorted(
            ({"id": c["id"], "claim": c["claim"], "status": c["status"], "domain": c["context"]["domain"],
              "author": c["contributors"][0]["person"] if c["contributors"] else None,
              "created_at": c["created_at"], "similarity": round(scores[c["id"]], 3)}
             for c in claims if scores[c["id"]] >= PRECHECK_THRESHOLD),
            key=lambda x: x["similarity"], reverse=True)[:PRECHECK_LIMIT]
        return {
            "similar_existing": similar,
            "choices": {"same": "이미 있는 것과 같아", "derived": "이걸 발전시킨 거야", "distinct": "비슷해 보이지만 달라"},
            "prior_art": self.prior_art.search(text, list(tags)),
        }

    def related(self, cid):
        self.store.get_claim(cid)
        return {"claim_id": cid, "related": relatedness.related(cid, self.store.list_claims()),
                "weights": relatedness.WEIGHTS}

    def search(self, query, domain=None):
        return search.tiered_search(self.store.list_claims(), query, domain or None)

    def create_claim(self, data):
        errors = models.validate_claim_input(data)
        if errors:
            raise ValidationError(errors)
        links = data.get("links", [])
        same = [l["ref"] for l in links if l["relation"] == "same"]
        if same:
            raise Conflict(same)
        refs = list(data.get("derived_from", [])) + [l["ref"] for l in links]
        for ref in refs:
            if ref.startswith("claim:"):
                self.store.get_claim(ref)  # 없는 주장을 걸면 NotFound
        return self.store.add_claim(
            data["claim"], data["domain"], data["author_id"],
            data.get("conditions", []), data.get("tags", []), data.get("derived_from", []), links,
        )

    def get_claim(self, cid):
        return self.store.get_claim(cid)

    def add_trial(self, cid, data):
        errors = models.validate_trial_input(data)
        if errors:
            raise ValidationError(errors)
        return self.store.add_trial(cid, data["person_id"], data["result"], data.get("notes", ""))

    def lineage(self, cid):
        return self.store.lineage(cid)

    def verifiers(self, cid):
        claim = self.store.get_claim(cid)
        return matching.suggest_verifiers(claim, self.store.list_people(), self.store.trial_record())

    def match_people(self, query, domain=None):
        return matching.match_people(self.store.list_people(), query, domain or None, self.store.trial_record())

    def create_person(self, data):
        errors = models.validate_person_input(data)
        if errors:
            raise ValidationError(errors)
        return self.store.add_person(data["name"], data.get("skills", []), data.get("domains", []))

    def list_people(self):
        return self.store.list_people()

    def portfolio(self, pid):
        return self.store.portfolio(pid)


__all__ = ["Service", "ValidationError", "NotFound", "Conflict"]
