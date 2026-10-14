"""두 시스템에 같은 문항을 묻고 논문 채점기가 읽는 형식(question_id · answer · document_ids)으로 남긴다.

문서 id 는 논문 채점기가 쓰는 dataset_doc_uuid 로 맞춘다. Baton 은 근거(evidence)의 문서 id 를,
일반 RAG 는 프롬프트에 넣은 상위 10개 문서를 낸다 — 둘 다 「답하려고 본 문서」다.

이어 달리기: 이미 답한 문항은 건너뛴다. 실패(오류)한 문항은 다시 돌린다.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from baton_bench.baseline import VectorRag
from baton_bench.baton_api import Baton
from baton_bench.corpus import Doc


def _done(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["question_id"]: row for row in rows if not row.get("error")}


def _write(path: Path, rows: dict[str, dict], order: list[str]) -> None:
    path.write_text("".join(json.dumps(rows[q], ensure_ascii=False) + "\n" for q in order if q in rows),
                    encoding="utf-8")


def run_baseline(rag: VectorRag, docs: list[Doc], questions: list[dict], out: Path, workers: int = 4) -> None:
    dsid = {doc.baton_id: doc.dsid for doc in docs}
    rows = _done(out)
    todo = [q for q in questions if q["question_id"] not in rows]

    def one(q: dict) -> dict:
        try:
            result = rag.answer(q["question"])
        except Exception as error:  # noqa: BLE001 — 한 문항 실패로 전체를 멈추지 않는다
            return {"question_id": q["question_id"], "answer": "", "document_ids": [], "error": repr(error)}
        return {"question_id": q["question_id"], "answer": result["text"],
                "document_ids": [dsid[d] for d in result["doc_ids"]],
                "latency_ms": result["latency_ms"], "prompt": result["prompt"]}

    with ThreadPoolExecutor(workers) as pool:
        for row in pool.map(one, todo):
            rows[row["question_id"]] = row
            print(f"  일반 RAG {row['question_id']} {'오류 ' + row['error'] if row.get('error') else 'ok'}", flush=True)
            _write(out, rows, [q["question_id"] for q in questions])


def run_baton(baton: Baton, docs: list[Doc], questions: list[dict], out: Path, workers: int = 3) -> None:
    dsid = {doc.baton_id: doc.dsid for doc in docs}
    rows = _done(out)
    todo = [q for q in questions if q["question_id"] not in rows]

    def one(q: dict) -> dict:
        answer = baton.ask(q["question"])
        seen: list[str] = []
        for ev in answer.evidences:
            d = dsid.get(ev.get("documentId"))
            if d and d not in seen:
                seen.append(d)
        row = {"question_id": q["question_id"], "answer": answer.text, "document_ids": seen,
               "latency_ms": answer.latency_ms, "first_token_ms": answer.first_token_ms,
               "search_text": answer.search_text, "no_evidence": answer.no_evidence,
               "ask_back": answer.ask_back, "evidences": answer.evidences}
        if answer.error or not (answer.text or answer.no_evidence):
            row["error"] = answer.error or "빈 답"
        return row

    with ThreadPoolExecutor(workers) as pool:
        for row in pool.map(one, todo):
            rows[row["question_id"]] = row
            print(f"  Baton {row['question_id']} {'오류 ' + row['error'] if row.get('error') else 'ok'}", flush=True)
            _write(out, rows, [q["question_id"] for q in questions])
