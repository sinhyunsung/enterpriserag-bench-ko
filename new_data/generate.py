"""한 사람 기준 인수인계 데이터셋을 만든다.

    python -m new_data.generate --out new_data/sources
    python -m new_data.generate --out new_data/sources --limit 12   # 맛보기

**1,000건 때와 같은 것.** 조직도(`generated_data/employee_directory.yaml`)를 그대로 쓰고,
문서 모양도 커넥터가 파싱하는 그 규격이다(`src/utils/connector_schema.py` 로 검사).
글은 같은 LLM 설정(`src/llm`)으로 쓴다.

**달라진 것.** 주제를 모델에게 맡기지 않는다. 각본(`new_data/scenario.py`)이 업무 열 개와
심을 것 여섯 개를 정해 두고, 생성기는 그 칸을 채우기만 한다.

**심은 문장은 글자 그대로 들어가야 한다.** 모델이 말을 바꾸면 린트가 못 잡는다 —
모순은 주어·속성이 같아야 짝이 되고, 노후는 주기가 적혀 있어야 오래됐는지 잴 수 있다.
그래서 쓴 뒤에 문장이 그대로 있는지 보고, 없으면 그 자리에서 다시 시킨다.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm import Message, get_llm  # noqa: E402
from src.utils.connector_schema import make_validator  # noqa: E402

from new_data import scenario  # noqa: E402

TEAM_ID = "T0DURETECH"
DIRECTORY = "generated_data/employee_directory.yaml"


def slack_user_id(name: str) -> str:
    """이름 → 슬랙 user id. 1,000건 때 쓰던 규칙 그대로라 같은 사람이 같은 id 가 된다."""
    return "U" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8].upper()


def channel_id(name: str) -> str:
    return "C" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8].upper()


def doc_uuid(seed: str) -> str:
    return "dsid_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def ts_of(iso: str, offset: int = 0) -> str:
    """ISO 시각 → 슬랙 ts. 스레드 안에서 순서가 지켜지도록 offset 초를 더한다."""
    from datetime import datetime

    when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{int(when.timestamp()) + offset}.{offset:06d}"


def load_people() -> dict:
    """조직도를 읽어 이름 → 깃허브 로그인. 없는 사람을 지어내지 않으려고 확인용으로 쓴다."""
    raw = yaml.safe_load(Path(DIRECTORY).read_text(encoding="utf-8"))
    found = {}
    for members in raw["departments"].values():
        for person in members:
            found[person["name"]] = person.get("github_login", "")
    return found


SYSTEM = """\
너는 두레테크라는 한국 물류 스타트업의 사내 기록을 쓴다.
실제로 오간 슬랙 스레드와 깃허브 PR 처럼 쓴다.

지켜야 할 것.
- 한국어. 존댓말과 반말이 섞인 실제 대화체.
- 사람은 주어진 이름만 쓴다. 새 사람을 지어내지 않는다.
- 숫자와 날짜는 주어진 것을 그대로 쓴다.
- 광고 문구나 요약체를 쓰지 않는다. 일하다 남긴 기록처럼 쓴다.
- 결과는 JSON 하나만. 설명이나 코드펜스를 붙이지 않는다.
"""

SLACK_SHAPE = """\
{"kind":"slack_thread","team_id":"T0DURETECH","channel":"<CHANNEL_ID>",
 "channel_info":{"id":"<CHANNEL_ID>","name":"<CHANNEL_NAME>"},
 "messages":[{"type":"message","user":"<USER_ID>","user_name":"<이름>","text":"...","ts":"<TS>"}],
 "title_field_name":"channel_info","content_field_names":["messages"]}
messages 는 3~7개. ts 는 주어진 것을 순서대로 쓴다."""

GITHUB_SHAPE = """\
{"kind":"pull_request",
 "issue":{"number":<NUM>,"title":"...","body":"## 배경\\n...\\n\\n## 변경\\n...",
   "state":"merged","html_url":"https://github.com/duretech/<REPO>/pull/<NUM>",
   "user":{"login":"<LOGIN>"},"assignees":[{"login":"<LOGIN>"}],
   "requested_reviewers":[{"login":"<REVIEWER>"}],
   "created_at":"<AT>","updated_at":"<AT>"},
 "comments":[{"user":{"login":"..."},"body":"..."}],
 "review_comments":[]}
comments 는 1~4개."""


def ask(llm, prompt: str) -> str:
    chunks = []
    for piece in llm.generate([Message(role="system", content=SYSTEM),
                               Message(role="user", content=prompt)]):
        if isinstance(piece, str):
            chunks.append(piece)
    return "".join(chunks)


def parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def build_prompt(job: dict) -> str:
    people = ", ".join(f"{p['name']}({p['github']}, {p['role']})" for p in scenario.CAST)
    target = scenario.TARGET
    lines = [
        f"업무: {job['work']} — {job['about']}",
        f"기준 인물: {target['name']}({target['github']}, {target['title']}, {target['department']})",
        f"같이 나올 수 있는 사람: {people}",
        "",
    ]
    if job.get("fact"):
        lines += [
            "이 문서에는 아래 문장이 **글자 하나 안 바꾸고 그대로** 들어가야 한다.",
            f"    {job['fact']}",
            f"맥락: {job['note']}",
            "그 문장 앞뒤로 자연스러운 대화나 설명을 붙여라. 문장 자체는 손대지 마라.",
            "",
        ]
    else:
        lines += [f"{target['name']} 이(가) 실제로 한 일처럼 쓴다. 평범한 작업 기록이다.", ""]

    if job["source"] == "github":
        lines += [
            "깃허브 PR 로 쓴다. 아래 모양을 지킨다.", GITHUB_SHAPE, "",
            f"REPO={job['repo']}  NUM={job['number']}  AT={job['at']}",
            f"user.login 과 assignees 는 {target['github']} 로 한다.",
        ]
    else:
        lines += [
            "슬랙 스레드로 쓴다. 아래 모양을 지킨다.", SLACK_SHAPE, "",
            f"CHANNEL_ID={job['channel_id']}  CHANNEL_NAME={job['channel']}",
            "쓸 수 있는 사람과 user id:",
            f"    {target['name']} = {slack_user_id(target['name'])}",
        ] + [f"    {p['name']} = {slack_user_id(p['name'])}" for p in scenario.CAST] + [
            "ts 는 아래 값을 순서대로 쓴다(모자라면 앞에서부터 다시 쓰지 말고 개수를 줄여라):",
            "    " + ", ".join(ts_of(job["at"], i) for i in range(7)),
        ]
    return "\n".join(lines)


def make_one(job: dict, llm) -> tuple[str, dict] | None:
    """문서 하나. 규격과 심은 문장을 통과할 때까지 최대 세 번 시킨다."""
    validate = make_validator(job["source"])
    prompt = build_prompt(job)
    for attempt in range(3):
        doc = parse_json(ask(llm, prompt))
        if doc is None:
            prompt = build_prompt(job) + "\n\nJSON 이 아니었다. JSON 하나만 다시 써라."
            continue
        problem = validate(json.dumps(doc, ensure_ascii=False))
        if problem:
            prompt = build_prompt(job) + f"\n\n앞서 쓴 것이 규격에 안 맞았다: {problem}\n고쳐서 다시 써라."
            continue
        # 심은 문장이 그대로 있는지. 없으면 린트가 못 잡으므로 통과시키면 안 된다
        if job.get("fact") and job["fact"] not in json.dumps(doc, ensure_ascii=False):
            prompt = build_prompt(job) + (
                f"\n\n앞서 쓴 것에 아래 문장이 그대로 없었다. 반드시 그대로 넣어라.\n    {job['fact']}"
            )
            continue
        doc["dataset_doc_uuid"] = doc_uuid(job["name"])
        return job["name"], doc
    return None


def plan_jobs(limit: int | None) -> list[dict]:
    """심은 것을 먼저 넣고 나머지를 업무별로 고르게 채운다."""
    rng = random.Random(20260819)
    jobs: list[dict] = []
    number = 3000

    for planted in scenario.PLANTED:
        work = next(w for w in scenario.WORKS if w["key"] == planted["work"])
        for index, doc in enumerate(planted["docs"]):
            source = "github" if doc["source"] == "github" else "slack"
            channel = rng.choice(scenario.CHANNELS)
            number += 1
            jobs.append({
                "name": f"{planted['id']}-{index + 1}",
                "source": source,
                "work": work["key"], "about": work["about"],
                "at": doc["at"], "fact": doc["fact"], "note": doc["note"],
                "planted": planted["id"], "kind": planted["kind"],
                "channel": channel, "channel_id": channel_id(channel),
                "repo": rng.choice(scenario.REPOS), "number": number,
            })

    filler = (limit or scenario.TOTAL_DOCS) - len(jobs)
    months = ["2025-10", "2025-12", "2026-01", "2026-02", "2026-03",
              "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    for index in range(max(0, filler)):
        work = scenario.WORKS[index % len(scenario.WORKS)]
        source = "github" if index % 3 == 0 else "slack"
        channel = rng.choice(scenario.CHANNELS)
        number += 1
        month = months[index % len(months)]
        jobs.append({
            "name": f"{work['key']}-{index + 1}",
            "source": source,
            "work": work["key"], "about": work["about"],
            "at": f"{month}-{rng.randint(1, 28):02d}T0{rng.randint(1, 9)}:{rng.randint(10, 59)}:00Z",
            "fact": None, "note": None, "planted": None, "kind": None,
            "channel": channel, "channel_id": channel_id(channel),
            "repo": rng.choice(scenario.REPOS), "number": number,
        })
    return jobs[: limit or scenario.TOTAL_DOCS]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="new_data/sources")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--parallelism", type=int, default=6)
    args = parser.parse_args()

    people = load_people()
    for person in [scenario.TARGET] + scenario.CAST:
        assert person["name"] in people, f"조직도에 없는 사람: {person['name']}"

    jobs = plan_jobs(args.limit)
    out = Path(args.out)
    (out / "slack").mkdir(parents=True, exist_ok=True)
    (out / "github").mkdir(parents=True, exist_ok=True)

    llm = get_llm(quiet=True)
    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=args.parallelism) as pool:
        futures = {pool.submit(make_one, job, llm): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                result = None
                print(f"  ! {job['name']}: {error}")
            if result is None:
                failed.append(job["name"])
                continue
            name, doc = result
            safe = re.sub(r"[^0-9A-Za-z가-힣-]", "-", name)
            (out / job["source"] / f"{safe}.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            done += 1
            print(f"  {done}/{len(jobs)}  {name}")

    manifest = {
        "target": scenario.TARGET,
        "works": [w["key"] for w in scenario.WORKS],
        "planted": [{"id": p["id"], "kind": p["kind"], "work": p["work"], "why": p["why"],
                     # 문장과 함께 삼단 줄도 남긴다. 린트가 짝을 찾는 열쇠가 주어·속성이라
                     # 무슨 문장을 넣었는지만으로는 왜 잡히는지 되짚을 수 없다
                     "facts": [d["fact"] for d in p["docs"]],
                     "triples": [scenario.triple_line(d["triple"])
                                 for d in p["docs"] if "triple" in d]}
                    for p in scenario.PLANTED],
        "documents": done,
        "failed": failed,
    }
    (out.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  만든 문서 {done}개 · 실패 {len(failed)}개")
    if failed:
        print("  실패:", ", ".join(failed))


if __name__ == "__main__":
    main()
