"""데모용 예시 데이터를 넣는다. 내용은 기능 시연을 위한 가상의 예시다.

실행: python seed.py   (기존 ib.sqlite3가 있으면 지우고 새로 만든다)
"""
import os
import sys
from pathlib import Path

from ib.service import Service

ROOT = Path(__file__).resolve().parent


def seed(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    s = Service(db_path)
    st = s.store

    people = {
        "minan": st.add_person("민안", ["AI 에이전트", "프롬프트 설계", "파이썬"], ["llm-agents"], "user:minan"),
        "jiwoo": st.add_person("지우", ["단백질 구조", "확산 모델", "생물정보학"], ["protein-design"], "user:jiwoo"),
        "seoyun": st.add_person("서윤", ["그래프 신경망", "물성 예측", "능동학습"], ["materials"], "user:seoyun"),
        "hyun": st.add_person("현", ["Lean", "형식 증명", "조합론"], ["formal-math"], "user:hyun"),
        "dana": st.add_person("다나", ["실험 설계", "단백질 발현", "AI 에이전트 활용"], ["protein-design", "llm-agents"], "user:dana"),
        "joon": st.add_person("준", ["강화학습", "에이전트 평가", "벤치마크"], ["llm-agents"], "user:joon"),
    }
    P = {k: v["id"] for k, v in people.items()}

    c1 = st.add_claim("LLM 에이전트에게 작업 전에 계획을 3단계로 먼저 쓰게 하면 다단계 코딩 과제 성공률이 오른다",
                      "llm-agents", P["minan"], ["범용 코딩 에이전트", "10단계 이상 과제"], ["계획", "에이전트"])
    st.add_trial(c1["id"], P["joon"], "success", "자체 벤치마크 20문항 중 성공 +4")
    st.add_trial(c1["id"], P["dana"], "success", "실험 자동화 스크립트에서 효과 있음")

    c2 = st.add_claim("확산 모델로 생성한 단백질 후보를 구조 예측 모델로 먼저 걸러내면 실험할 후보 수를 크게 줄일 수 있다",
                      "protein-design", P["jiwoo"], ["후보 1000개 이상", "구조 신뢰도 점수 사용"], ["단백질", "필터링"])
    st.add_trial(c2["id"], P["dana"], "success", "실험 후보 1/5로 감소")
    st.add_trial(c2["id"], P["seoyun"], "success")

    st.add_claim("LLM 에이전트의 계획-검증 루프를 단백질 설계 파이프라인에 붙이면 실패할 후보를 실험 전에 더 일찍 걸러낸다",
                 "protein-design", P["dana"], ["c1의 3단계 계획 방식", "c2의 필터 이후 단계"], ["융합", "에이전트", "단백질"],
                 [c1["id"], c2["id"], "doi:10.0000/example"])

    c4 = st.add_claim("그래프 신경망 물성 예측에 능동학습을 붙이면 신소재 탐색에 필요한 실험 횟수를 줄일 수 있다",
                      "materials", P["seoyun"], ["결정 구조 데이터"], ["능동학습", "탐색"])
    st.add_trial(c4["id"], P["joon"], "success", "시뮬레이션에서만 확인")

    c5 = st.add_claim("프롬프트에 '천천히 생각해'만 덧붙이면 모든 추론 과제의 정확도가 오른다",
                      "llm-agents", P["joon"], [], ["프롬프트"])
    st.add_trial(c5["id"], P["minan"], "fail", "최신 추론 모델에선 차이 없음")
    st.add_trial(c5["id"], P["dana"], "fail")

    c6 = st.add_claim("Lean 증명을 AI와 쓸 때 필요한 보조정리 목록을 먼저 뽑게 하면 증명 완성률이 오른다",
                      "formal-math", P["hyun"], ["Mathlib 사용", "중간 난이도 정리"], ["Lean", "에이전트"])
    st.add_trial(c6["id"], P["minan"], "success")
    return s


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "ib.sqlite3")
    seed(db)
    print(f"예시 데이터 생성 완료 → {db}")
