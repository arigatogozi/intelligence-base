"""저장소: SQLite 데이터베이스에 사람·주장·시도·기여를 저장하고 꺼낸다.

SQLite는 파일 하나짜리 데이터베이스라 따로 서버를 띄울 필요가 없다.
나중에 사용자가 많아지면 PostgreSQL로 바꾸면 되고, 이 파일만 고치면 된다.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import models

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    skills TEXT NOT NULL DEFAULT '[]',
    domains TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    domain TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    derived_from TEXT NOT NULL DEFAULT '[]',
    author_id TEXT NOT NULL REFERENCES people(id),
    status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    person_id TEXT NOT NULL REFERENCES people(id),
    result TEXT NOT NULL CHECK (result IN ('success','fail')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    ref TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('derived','distinct')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES people(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    role TEXT NOT NULL CHECK (role IN ('author','trial','derived')),
    created_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class NotFound(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA_SQL)

    @contextmanager
    def _conn(self):
        # 요청마다 새 연결을 열고 끝나면 저장(commit) 후 닫는다.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- 사람 ----------
    def add_person(self, name, skills=(), domains=(), person_id=None):
        pid = person_id or "user:" + uuid.uuid4().hex[:8]
        with self._conn() as c:
            c.execute(
                "INSERT INTO people (id, name, skills, domains, created_at) VALUES (?,?,?,?,?)",
                (pid, name.strip(), json.dumps(list(skills), ensure_ascii=False),
                 json.dumps(list(domains), ensure_ascii=False), now_iso()),
            )
        return self.get_person(pid)

    def get_person(self, pid):
        with self._conn() as c:
            row = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
        if not row:
            raise NotFound(pid)
        return _person_row(row)

    def list_people(self):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM people ORDER BY created_at").fetchall()
        return [_person_row(r) for r in rows]

    # ---------- 주장 ----------
    def add_claim(self, claim, domain, author_id, conditions=(), tags=(), derived_from=(), links=()):
        """links: 작성자가 사전 확인 단계에서 고른 관계 [{"ref": "claim:..", "relation": "derived|distinct"}]
        relation이 derived인 것은 계보(derived_from)에도 자동으로 합쳐진다."""
        self.get_person(author_id)  # 없는 사람이면 NotFound
        derived_from = list(dict.fromkeys(
            list(derived_from) + [l["ref"] for l in links if l["relation"] == "derived"]))
        cid = "claim:" + uuid.uuid4().hex[:8]
        ts = now_iso()
        with self._conn() as c:
            c.execute(
                "INSERT INTO claims (id, claim, domain, conditions, tags, derived_from, author_id, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, claim.strip(), domain, _j(conditions), _j(tags), _j(derived_from),
                 author_id, "unverified", ts),
            )
            c.execute(
                "INSERT INTO contributions (person_id, claim_id, role, created_at) VALUES (?,?,?,?)",
                (author_id, cid, "author", ts),
            )
            for l in links:
                c.execute(
                    "INSERT INTO claim_links (claim_id, ref, relation, created_by, created_at) VALUES (?,?,?,?,?)",
                    (cid, l["ref"], l["relation"], author_id, ts),
                )
            # 다른 주장에서 파생됐다면, 원래 주장의 작성자에게도 'derived' 기여를 기록한다.
            for parent in derived_from:
                if parent.startswith("claim:"):
                    prow = c.execute("SELECT author_id FROM claims WHERE id=?", (parent,)).fetchone()
                    if prow:
                        c.execute(
                            "INSERT INTO contributions (person_id, claim_id, role, created_at) VALUES (?,?,?,?)",
                            (prow["author_id"], cid, "derived", ts),
                        )
        return self.get_claim(cid)

    def get_claim(self, cid):
        with self._conn() as c:
            row = c.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
            if not row:
                raise NotFound(cid)
            trials = c.execute(
                "SELECT person_id, result, notes, created_at FROM trials WHERE claim_id=? ORDER BY id", (cid,)
            ).fetchall()
            contribs = c.execute(
                "SELECT person_id, role, created_at FROM contributions WHERE claim_id=? ORDER BY id", (cid,)
            ).fetchall()
            links = c.execute(
                "SELECT ref, relation, created_by, created_at FROM claim_links WHERE claim_id=? ORDER BY id", (cid,)
            ).fetchall()
        return _claim_to_protocol(row, trials, contribs, links)

    def list_claims(self):
        with self._conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM claims ORDER BY created_at DESC")]
        return [self.get_claim(i) for i in ids]

    # ---------- 시도(=검증) ----------
    def add_trial(self, cid, person_id, result, notes=""):
        claim = self.get_claim(cid)
        self.get_person(person_id)
        ts = now_iso()
        with self._conn() as c:
            c.execute(
                "INSERT INTO trials (claim_id, person_id, result, notes, created_at) VALUES (?,?,?,?,?)",
                (cid, person_id, result, notes, ts),
            )
            already = c.execute(
                "SELECT 1 FROM contributions WHERE claim_id=? AND person_id=? AND role='trial'",
                (cid, person_id),
            ).fetchone()
            if not already:
                c.execute(
                    "INSERT INTO contributions (person_id, claim_id, role, created_at) VALUES (?,?,?,?)",
                    (person_id, cid, "trial", ts),
                )
        # 시도가 추가될 때마다 상태를 다시 계산한다 → 사용이 곧 검증.
        trials = claim["trials"] + [{"by": person_id, "result": result}]
        new_status = models.compute_status(trials)
        with self._conn() as c:
            c.execute("UPDATE claims SET status=? WHERE id=?", (new_status, cid))
        return self.get_claim(cid)

    # ---------- 계보 ----------
    def lineage(self, cid):
        """조상(이 주장이 기반한 것)과 후손(이 주장에서 파생된 것)을 돌려준다."""
        root = self.get_claim(cid)
        all_claims = {c["id"]: c for c in self.list_claims()}

        ancestors, seen = [], set()
        stack = list(root["derived_from"])
        while stack:
            ref = stack.pop()
            if ref in seen:
                continue
            seen.add(ref)
            if ref in all_claims:
                ancestors.append(_brief(all_claims[ref]))
                stack.extend(all_claims[ref]["derived_from"])
            else:
                ancestors.append({"id": ref, "external": True})

        descendants, frontier, seen = [], [cid], {cid}
        while frontier:
            cur = frontier.pop()
            for c in all_claims.values():
                if cur in c["derived_from"] and c["id"] not in seen:
                    seen.add(c["id"])
                    descendants.append(_brief(c))
                    frontier.append(c["id"])
        return {"claim": _brief(root), "ancestors": ancestors, "descendants": descendants}

    # ---------- 포트폴리오 ----------
    def portfolio(self, pid):
        person = self.get_person(pid)
        with self._conn() as c:
            rows = c.execute(
                "SELECT ct.role, ct.created_at, cl.id, cl.claim, cl.status, cl.domain"
                " FROM contributions ct JOIN claims cl ON cl.id = ct.claim_id"
                " WHERE ct.person_id=? ORDER BY ct.id DESC",
                (pid,),
            ).fetchall()
        items = [
            {"role": r["role"], "at": r["created_at"], "claim_id": r["id"], "claim": r["claim"],
             "status": r["status"], "domain": r["domain"]}
            for r in rows
        ]
        summary = {
            "authored": sum(1 for i in items if i["role"] == "author"),
            "authored_reproduced": sum(1 for i in items if i["role"] == "author" and i["status"] == "reproduced"),
            "trials": sum(1 for i in items if i["role"] == "trial"),
            "built_upon_by_others": sum(1 for i in items if i["role"] == "derived"),
        }
        return {"person": person, "summary": summary, "contributions": items}

    def trial_record(self):
        """사람별 시도 횟수 (매칭에서 '활동 이력' 가산점으로 쓴다)."""
        with self._conn() as c:
            rows = c.execute("SELECT person_id, COUNT(*) n FROM trials GROUP BY person_id").fetchall()
        return {r["person_id"]: r["n"] for r in rows}


def _j(v):
    return json.dumps(list(v), ensure_ascii=False)


def _person_row(r):
    return {"id": r["id"], "name": r["name"], "skills": json.loads(r["skills"]),
            "domains": json.loads(r["domains"]), "created_at": r["created_at"]}


def _brief(c):
    return {"id": c["id"], "claim": c["claim"], "status": c["status"], "domain": c["context"]["domain"]}


def _claim_to_protocol(row, trials, contribs, links=()):
    """DB의 한 줄을 프로토콜(schema/claim.schema.json) 모양으로 바꾼다."""
    return {
        "links": [{"ref": l["ref"], "relation": l["relation"], "by": l["created_by"], "at": l["created_at"]}
                  for l in links],
        "spec_version": models.SPEC_VERSION,
        "id": row["id"],
        "claim": row["claim"],
        "status": row["status"],
        "context": {"domain": row["domain"], "conditions": json.loads(row["conditions"])},
        "tags": json.loads(row["tags"]),
        "derived_from": json.loads(row["derived_from"]),
        "contributors": [{"person": c["person_id"], "role": c["role"], "at": c["created_at"]} for c in contribs],
        "trials": [{"by": t["person_id"], "result": t["result"], "notes": t["notes"], "at": t["created_at"]}
                   for t in trials],
        "created_at": row["created_at"],
    }
