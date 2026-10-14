"""집계 — 범주별 점수와 신뢰구간, 두 시스템 차이의 유의성.

- 정답률 신뢰구간: Wilson 95% (문항 수가 적을 때도 0~100% 밖으로 안 나간다)
- 두 시스템 차이: McNemar 정확 검정(같은 문항 쌍에서 한쪽만 맞힌 수를 견준다)
- 검색: 문서 재현율(논문) · nDCG@10(Järvelin & Kekäläinen 2002, BEIR 기본 지표) ·
        근거 정밀도(가져온 문서 중 정답 문서 비율 — RAGAS context precision 의 문서 단위판)
- 운영: 응답 지연 p50 · p95
"""

import math
from statistics import mean

from baton_bench.questions import CATEGORY_LABELS


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p": 1.0}
    tail = sum(math.comb(n, i) for i in range(0, min(only_a, only_b) + 1)) / 2 ** n
    return {"only_a": only_a, "only_b": only_b, "p": min(1.0, 2 * tail)}


def ndcg10(ranked: list[str], relevant: set[str]) -> float | None:
    if not relevant:
        return None
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:10]) if d in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    return dcg / ideal


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def per_question(q: dict, answer: dict, verdict: dict, paper: dict | None) -> dict:
    relevant = set(q["expected_doc_ids"])
    docs = answer.get("document_ids") or []
    return {
        "question_id": q["question_id"],
        "category": q["question_type"],
        "correct": verdict["verdict"] == "correct",
        "verdict": verdict["verdict"],
        "truth": verdict["truth"],
        "unanimous": verdict["unanimous"],
        "rule_pass": (verdict.get("rule") or {}).get("pass"),
        "ndcg10": ndcg10(docs, relevant),
        "doc_recall": len(relevant & set(docs)) / len(relevant) if relevant else None,
        "doc_precision": len(relevant & set(docs)) / len(docs) if relevant and docs else None,
        "paper_correct": paper.get("answer_correct") if paper else None,
        "paper_completeness": paper.get("completeness_pct") if paper else None,
        "paper_invalid_docs": paper.get("invalid_extra_docs") if paper else None,
        "latency_ms": answer.get("latency_ms"),
    }


def _avg(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return mean(values) if values else None


def summarize(rows: list[dict]) -> dict:
    n, k = len(rows), sum(1 for r in rows if r["correct"])
    lo, hi = wilson(k, n)
    rule = [r["rule_pass"] for r in rows if r["rule_pass"] is not None]
    latency = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    return {
        "n": n, "correct": k, "accuracy": k / n if n else None, "ci": [lo, hi],
        "truthfulness": _avg(rows, "truth"),
        "hallucination": sum(1 for r in rows if r["verdict"] == "wrong") / n if n else None,
        "rule_pass": sum(rule) / len(rule) if rule else None,
        "ndcg10": _avg(rows, "ndcg10"), "doc_recall": _avg(rows, "doc_recall"),
        "doc_precision": _avg(rows, "doc_precision"),
        "paper_accuracy": (sum(1 for r in rows if r["paper_correct"]) / n
                           if n and any(r["paper_correct"] is not None for r in rows) else None),
        "paper_completeness": _avg(rows, "paper_completeness"),
        "paper_invalid_docs": _avg(rows, "paper_invalid_docs"),
        "latency_p50": percentile(latency, 0.5), "latency_p95": percentile(latency, 0.95),
        "unanimous": sum(1 for r in rows if r["unanimous"]) / n if n else None,
    }


def scorecard(per_system: dict[str, list[dict]]) -> dict:
    systems = list(per_system)
    categories = [c for c in CATEGORY_LABELS if any(r["category"] == c for r in per_system[systems[0]])]
    card = {"categories": [], "overall": {}}
    for cat in categories:
        entry = {"key": cat, "label": CATEGORY_LABELS[cat], "systems": {}}
        for s in systems:
            entry["systems"][s] = summarize([r for r in per_system[s] if r["category"] == cat])
        a, b = ([r["correct"] for r in per_system[s] if r["category"] == cat] for s in systems)
        entry["mcnemar"] = mcnemar(a, b)
        card["categories"].append(entry)
    for s in systems:
        card["overall"][s] = summarize(per_system[s])
    a, b = ([r["correct"] for r in per_system[s]] for s in systems)
    card["overall_mcnemar"] = mcnemar(a, b)
    return card
