"""웹 서버: 브라우저와 다른 프로그램이 HTTP로 기능을 쓰게 해주는 REST API.

REST API는 '주소 + 동사'로 기능을 부르는 약속이다.
  GET  /api/claims?q=...        → 검색 (가져오기)
  POST /api/claims              → 주장 등록 (만들기)
요청과 응답은 모두 JSON(중괄호로 된 데이터 형식)으로 주고받는다.
Python 표준 라이브러리(http.server)만 써서 설치 없이 돌아간다.
실서비스에선 FastAPI 같은 프레임워크로 옮기는 게 좋다(README 참고).
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import NotFound, Service, ValidationError

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SCHEMA_FILE = ROOT / "schema" / "claim.schema.json"
MAX_BODY = 64 * 1024

# (메서드, 주소 패턴, 처리 함수 이름) 목록 = 라우팅 표
ROUTES = [
    ("GET", r"^/api/claims$", "search_claims"),
    ("POST", r"^/api/claims$", "create_claim"),
    ("GET", r"^/api/claims/(claim:[a-z0-9]+)$", "get_claim"),
    ("POST", r"^/api/claims/(claim:[a-z0-9]+)/trials$", "add_trial"),
    ("GET", r"^/api/claims/(claim:[a-z0-9]+)/lineage$", "lineage"),
    ("GET", r"^/api/claims/(claim:[a-z0-9]+)/verifiers$", "verifiers"),
    ("GET", r"^/api/people$", "list_people"),
    ("POST", r"^/api/people$", "create_person"),
    ("GET", r"^/api/people/(user:[a-z0-9]+)/portfolio$", "portfolio"),
    ("GET", r"^/api/match/people$", "match_people"),
    ("GET", r"^/schema/claim$", "schema"),
    ("GET", r"^/$", "index"),
]


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")  # 다른 제품에서 호출 허용(연동용)
            self.end_headers()
            self.wfile.write(data)

        def _json_body(self):
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                raise ValidationError(["요청이 너무 커"])
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise ValidationError(["JSON 형식이 잘못됐어"])

        def _dispatch(self, method):
            url = urlparse(self.path)
            self.query = {k: v[0] for k, v in parse_qs(url.query).items()}
            for m, pattern, name in ROUTES:
                match = re.match(pattern, url.path)
                if m == method and match:
                    try:
                        status, body, *ctype = getattr(self, "h_" + name)(*match.groups())
                        return self._send(status, body, *ctype)
                    except ValidationError as e:
                        return self._send(400, {"error": "validation", "details": e.errors})
                    except NotFound as e:
                        return self._send(404, {"error": "not_found", "id": str(e)})
            self._send(404, {"error": "no_route"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, fmt, *args):
            sys.stderr.write("[ib] " + fmt % args + "\n")

        # ----- 처리 함수들 -----
        def h_search_claims(self):
            q = self.query.get("q", "").strip()
            if not q:
                return 200, {"claims": service.store.list_claims()}
            return 200, service.search(q, self.query.get("domain"))

        def h_create_claim(self):
            return 201, service.create_claim(self._json_body())

        def h_get_claim(self, cid):
            return 200, service.get_claim(cid)

        def h_add_trial(self, cid):
            return 201, service.add_trial(cid, self._json_body())

        def h_lineage(self, cid):
            return 200, service.lineage(cid)

        def h_verifiers(self, cid):
            return 200, service.verifiers(cid)

        def h_list_people(self):
            return 200, {"people": service.list_people()}

        def h_create_person(self):
            return 201, service.create_person(self._json_body())

        def h_portfolio(self, pid):
            return 200, service.portfolio(pid)

        def h_match_people(self):
            q = self.query.get("q", "").strip()
            if not q:
                raise ValidationError(["q가 필요해"])
            return 200, service.match_people(q, self.query.get("domain"))

        def h_schema(self):
            return 200, SCHEMA_FILE.read_bytes(), "application/schema+json"

        def h_index(self):
            return 200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8"

    return Handler


def main():
    db = os.environ.get("IB_DB", str(ROOT / "ib.sqlite3"))
    port = int(os.environ.get("IB_PORT", "8000"))
    service = Service(db)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service))
    print(f"Intelligence Base 실행 중 → http://127.0.0.1:{port}  (DB: {db})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
