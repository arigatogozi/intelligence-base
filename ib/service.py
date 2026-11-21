"""서비스 계층: 웹 API와 MCP 서버가 함께 쓰는 '기능 묶음'.

웹(server.py)과 AI 연동(mcp_server.py)이 같은 기능을 따로 구현하면 어긋나기 쉽다.
그래서 실제 동작은 여기 한 곳에 두고, 두 입구는 이 함수들을 부르기만 한다.
"""
from . import matching, models, search
from .db import NotFound, Store


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class Service:
    def __init__(self, db_path):
        self.store = Store(db_path)

    def search(self, query, domain=None):
        return search.tiered_search(self.store.list_claims(), query, domain or None)

    def create_claim(self, data):
        errors = models.validate_claim_input(data)
        if errors:
            raise ValidationError(errors)
        for ref in data.get("derived_from", []):
            if ref.startswith("claim:"):
                self.store.get_claim(ref)  # 없는 주장을 계보로 걸면 NotFound
        return self.store.add_claim(
            data["claim"], data["domain"], data["author_id"],
            data.get("conditions", []), data.get("tags", []), data.get("derived_from", []),
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


__all__ = ["Service", "ValidationError", "NotFound"]
