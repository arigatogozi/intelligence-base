"""데이터 규칙: 입력 검사와 '사용이 곧 검증' 상태 계산.

이 파일은 데이터베이스나 웹과 상관없는 순수한 규칙만 담는다.
그래서 테스트하기 쉽고, 규칙을 바꾸고 싶으면 여기만 고치면 된다.
"""
import re

SPEC_VERSION = "0.1"
STATUSES = ("unverified", "in_review", "reproduced", "refuted")
STATUS_KO = {
    "unverified": "미검증",
    "in_review": "검증 중",
    "reproduced": "재현됨",
    "refuted": "반박됨",
}

# 상태 판정 기준. 숫자를 바꾸면 검증 문턱이 바뀐다.
MIN_DISTINCT_TESTERS = 2   # 서로 다른 사람 최소 몇 명이 시도해야 판정하나
REPRODUCED_RATIO = 0.7     # 성공 비율이 이 이상이면 '재현됨'
REFUTED_RATIO = 0.6        # 실패 비율이 이 이상이면 '반박됨'

DOMAIN_RE = re.compile(r"^[a-z0-9-]+$")


def compute_status(trials):
    """시도 기록 목록으로 검증 상태를 계산한다.

    trials: [{"by": "user:a", "result": "success"}, ...]
    같은 사람이 여러 번 시도하면 마지막 결과 하나만 센다(혼자 도배 방지).
    """
    if not trials:
        return "unverified"
    latest_by_person = {}
    for t in trials:
        latest_by_person[t["by"]] = t["result"]
    testers = len(latest_by_person)
    successes = sum(1 for r in latest_by_person.values() if r == "success")
    if testers < MIN_DISTINCT_TESTERS:
        return "in_review"
    if successes / testers >= REPRODUCED_RATIO:
        return "reproduced"
    if (testers - successes) / testers >= REFUTED_RATIO:
        return "refuted"
    return "in_review"


def validate_claim_input(data):
    """새 주장 입력을 검사하고 오류 메시지 목록을 돌려준다(빈 목록이면 통과)."""
    errors = []
    if not isinstance(data, dict):
        return ["JSON 객체가 필요해"]
    text = data.get("claim")
    if not isinstance(text, str) or not (10 <= len(text.strip()) <= 2000):
        errors.append("claim은 10~2000자 문자열이어야 해")
    domain = data.get("domain")
    if not isinstance(domain, str) or not DOMAIN_RE.match(domain):
        errors.append("domain은 영어 소문자·숫자·하이픈만 써야 해 (예: protein-design)")
    for key in ("conditions", "tags", "derived_from"):
        val = data.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            errors.append(f"{key}는 문자열 목록이어야 해")
    author = data.get("author_id")
    if not isinstance(author, str) or not author.startswith("user:"):
        errors.append("author_id는 user:로 시작해야 해")
    links = data.get("links", [])
    if not isinstance(links, list) or not all(
            isinstance(l, dict) and isinstance(l.get("ref"), str) and l.get("relation") in LINK_CHOICES
            for l in links):
        errors.append("links는 [{ref, relation}] 목록이고 relation은 same/derived/distinct 중 하나여야 해")
    return errors


# 사전 확인 단계에서 작성자가 고를 수 있는 선택지
#   same     : 이미 있는 것과 같다 → 새로 올리지 않고 기존 주장에 시도 기록을 남기게 안내
#   derived  : 이걸 발전시켰다   → 계보(derived_from)에 연결, 원작자에게 파생 기여 기록
#   distinct : 비슷해 보이지만 다르다 → 작성자의 판단으로 기록만 남김
LINK_CHOICES = ("same", "derived", "distinct")


def validate_trial_input(data):
    errors = []
    if not isinstance(data, dict):
        return ["JSON 객체가 필요해"]
    if data.get("result") not in ("success", "fail"):
        errors.append("result는 success 또는 fail이어야 해")
    by = data.get("person_id")
    if not isinstance(by, str) or not by.startswith("user:"):
        errors.append("person_id는 user:로 시작해야 해")
    notes = data.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        errors.append("notes는 2000자 이하 문자열이어야 해")
    return errors


def validate_person_input(data):
    errors = []
    if not isinstance(data, dict):
        return ["JSON 객체가 필요해"]
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name이 필요해")
    for key in ("skills", "domains"):
        val = data.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            errors.append(f"{key}는 문자열 목록이어야 해")
    return errors
