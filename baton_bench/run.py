"""인제스트가 끝난 말뭉치로 「일반 RAG vs Baton」 보고서를 만든다. 사람 손이 안 간다.

    .venv/bin/python -m baton_bench.run            # 전부(이어 달리기 — 끝난 단계는 건너뜀)
    .venv/bin/python -m baton_bench.run --langfuse # Langfuse 실험으로도 올린다

결과는 bench_runs/<말뭉치 지문>/ 에 쌓인다. 말뭉치가 바뀌면 지문이 바뀌어 새 폴더에서 처음부터 돈다.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LLM_PROVIDER", "deepseek")  # 답변 모델 — Baton 과 같은 DeepSeek
sys.path.insert(0, str(ROOT))

from baton_bench import answers, corpus, judge, metrics, questions, report  # noqa: E402
from baton_bench.baton_api import Baton  # noqa: E402

SYSTEMS = ["baseline", "baton"]


def _jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["question_id"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:2350")
    p.add_argument("--org", default="org-baton")
    p.add_argument("--email", default="admin@baton.local")
    p.add_argument("--password", default="baton-local-admin")
    p.add_argument("--seed", default="/home/shs/batone/baton-be/tools/seed/data")
    p.add_argument("--bench", default=str(ROOT / "new_data/bench/sources"))
    p.add_argument("--manifest", default=str(ROOT / "new_data/bench/manifest.json"))
    p.add_argument("--generated", default=str(ROOT / "questions_bench.jsonl"))
    p.add_argument("--langfuse", action="store_true")
    p.add_argument("--only", nargs="*", help="문항 id 일부만(시험용)")
    args = p.parse_args()

    baton = Baton(args.base, args.email, args.password, args.org)
    uploaded = baton.documents()
    pending = [d["documentId"] for d in uploaded if d.get("status") not in ("DONE", None)]
    if pending:
        sys.exit(f"아직 인제스트 중인 문서 {len(pending)}건 — 끝난 뒤 다시 돌린다: {pending[:3]}")
    docs = corpus.load(Path(args.seed), Path(args.bench), uploaded)
    fp = corpus.fingerprint(docs)
    out = ROOT / "bench_runs" / fp
    out.mkdir(parents=True, exist_ok=True)
    print(f"말뭉치 {len(docs)}건 · 지문 {fp} · 결과 {out}")

    # 1. 문항 · 논문 채점기용 문서 색인
    qs = questions.build(Path(args.manifest), Path(args.generated), docs)
    if args.only:
        qs = [q for q in qs if q["question_id"] in args.only]
    (out / "questions.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in qs), encoding="utf-8")
    (out / "uuid_index.json").write_text(json.dumps({d.dsid: d.path for d in docs}), encoding="utf-8")
    print(f"문항 {len(qs)}개")

    # 2. 답변
    from baton_bench.baseline import Embedder, VectorRag
    rag = VectorRag(docs, ROOT / "baton_bench_cache", fp, Embedder())
    answers.run_baseline(rag, docs, qs, out / "answers_baseline.jsonl")
    answers.run_baton(baton, docs, qs, out / "answers_baton.jsonl")

    per_system, verdicts_all, answers_all = {}, {}, {}
    for system in SYSTEMS:
        ans = _jsonl(out / f"answers_{system}.jsonl")
        answers_all[system] = ans

        # 3. 심판 + 규칙 검사 (문항 단위 캐시)
        vpath = out / f"verdicts_{system}.json"
        verdicts = json.loads(vpath.read_text()) if vpath.exists() else {}
        todo = [q for q in qs if q["question_id"] not in verdicts and q["question_id"] in ans]
        if todo:
            print(f"심판 {system}: {len(todo)}문항")
            verdicts.update(judge.judge_all(todo, ans))
            vpath.write_text(json.dumps(verdicts, ensure_ascii=False, indent=1))
        verdicts_all[system] = verdicts

        # 4. 논문 채점기(EnterpriseRAG-Bench metrics_based_eval) — 정답 문서 재보정 없이
        ppath = out / f"paper_{system}.json"
        env = {**os.environ, "LLM_PROVIDER": "deepseek"}
        subprocess.run([sys.executable, "-m", "src.scripts.answer_evaluation.metrics_based_eval",
                        "--answers-file", str(out / f"answers_{system}.jsonl"),
                        "--questions-file", str(out / "questions.jsonl"),
                        "--updated-questions-file", str(out / f"questions_updated_{system}.jsonl"),
                        "--results-file", str(ppath), "--uuid-index-cache-file", str(out / "uuid_index.json"),
                        "--parallelism", "6", "--no-correction", "--resume"],
                       cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
        paper = {r["question_id"]: r for r in json.loads(ppath.read_text())["questions"]}

        per_system[system] = [metrics.per_question(q, ans.get(q["question_id"], {}), verdicts[q["question_id"]],
                                                   paper.get(q["question_id"])) for q in qs]

    # 5. 성적표
    card = metrics.scorecard(per_system)
    card["judge_kappa"] = {s: judge.fleiss_kappa(list(verdicts_all[s].values())) for s in SYSTEMS}
    card["meta"] = {"fingerprint": fp, "docs": len(docs), "seed_docs": sum(d.origin == "seed" for d in docs),
                    "bench_docs": sum(d.origin == "bench" for d in docs), "questions": len(qs),
                    "generated_at": time.strftime("%Y-%m-%d %H:%M")}
    (out / "scorecard.json").write_text(json.dumps(card, ensure_ascii=False, indent=1))

    html = report.render(card, qs, answers_all, verdicts_all, per_system, docs)
    (out / "report.html").write_text(html, encoding="utf-8")
    print(f"보고서: {out / 'report.html'}")

    # 6. Langfuse 실험
    if args.langfuse:
        from baton_bench.langfuse_push import Langfuse
        lf = Langfuse()
        name = f"baton-bench-{fp}"
        lf.dataset(name, f"일반 RAG vs Baton · 문서 {len(docs)}건 · 문항 {len(qs)}개", qs)
        stamp = time.strftime("%m%d-%H%M")
        for system in SYSTEMS:
            rows = {r["question_id"]: r for r in per_system[system]}
            lf.run(name, f"{system}-{stamp}", system, qs, answers_all[system], rows, verdicts_all[system])
        print(f"Langfuse 데이터셋 {name} 에 실행 {len(SYSTEMS)}개를 올렸다")


if __name__ == "__main__":
    main()
