"""보고서 HTML — 성적표 한 장 + 막대를 누르면 두 답을 나란히.

파일 하나로 끝난다(외부 스크립트 없음). 데이터는 JSON 으로 박아 넣고 화면은 작은 JS 가 그린다.
"""

import json
from pathlib import Path

from baton_bench.corpus import Doc
from baton_bench.questions import CATEGORY_LABELS

SYSTEM_LABELS = {"baseline": "일반 RAG", "baton": "Baton"}

# 표의 각 줄 — (키, 이름, 형식, 높을수록 좋은가, 근거)
METRIC_ROWS = [
    ("accuracy", "정답률 (심판 판정)", "pct", True, "Zheng 외 2023 (LLM 심판) · CONFLICTS 기대 행동"),
    ("truthfulness", "진실성 점수 (+1 / 0 / −1)", "signed", True, "CRAG, Yang 외 2024"),
    ("hallucination", "틀린 답 비율 (환각)", "pct", False, "CRAG, Yang 외 2024"),
    ("rule_pass", "규칙 검사 통과 (심은 문항)", "pct", True, "정답 값 글자 대조 · 모델 없음"),
    ("paper_accuracy", "논문 채점기 정답률", "pct", True, "EnterpriseRAG-Bench, 2026"),
    ("paper_completeness", "정답 사실 포함률", "pct100", True, "EnterpriseRAG-Bench, 2026"),
    ("doc_recall", "정답 문서 재현율", "pct", True, "EnterpriseRAG-Bench · RAGAS context recall"),
    ("ndcg10", "nDCG@10", "num", True, "Järvelin & Kekäläinen 2002 · BEIR"),
    ("doc_precision", "근거 정밀도", "pct", True, "RAGAS context precision (문서 단위)"),
    ("paper_invalid_docs", "쓸데없는 문서 수 (문항당)", "num", False, "EnterpriseRAG-Bench, 2026"),
    ("latency_p50", "응답 시간 p50", "ms", False, "운영 지표"),
    ("latency_p95", "응답 시간 p95", "ms", False, "운영 지표"),
]


def _doc_view(doc_ids: list[str], expected: set[str], by_dsid: dict[str, Doc]) -> list[dict]:
    out = []
    for d in doc_ids:
        doc = by_dsid.get(d)
        out.append({"title": doc.title if doc else d, "origin": doc.origin if doc else "?", "expected": d in expected})
    return out


def render(card: dict, qs: list[dict], answers: dict, verdicts: dict, per_system: dict, docs: list[Doc]) -> str:
    by_dsid = {d.dsid: d for d in docs}
    rows = {s: {r["question_id"]: r for r in per_system[s]} for s in per_system}
    cases = []
    for q in qs:
        qid, expected = q["question_id"], set(q["expected_doc_ids"])
        case = {"id": qid, "category": q["question_type"], "question": q["question"], "gold": q["gold_answer"],
                "expect": q.get("expect"), "systems": {}}
        for s in per_system:
            a, v = answers[s].get(qid, {}), verdicts[s].get(qid, {})
            evs = a.get("evidences") or []
            case["systems"][s] = {
                "answer": a.get("answer", ""), "verdict": v.get("verdict"), "votes": v.get("votes", {}),
                "reason": v.get("reason", ""), "rule": v.get("rule"),
                "docs": _doc_view(a.get("document_ids") or [], expected, by_dsid),
                "latency": a.get("latency_ms"), "no_evidence": a.get("no_evidence", False),
                "superseded": sum(1 for e in evs if e.get("superseded")),
                "confirmed": sum(1 for e in evs if e.get("humanConfirmed")),
                "ndcg": rows[s][qid]["ndcg10"],
            }
        cases.append(case)
    data = {"card": card, "cases": cases, "systems": SYSTEM_LABELS, "categories": CATEGORY_LABELS,
            "metrics": METRIC_ROWS}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (Path(__file__).with_name("report_template.html").read_text(encoding="utf-8")
            .replace("/*__DATA__*/null", blob))
