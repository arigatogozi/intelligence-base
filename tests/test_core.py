"""자동 테스트: 핵심 규칙이 의도대로 동작하는지 확인한다.

실행: python -m unittest discover tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ib import models  # noqa: E402
from ib.server import make_handler  # noqa: E402
from ib.service import NotFound, Service, ValidationError  # noqa: E402
from seed import seed  # noqa: E402


class StatusRuleTest(unittest.TestCase):
    def test_no_trials_is_unverified(self):
        self.assertEqual(models.compute_status([]), "unverified")

    def test_single_tester_stays_in_review(self):
        self.assertEqual(models.compute_status([{"by": "user:a", "result": "success"}] * 5), "in_review")

    def test_two_successes_reproduced(self):
        t = [{"by": "user:a", "result": "success"}, {"by": "user:b", "result": "success"}]
        self.assertEqual(models.compute_status(t), "reproduced")

    def test_two_fails_refuted(self):
        t = [{"by": "user:a", "result": "fail"}, {"by": "user:b", "result": "fail"}]
        self.assertEqual(models.compute_status(t), "refuted")

    def test_mixed_in_review(self):
        t = [{"by": "user:a", "result": "fail"}, {"by": "user:b", "result": "success"}]
        self.assertEqual(models.compute_status(t), "in_review")

    def test_latest_result_per_person_counts(self):
        t = [{"by": "user:a", "result": "fail"}, {"by": "user:a", "result": "success"},
             {"by": "user:b", "result": "success"}]
        self.assertEqual(models.compute_status(t), "reproduced")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = seed(os.path.join(self.tmp.name, "t.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_tiers(self):
        res = self.s.search("단백질 후보를 실험 전에 빨리 줄이고 싶어", "protein-design")
        tiers = res["tiers"]
        self.assertTrue(all(c["status"] == "reproduced" for c in tiers["verified"]))
        self.assertTrue(any("구조 예측" in c["claim"] for c in tiers["verified"]))
        self.assertTrue(all(c["context"]["domain"] != "protein-design" for c in tiers["similar_context"]))
        self.assertTrue(any("계획-검증 루프" in c["claim"] for c in tiers["fresh"]))

    def test_refuted_hidden(self):
        res = self.s.search("프롬프트 천천히 생각 추론 정확도")
        shown = [c for t in res["tiers"].values() for c in t]
        self.assertFalse(any(c["status"] == "refuted" for c in shown))
        self.assertEqual(res["refuted_hidden"], 1)

    def test_usage_becomes_verification(self):
        c = self.s.create_claim({"claim": "새로운 융합 기법을 시험해 보는 아이디어 문장", "domain": "materials",
                                 "author_id": "user:seoyun"})
        self.assertEqual(c["status"], "unverified")
        c = self.s.add_trial(c["id"], {"person_id": "user:joon", "result": "success"})
        self.assertEqual(c["status"], "in_review")
        c = self.s.add_trial(c["id"], {"person_id": "user:dana", "result": "success"})
        self.assertEqual(c["status"], "reproduced")

    def test_lineage_and_derived_credit(self):
        fused = [c for c in self.s.store.list_claims() if "계획-검증 루프" in c["claim"]][0]
        lin = self.s.lineage(fused["id"])
        self.assertEqual(len([a for a in lin["ancestors"] if not a.get("external")]), 2)
        self.assertTrue(any(a.get("external") for a in lin["ancestors"]))
        roles = {(c["person"], c["role"]) for c in fused["contributors"]}
        self.assertIn(("user:minan", "derived"), roles)
        self.assertEqual(self.s.portfolio("user:minan")["summary"]["built_upon_by_others"], 1)

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.s.create_claim({"claim": "짧음", "domain": "Bad Domain", "author_id": "x"})
        with self.assertRaises(NotFound):
            self.s.create_claim({"claim": "존재하지 않는 계보를 가진 주장 문장", "domain": "materials",
                                 "author_id": "user:seoyun", "derived_from": ["claim:00000000"]})

    def test_verifiers_exclude_author_and_testers(self):
        c = [c for c in self.s.store.list_claims() if "구조 예측" in c["claim"]][0]
        v = self.s.verifiers(c["id"])
        ids = {r["person"]["id"] for r in v["same_domain"] + v["cross_domain"]}
        self.assertNotIn("user:jiwoo", ids)
        self.assertNotIn("user:dana", ids)

    def test_cross_domain_matching(self):
        res = self.s.match_people("단백질 실험을 자동화할 AI 에이전트", "protein-design")
        self.assertTrue(res["same_domain"])
        self.assertTrue(any(r["person"]["id"] == "user:minan" for r in res["cross_domain"]))


class HttpAndMcpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "h.sqlite3")
        seed(cls.db)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Service(cls.db)))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_http_flow(self):
        st, res = self.req("/api/claims?q=" + urllib.parse.quote("단백질 후보 필터링") + "&domain=protein-design")
        self.assertEqual(st, 200)
        self.assertIn("verified", res["tiers"])
        st, c = self.req("/api/claims", {"claim": "HTTP로 올린 테스트용 아이디어 문장", "domain": "llm-agents",
                                         "author_id": "user:minan"})
        self.assertEqual(st, 201)
        st, c = self.req(f"/api/claims/{c['id']}/trials", {"person_id": "user:joon", "result": "success"})
        self.assertEqual((st, c["status"]), (201, "in_review"))
        st, err = self.req("/api/claims", {"claim": "x"})
        self.assertEqual(st, 400)
        st, _ = self.req("/api/claims/claim:00000000")
        self.assertEqual(st, 404)
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn("Intelligence Base", resp.read().decode())

    def test_mcp_stdio(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_knowledge", "arguments": {"query": "단백질 후보 줄이기", "domain": "protein-design"}}},
        ]
        proc = subprocess.run([sys.executable, "-m", "ib.mcp_server"], cwd=ROOT,
                              input="\n".join(json.dumps(m) for m in msgs) + "\n",
                              capture_output=True, text=True, env={**os.environ, "IB_DB": self.db}, timeout=20)
        out = [json.loads(l) for l in proc.stdout.splitlines()]
        self.assertEqual([o["id"] for o in out], [1, 2, 3])
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]},
                         {"search_knowledge", "get_claim", "check_existing", "report_trial"})
        payload = json.loads(out[2]["result"]["content"][0]["text"])
        self.assertTrue(payload["tiers"]["verified"])
        self.assertIn("status", payload["tiers"]["verified"][0])


if __name__ == "__main__":
    unittest.main()
