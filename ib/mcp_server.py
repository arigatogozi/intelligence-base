"""MCP 서버: Claude 같은 AI가 이 지식 베이스를 직접 조회하게 해주는 통로.

MCP(Model Context Protocol)는 AI와 외부 도구 사이의 연결 규격이다.
AI 앱이 이 프로그램을 실행하고, 표준입력/표준출력으로 JSON-RPC 메시지를 한 줄씩 주고받는다.
  AI → {"method": "tools/list"}           : 쓸 수 있는 도구 목록 요청
  AI → {"method": "tools/call", ...}      : 도구 실행 요청
이렇게 하면 AI가 학습하지 못한 최신 지식을 '검증 상태와 함께' 읽어갈 수 있다.
공식 SDK 없이 규격을 직접 구현했으니, 실서비스에선 공식 mcp 패키지로 바꾸는 게 좋다.
"""
import json
import os
import sys
from pathlib import Path

from . import models
from .service import NotFound, Service, ValidationError

PROTOCOL_VERSION = "2025-06-18"
ROOT = Path(__file__).resolve().parent.parent

STATUS_GUIDE = (
    "결과의 status를 반드시 확인해: reproduced=여러 사람이 재현함(신뢰 가능), "
    "in_review=검증 중(참고용), unverified=아직 아무도 확인 안 함(가설로만 취급), "
    "refuted=반박됨(기본 검색에서 제외됨). 사용자에게 답할 때 미검증 내용은 미검증이라고 밝혀."
)

TOOLS = [
    {
        "name": "search_knowledge",
        "description": "초기 기술·융합 분야의 최신 지식과 아이디어를 검색한다. "
                       "결과는 verified / similar_context / fresh 세 칸으로 나뉜다. " + STATUS_GUIDE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "풀고 싶은 문제나 찾는 지식"},
                "domain": {"type": "string", "description": "선택: 분야 ID (예: protein-design)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_claim",
        "description": "주장 하나의 전체 기록(검증 상태, 조건, 계보, 시도 기록)을 가져온다.",
        "inputSchema": {
            "type": "object",
            "properties": {"claim_id": {"type": "string"}},
            "required": ["claim_id"],
        },
    },
    {
        "name": "report_trial",
        "description": "사용자가 어떤 주장을 실제로 적용해 본 결과를 기록한다. "
                       "사용자가 결과를 명시적으로 알려줬을 때만 호출해.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "person_id": {"type": "string"},
                "result": {"enum": ["success", "fail"]},
                "notes": {"type": "string"},
            },
            "required": ["claim_id", "person_id", "result"],
        },
    },
]


def _slim(claim):
    """AI에게 줄 때는 핵심만: 길면 AI의 기억 공간(컨텍스트)을 낭비한다."""
    return {
        "id": claim["id"],
        "claim": claim["claim"],
        "status": claim["status"],
        "status_ko": models.STATUS_KO[claim["status"]],
        "domain": claim["context"]["domain"],
        "conditions": claim["context"].get("conditions", []),
        "trials": len(claim["trials"]),
        "derived_from": claim["derived_from"],
    }


def call_tool(service, name, args):
    if name == "search_knowledge":
        res = service.search(args["query"], args.get("domain"))
        return {
            "guide": STATUS_GUIDE,
            "tiers": {k: [_slim(c) for c in v] for k, v in res["tiers"].items()},
            "refuted_hidden": res["refuted_hidden"],
        }
    if name == "get_claim":
        return service.get_claim(args["claim_id"])
    if name == "report_trial":
        claim = service.add_trial(args["claim_id"], {
            "person_id": args["person_id"], "result": args["result"], "notes": args.get("notes", "")})
        return {"recorded": True, "new_status": claim["status"]}
    raise KeyError(name)


def handle(service, msg):
    """메시지 하나를 처리해 응답(dict)을 돌려준다. 알림(id 없음)이면 None."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notifications/initialized 같은 알림은 응답하지 않는다
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "intelligence-base", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        try:
            data = call_tool(service, params.get("name"), params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
        except KeyError as e:
            return _error(mid, -32602, f"알 수 없는 도구 또는 누락된 인자: {e}")
        except (ValidationError, NotFound) as e:
            result = {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True}
    else:
        return _error(mid, -32601, f"지원하지 않는 메서드: {method}")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main():
    service = Service(os.environ.get("IB_DB", str(ROOT / "ib.sqlite3")))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = _error(None, -32700, "JSON 파싱 실패")
        else:
            resp = handle(service, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
