"""사전 확인(중복·발전 선택), 외부 선행 자료, 기술 간 거리 테스트.

실행: python -m unittest discover tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ib import prior_art  # noqa: E402
from ib.server import make_handler  # noqa: E402
from ib.service import Conflict, Service  # noqa: E402
from seed import seed  # noqa: E402

# 형식 확인용으로 직접 만든 가짜 arXiv 응답 (실제 논문 아님)
SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/0000.00001v1</id>
    <published>2026-01-15T00:00:00Z</published>
    <title>Sample Title About Planning
      Agents</title>
    <summary>Sample summary text.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/0000.00002v1</id>
    <published>2025-11-02T00:00:00Z</published>
    <title>Second Sample</title>
    <summary>Another sample.</summary>
  </entry>
</feed>"""


class PriorArtTest(unittest.TestCase):
    def test_parse_atom(self):
        items = prior_art.parse_arxiv_atom(SAMPLE_ATOM)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Sample Title About Planning Agents")  # 줄바꿈 정리
        self.assertEqual(items[0]["published"], "2026-01-15")

    def test_arxiv_provider_with_fake_fetch(self):
        called = {}
        def fake(url):
            called["url"] = url
            return SAMPLE_ATOM
        res = prior_art.ArxivProvider(fetch=fake).search("LLM agent planning loop", ["protein", "design"])
        self.assertEqual(len(res["items"]), 2)
        self.assertIn("export.arxiv.org", called["url"])

    def test_korean_only_text_is_skipped(self):
        res = prior_art.ArxivProvider(fetch=lambda u: SAMPLE_ATOM).search("한국어로만 쓴 주장 문장입니다")
        self.assertEqual(res["items"], [])
        self.assertIn("영어 키워드", res["note"])

    def test_network_failure_does_not_raise(self):
        def boom(url):
            raise OSError("offline")
        res = prior_art.ArxivProvider(fetch=boom).search("planning agents protein design")
        self.assertEqual(res["items"], [])
        self.assertIn("실패", res["note"])


class PrecheckAndLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "o.sqlite3")
        seed(self.db)
        self.s = Service(self.db, prior_art.NullProvider())
        self.c2 = [c for c in self.s.store.list_claims() if "구조 예측" in c["claim"]][0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_precheck_finds_earlier_similar(self):
        r = self.s.precheck("구조 예측 모델로 단백질 후보를 먼저 걸러서 실험 후보를 줄인다", "protein-design")
        ids = [x["id"] for x in r["similar_existing"]]
        self.assertIn(self.c2["id"], ids)
        top = r["similar_existing"][0]
        self.assertIn("created_at", top)
        self.assertEqual(top["author"], "user:jiwoo")   # 누가 먼저 올렸는지 보여준다

    def test_same_choice_blocks_new_claim(self):
        with self.assertRaises(Conflict):
            self.s.create_claim({"claim": "구조 예측으로 단백질 후보를 줄이는 같은 방법", "domain": "protein-design",
                                 "author_id": "user:hyun", "links": [{"ref": self.c2["id"], "relation": "same"}]})

    def test_derived_choice_links_lineage_and_credit(self):
        c = self.s.create_claim({
            "claim": "구조 예측 필터에 능동학습을 붙여 실험 라운드를 더 줄인다", "domain": "protein-design",
            "author_id": "user:seoyun", "links": [{"ref": self.c2["id"], "relation": "derived"}]})
        self.assertIn(self.c2["id"], c["derived_from"])
        self.assertEqual(c["links"][0]["relation"], "derived")
        self.assertIn(("user:jiwoo", "derived"), {(x["person"], x["role"]) for x in c["contributors"]})

    def test_distinct_choice_is_recorded_only(self):
        c = self.s.create_claim({
            "claim": "구조 예측 대신 서열 언어모델 점수로 후보를 거른다", "domain": "protein-design",
            "author_id": "user:hyun", "links": [{"ref": self.c2["id"], "relation": "distinct"}]})
        self.assertNotIn(self.c2["id"], c["derived_from"])
        self.assertEqual(c["links"][0]["relation"], "distinct")

    def test_related_uses_three_signals(self):
        fused = [c for c in self.s.store.list_claims() if "계획-검증 루프" in c["claim"]][0]
        rel = {r["id"]: r for r in self.s.related(fused["id"])["related"]}
        c1 = [c for c in self.s.store.list_claims() if "3단계" in c["claim"]][0]
        self.assertIn(c1["id"], rel)
        self.assertGreater(rel[c1["id"]]["signals"]["lineage"], 0)   # 부모-자식이라 계보가 겹친다
        self.assertTrue(rel[c1["id"]]["cross_domain"])
        # c1과 c2는 공통 시도자(다나)가 있어 함께 시도 신호가 생긴다
        rel_c1 = {r["id"]: r for r in self.s.related(c1["id"])["related"]}
        self.assertGreater(rel_c1[self.c2["id"]]["signals"]["co_trial"], 0)


class HttpPrecheckTest(unittest.TestCase):
    def test_http_precheck_and_409(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "h.sqlite3")
        seed(db)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Service(db, prior_art.NullProvider())))
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            def post(path, body):
                r = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                           headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(r) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            st, r = post("/api/claims/precheck", {"claim": "구조 예측 모델로 단백질 후보를 걸러낸다", "domain": "protein-design"})
            self.assertEqual(st, 200)
            ref = r["similar_existing"][0]["id"]
            st, r = post("/api/claims", {"claim": "구조 예측 모델로 단백질 후보를 걸러낸다", "domain": "protein-design",
                                         "author_id": "user:hyun", "links": [{"ref": ref, "relation": "same"}]})
            self.assertEqual((st, r["existing"]), (409, [ref]))
            with urllib.request.urlopen(f"{base}/api/claims/{ref}/related") as resp:
                self.assertIn("signals", json.loads(resp.read())["related"][0])
        finally:
            httpd.shutdown()
            httpd.server_close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
