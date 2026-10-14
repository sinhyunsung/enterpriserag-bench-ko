"""문항 세트 — 논문(EnterpriseRAG-Bench) 문항 형식으로 만든다. 그래야 논문 채점기를 그대로 쓴다.

두 갈래다.
- 생성 문항: 논문의 질문 생성기로 시드 말뭉치에서 만든 것(사실 하나 · 의미 질문)
- 심은 문항: bench_scenario 에 심은 모순 · 노후 · 공백. 정답을 우리가 안다

심은 문항의 정답은 CONFLICTS(Cattan 외, 2025) 의 「기대 행동」을 따른다.
- 갱신형(낡은 정보로 인한 충돌): 최신 값을 우선한다. 옛 값은 덧붙여도 되지만 현재 값처럼 말하면 틀림
- 충돌형: 두 값을 다 말하고 문서끼리 엇갈린다는 것을 짚는다. 하나만 단정하면 틀림
- 노후: 값을 말하되 오래된 기록이라 지금도 맞는지 확인이 필요하다고 짚는다
- 답 없음: 논문(EnterpriseRAG-Bench)의 Info Not Found 기준 그대로 — 없다고 인정했는가
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.prompts.info_not_found_question import GOLD_ANSWER_AND_FACTS  # noqa: E402

from baton_bench.corpus import Doc  # noqa: E402

CATEGORY_LABELS = {
    "basic": "사실 하나",
    "semantic": "돌려 말한 질문",
    "updated": "모순 · 갱신형",
    "conflict": "모순 · 충돌형",
    "stale": "노후",
    "unanswerable": "답 없음",
}


def planted_rows(manifest: dict, docs: list[Doc]) -> list[dict]:
    by_fact = {}
    for doc in docs:
        if doc.origin == "bench":
            by_fact.setdefault(Path(doc.path).stem.rsplit("-", 1)[0], []).append(doc.dsid)
    rows = []
    for item in manifest["planted"]:
        if "question" not in item:
            continue
        category, expect = item["category"], item["expect"]
        if category == "updated":
            gold = (f"현재 값은 {expect['latest']}이다. 예전 문서에는 {expect['outdated']}로 적혀 있었지만 "
                    f"나중 문서에서 {expect['latest']}로 바뀌었다.")
            facts = [f"현재 값은 {expect['latest']}이다.",
                     f"{expect['outdated']}는 현재 값이 아니다."]
        elif category == "conflict":
            a, b = expect["both"]
            gold = (f"문서마다 다르게 적혀 있다. 한 문서는 {a}, 다른 문서는 {b}라고 하며 "
                    f"어느 쪽이 맞는지 확인이 필요하다.")
            facts = [f"{a}라고 적은 문서가 있다.", f"{b}라고 적은 문서가 있다.",
                     "두 문서의 값이 서로 엇갈린다는 점을 밝힌다."]
        elif category == "stale":
            gold = (f"{expect['value']}로 기록돼 있다. 다만 {expect['as_of']} 기록이고 주기적으로 바뀌는 값이라 "
                    f"지금도 맞는지 확인이 필요하다.")
            facts = [f"{expect['value']}로 기록돼 있다.",
                     "기록이 오래돼 현재 값인지 확인이 필요하다는 점을 밝힌다."]
        else:  # unanswerable
            gold, facts = GOLD_ANSWER_AND_FACTS, [GOLD_ANSWER_AND_FACTS]
        rows.append({
            "question_id": item["id"],
            "question_type": category,
            "source_types": sorted({d["source"] for d in item["documents"]}),
            "question": item["question"],
            # 답 없음은 정답 문서가 없다 — 논문의 Info Not Found 와 같다
            "expected_doc_ids": [] if category == "unanswerable" else by_fact.get(item["id"], []),
            "gold_answer": gold,
            "answer_facts": facts,
            "expect": expect,
        })
    return rows


def generated_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        row["question_id"] = "gen-" + row["question_id"]
    return rows


def build(manifest_path: Path, generated_path: Path, docs: list[Doc]) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = generated_rows(generated_path) + planted_rows(manifest, docs)
    known = {doc.dsid for doc in docs}
    for row in rows:
        lost = [d for d in row["expected_doc_ids"] if d not in known]
        if lost:
            raise RuntimeError(f"{row['question_id']}: 정답 문서가 말뭉치에 없다 {lost}")
    return rows
