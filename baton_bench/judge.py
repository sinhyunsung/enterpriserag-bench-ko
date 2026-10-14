"""자동 채점 — 사람 판정 없이 두 겹으로 잰다.

1. 규칙 검사(모델 없음): 심은 문항은 정답 값을 우리가 안다. 답에 그 값이 글자로 있는지 본다
2. 심판(LLM-as-a-judge): 범주마다 CONFLICTS(Cattan 외, 2025) 의 기대 행동 기준으로
   맞음 · 부분 · 틀림 · 답 못함 중 하나를 고른다
   - 심판: DeepSeek v4 Pro(온도 0) 한 표
   - 약점: 답변 모델과 같은 계열이라 자기 답을 편애할 수 있다. 두 시스템 다 DeepSeek 로 답하므로
     한쪽만 편드는 치우침은 아니다. Bedrock Claude 심판은 지웠다 (2026-10-14)
   - 근거: Zheng 외(2023, MT-Bench) — 강한 모델 심판과 사람의 일치율(85%)이 사람끼리(81%)만큼 높다

CRAG(Yang 외, 2024) 진실성 점수로 바꾼다: 맞음 +1, 부분 +0.5, 답 못함 0, 틀림 −1.
틀린 답(환각)을 답 안 함보다 나쁘게 친다 — 사내 위키에서 그럴듯한 오답이 가장 비싸다.
"""

import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

# 한 표면 된다 (2026-10-14 결정). 여럿으로 늘리면 다수결 · 일치도가 다시 살아난다
JUDGES = [("pro", "deepseek-v4-pro", 0.0)]
TRUTH = {"correct": 1.0, "partial": 0.5, "abstain": 0.0, "wrong": -1.0}

RUBRIC = {
    "basic": "정답과 같은 사실을 말하면 correct. 핵심 사실 일부만 맞으면 partial. 다른 값을 말하면 wrong. "
             "모른다 · 찾지 못했다고 하면 abstain.",
    "semantic": "정답과 같은 사실을 말하면 correct. 핵심 사실 일부만 맞으면 partial. 다른 값을 말하면 wrong. "
                "모른다 · 찾지 못했다고 하면 abstain.",
    "updated": "값이 시간이 지나며 바뀐 경우다. 최신 값을 현재 값으로 답하면 correct (옛 값을 이전 값이라고 덧붙여도 correct). "
               "최신 값과 옛 값을 둘 다 내놓고 어느 것이 현재인지 정하지 못하면 partial. "
               "옛 값을 현재 값처럼 답하면 wrong. 모른다고 하면 abstain.",
    "conflict": "문서끼리 값이 엇갈리는 경우다. 두 값을 모두 말하고 서로 엇갈린다는 점을 짚으면 correct. "
                "두 값을 다 말했지만 엇갈림을 짚지 않거나, 엇갈림을 짚었지만 한 값만 말하면 partial. "
                "한 값만 사실처럼 단정하면 wrong. 모른다고 하면 abstain.",
    "stale": "오래된 기록만 있는 경우다. 값을 말하면서 기록 시점이 오래됐거나 지금도 맞는지 확인이 필요하다고 짚으면 correct. "
             "값은 맞지만 오래된 기록이라는 점을 짚지 않으면 partial. 다른 값을 말하면 wrong. 모른다고 하면 abstain.",
    "unanswerable": "말뭉치에 답이 없는 질문이다. 찾을 수 없다 · 모른다고 하면 correct. "
                    "그럴듯한 값을 지어내 답하면 wrong. 관련 정보를 나열하되 답은 없다고 분명히 하면 correct. "
                    "(이 범주에는 abstain 을 쓰지 않는다)",
}

PROMPT = """사내 지식 위키 챗봇의 답을 채점한다. 기준에 맞춰 판정만 한다.

[범주별 기준]
{rubric}

[질문]
{question}

[정답]
{gold}

[챗봇의 답]
{answer}

JSON 한 줄로만 답한다: {{"verdict": "correct|partial|wrong|abstain", "reason": "한 문장"}}"""


def _client() -> httpx.Client:
    return httpx.Client(base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                        headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
                        timeout=120)


def _vote(client, model: str, temperature: float, prompt: str) -> dict:
    for _ in range(3):
        try:
            response = client.post("/chat/completions", json={
                "model": model, "max_tokens": 300, "temperature": temperature,
                "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": prompt}]})
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            if parsed.get("verdict") in TRUTH:
                return parsed
        except Exception as error:  # noqa: BLE001 — 다시 묻는다
            last = repr(error)
    return {"verdict": "error", "reason": locals().get("last", "형식 오류")}


def judge_one(client, question: dict, answer_text: str) -> dict:
    prompt = PROMPT.format(rubric=RUBRIC[question["question_type"]], question=question["question"],
                           gold=question["gold_answer"], answer=answer_text or "(빈 답)")
    votes = {name: _vote(client, model, temp, prompt) for name, model, temp in JUDGES}
    counts = Counter(v["verdict"] for v in votes.values() if v["verdict"] in TRUTH)
    verdict, top = counts.most_common(1)[0] if counts else ("error", 0)
    if top == 1 and len(counts) == 3:
        verdict = votes["pro"]["verdict"]  # 셋이 다 다르면 온도 0 Pro 를 따른다
    if question["question_type"] == "unanswerable" and verdict == "abstain":
        verdict = "correct"
    return {"verdict": verdict, "truth": TRUTH.get(verdict), "unanimous": top == 3,
            "votes": {name: v["verdict"] for name, v in votes.items()},
            "reason": votes["pro"].get("reason", "")}


def _norm(text: str) -> str:
    return re.sub(r"[\s,·*]", "", text or "")


def rule_check(question: dict, answer_text: str) -> dict | None:
    """심은 문항만. 값이 글자로 들어 있는지만 본다 — 해석 없이 재현 가능한 하한선."""
    expect, kind = question.get("expect"), question["question_type"]
    if not expect:
        return None
    answer = _norm(answer_text)
    has = lambda value: _norm(value) in answer  # noqa: E731
    if kind == "updated":
        return {"latest": has(expect["latest"]), "outdated_mentioned": has(expect["outdated"]),
                "pass": has(expect["latest"])}
    if kind == "conflict":
        a, b = expect["both"]
        return {"a": has(a), "b": has(b), "pass": has(a) and has(b)}
    if kind == "stale":
        flagged = bool(re.search(r"오래|확인이 ?필요|최신.{0,6}(아닐|아님|확인)|기준|당시|변경됐을|바뀌었을|노후", answer_text or ""))
        return {"value": has(expect["value"]), "flagged": flagged, "pass": has(expect["value"]) and flagged}
    if kind == "unanswerable":
        abstained = bool(re.search(r"없|찾지 ?못|확인되지 ?않|모르|알 ?수 ?없", answer_text or ""))
        return {"abstained": abstained, "pass": abstained}
    return None


def judge_all(questions: list[dict], answers: dict[str, dict], workers: int = 6) -> dict[str, dict]:
    client = _client()

    def one(q: dict) -> tuple[str, dict]:
        text = answers.get(q["question_id"], {}).get("answer", "")
        result = judge_one(client, q, text)
        result["rule"] = rule_check(q, text)
        return q["question_id"], result

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, questions))


def fleiss_kappa(results: list[dict]) -> float | None:
    """심판끼리의 일치도(둘 이상일 때만). 0.6 넘으면 상당한 일치(Landis & Koch 1977)."""
    cats = list(TRUTH)
    rows = [[sum(1 for v in r["votes"].values() if v == c) for c in cats] for r in results
            if all(v in TRUTH for v in r["votes"].values())]
    if not rows or len(JUDGES) < 2:  # 심판이 하나면 일치도를 잴 수 없다
        return None
    n, k = len(rows), len(JUDGES)
    p_i = [(sum(x * x for x in row) - k) / (k * (k - 1)) for row in rows]
    p_bar = sum(p_i) / n
    p_j = [sum(row[j] for row in rows) / (n * k) for j in range(len(cats))]
    p_e = sum(p * p for p in p_j)
    return None if p_e == 1 else (p_bar - p_e) / (1 - p_e)
