"""Langfuse 에 실험으로 올린다 — 데이터셋(문항) · 실행(시스템별) · 문항별 트레이스 · 점수.

Langfuse 화면에서 데이터셋 → 실행 두 개를 고르면 문항마다 두 답과 점수가 나란히 보인다.

Langfuse 4 는 트레이스를 OpenTelemetry 로만 받는다(옛 trace-create 는 거절). 그래서 OTLP JSON 을
직접 보낸다. 점수와 실행 연결은 공개 REST API 로 보낸다.
"""

import json
import os
import time
import uuid

import httpx


def _attr(key: str, value) -> dict:
    if isinstance(value, list):
        return {"key": key, "value": {"arrayValue": {"values": [{"stringValue": str(v)} for v in value]}}}
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return {"key": key, "value": {"stringValue": value}}


class Langfuse:
    def __init__(self, host: str | None = None):
        self.http = httpx.Client(
            base_url=host or os.environ.get("LANGFUSE_HOST", "http://localhost:2360"),
            auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]), timeout=60)

    def dataset(self, name: str, description: str, questions: list[dict]) -> None:
        self.http.post("/api/public/v2/datasets", json={"name": name, "description": description}).raise_for_status()
        for q in questions:
            self.http.post("/api/public/dataset-items", json={
                "datasetName": name, "id": f"{name}:{q['question_id']}",
                "input": {"question": q["question"]},
                "expectedOutput": {"gold_answer": q["gold_answer"], "answer_facts": q["answer_facts"]},
                "metadata": {"category": q["question_type"], "expected_doc_ids": q["expected_doc_ids"],
                             "question_id": q["question_id"]},
            }).raise_for_status()

    def run(self, dataset: str, run_name: str, system: str, questions: list[dict],
            answers: dict[str, dict], per_q: dict[str, dict], verdicts: dict[str, dict]) -> None:
        spans, scores, links = [], [], []
        now = time.time_ns()
        for q in questions:
            qid = q["question_id"]
            answer, row, verdict = answers.get(qid, {}), per_q[qid], verdicts[qid]
            trace_id, root = uuid.uuid4().hex, uuid.uuid4().hex[:16]
            latency_ns = int(answer.get("latency_ms") or 0) * 1_000_000
            start, end = now - latency_ns, now
            spans.append({
                "traceId": trace_id, "spanId": root, "name": f"{system}:{qid}", "kind": 1,
                "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
                "attributes": [
                    _attr("langfuse.trace.name", f"bench · {system}"),
                    _attr("langfuse.trace.tags", ["bench", system, q["question_type"]]),
                    _attr("langfuse.session.id", run_name),
                    _attr("langfuse.observation.type", "chain"),
                    _attr("langfuse.trace.input", q["question"]),
                    _attr("langfuse.observation.input", q["question"]),
                    _attr("langfuse.trace.output", answer.get("answer", "")),
                    _attr("langfuse.observation.output", answer.get("answer", "")),
                    _attr("langfuse.trace.metadata", {"category": q["question_type"], "verdict": verdict["verdict"],
                                                      "votes": verdict["votes"], "judge_reason": verdict["reason"]}),
                ]})
            spans.append({
                "traceId": trace_id, "spanId": uuid.uuid4().hex[:16], "parentSpanId": root,
                "name": "retrieval", "kind": 1, "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + latency_ns // 3),
                "attributes": [
                    _attr("langfuse.observation.type", "retriever"),
                    _attr("langfuse.observation.input", answer.get("search_text") or q["question"]),
                    _attr("langfuse.observation.output", {"document_ids": answer.get("document_ids", []),
                                                          "expected_doc_ids": q["expected_doc_ids"],
                                                          "evidences": answer.get("evidences")}),
                ]})
            values = {
                "verdict": (verdict["verdict"], "CATEGORICAL"),
                "truthfulness": (verdict["truth"], "NUMERIC"),
                "judge_unanimous": (1 if verdict["unanimous"] else 0, "BOOLEAN"),
                "rule_pass": (None if row["rule_pass"] is None else int(row["rule_pass"]), "BOOLEAN"),
                "ndcg10": (row["ndcg10"], "NUMERIC"),
                "doc_recall": (row["doc_recall"], "NUMERIC"),
                "paper_correct": (None if row["paper_correct"] is None else int(row["paper_correct"]), "BOOLEAN"),
                "paper_completeness": (row["paper_completeness"], "NUMERIC"),
                "latency_ms": (row["latency_ms"], "NUMERIC"),
            }
            for name, (value, kind) in values.items():
                if value is None:
                    continue
                scores.append({"id": str(uuid.uuid4()), "type": "score-create",
                               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "body": {"traceId": trace_id, "name": name, "value": value, "dataType": kind,
                                        **({"comment": verdict["reason"]} if name == "verdict" else {})}})
            links.append({"runName": run_name, "datasetItemId": f"{dataset}:{qid}", "traceId": trace_id,
                          "metadata": {"system": system}})

        for start in range(0, len(spans), 100):
            body = {"resourceSpans": [{"resource": {"attributes": [_attr("service.name", "baton-bench")]},
                                       "scopeSpans": [{"scope": {"name": "baton.bench"},
                                                       "spans": spans[start:start + 100]}]}]}
            self.http.post("/api/public/otel/v1/traces", json=body).raise_for_status()
        for start in range(0, len(scores), 200):
            self.http.post("/api/public/ingestion", json={"batch": scores[start:start + 200]}).raise_for_status()
        for link in links:
            link["runDescription"] = f"{system} — 같은 말뭉치 · 같은 임베딩 · 같은 답변 모델"
            self.http.post("/api/public/dataset-run-items", json=link).raise_for_status()
