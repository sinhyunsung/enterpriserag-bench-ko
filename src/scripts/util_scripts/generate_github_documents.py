"""
Generate GitHub documents for one repository, from its fact sheet.

The volume generator walks every source type at once, which is the wrong shape for
checking whether a single repository's contract actually holds. This generates a
handful of documents for one repository so the result can be inspected and validated
before committing to a full run.

Everything the model needs is already on disk: the repository's ``agents.md`` carries
the prose brief plus the machine-written connector contract, and ``_repository.json``
carries the facts. Nothing here re-derives them.

Usage:
    python -m src.scripts.util_scripts.generate_github_documents --repo dure-settlement --count 5
"""

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from src.llm import get_llm
from src.llm.interface import Message
from src.paths import (
    AGENTS_MD_FILE,
    COMPANY_OVERVIEW_PATH,
    INITIATIVES_PATH,
    SOURCES_DIR,
)
from src.utils import load_file

REPOSITORY_METADATA_FILE = "_repository.json"

DOCUMENT_PROMPT = """
아래 저장소에서 실제로 있었을 법한 {kind} 한 건을 JSON 으로 만들어라.

# 저장소 규칙 ({agents_file})
{agents_md}

# 저장소 사실
{facts}

# 회사 개요 (발췌)
{overview}

# 이번 문서의 주제
{topic}

# 지켜야 하는 것
- 위 규칙의 "커넥터 계약" 절을 글자 그대로 지킨다. 필드 이름을 바꾸지 않는다.
- 사람은 등장 가능한 사람 목록 안에서만 고르고, 반드시 login 으로 적는다.
- 작성자와 담당자는 collaborator 안에서 고른다.
- review_comments[].path 는 위에 나열된 실제 경로에서만 고른다.
- 모든 시각은 저장소 활동 기간 안이고 ISO 8601 Z 형식이다.
- 제목·본문·리뷰 대화는 한국어. 코드·경로·브랜치명은 영문.
- 리뷰 대화는 실제 리뷰처럼 오간다 — 지적, 반론, 근거, 수정 후 재확인. 서로
  칭찬만 하는 대화는 실제 저장소에 없다.

JSON 만 출력한다. 설명을 덧붙이지 않는다.
""".strip()

TOPIC_PROMPT = """
아래 저장소에서 {count} 건의 서로 다른 작업 주제를 뽑아라.
같은 기능을 다르게 부른 것이 아니라, 실제로 다른 일이어야 한다.
저장소 사실의 모듈·파일 경로를 고르게 훑어라 — 한 모듈에 몰리면 안 된다.

{facts}

한 줄에 하나씩 아래 형식으로 출력한다. 번호나 기호를 붙이지 않는다.

한국어 주제 | roman-lowercase-slug

슬러그는 파일 이름이 되므로 영문 소문자와 하이픈만 쓰고 서너 낱말로 요약한다.
""".strip()


def slugify(text: str) -> str:
    """
    Roman-lowercase-hyphen slug for the file name.

    Korean text leaves nothing behind once non-ASCII is stripped, so the model is
    asked for a romanised slug alongside each topic; this only tidies that up.
    """
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return ascii_only[:48]


def load_repository(repo: str) -> tuple[dict, str]:
    base = os.path.join(SOURCES_DIR, "github", repo)
    with open(os.path.join(base, REPOSITORY_METADATA_FILE), encoding="utf-8") as handle:
        metadata = json.load(handle)
    return metadata, load_file(os.path.join(base, AGENTS_MD_FILE))


def ask(prompt: str) -> str:
    llm = get_llm(quiet=True)
    return "".join(
        chunk for chunk in llm.generate([Message(role="user", content=prompt)])
        if isinstance(chunk, str)
    )


def parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    text = text.removeprefix("json").strip()
    return json.loads(text)


def generate_one(repo: str, agents_md: str, facts: str, overview: str,
                 topic: str, slug: str, kind: str) -> tuple[str, dict] | None:
    prompt = DOCUMENT_PROMPT.format(
        kind="풀 리퀘스트" if kind == "pull_request" else "이슈",
        agents_file=AGENTS_MD_FILE,
        agents_md=agents_md,
        facts=facts,
        overview=overview[:2500],
        topic=topic,
    )
    try:
        document = parse_json(ask(prompt))
    except (json.JSONDecodeError, ValueError) as error:
        print(f"   [건너뜀] JSON 을 못 읽었다: {topic[:30]} ({error})")
        return None

    issue = document.get("issue") or {}
    number = issue.get("number") or 0
    prefix = "pr" if document.get("kind") == "pull_request" else "issue"
    tail = slugify(slug) or slugify(issue.get("title", "")) or f"doc-{number}"
    name = f"{prefix}-{number}-{tail}.json"
    return name, document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="sources/github 아래 저장소 이름")
    parser.add_argument("--count", type=int, default=5, help="만들 문서 수")
    parser.add_argument("--parallelism", type=int, default=3)
    args = parser.parse_args()

    base = os.path.join(SOURCES_DIR, "github", args.repo)
    if not os.path.isdir(base):
        print(f"저장소가 없습니다: {base}")
        return
    if not os.path.exists(os.path.join(base, REPOSITORY_METADATA_FILE)):
        print(f"{REPOSITORY_METADATA_FILE} 이 없습니다. step_5b 를 먼저 돌리세요.")
        return

    metadata, agents_md = load_repository(args.repo)
    facts = json.dumps(metadata, ensure_ascii=False, indent=1)
    overview = load_file(COMPANY_OVERVIEW_PATH) + "\n" + load_file(INITIATIVES_PATH)

    print(f"저장소 {args.repo} · {args.count}건 생성")
    topics: list[tuple[str, str]] = []
    for line in ask(TOPIC_PROMPT.format(count=args.count, facts=facts)).splitlines():
        line = line.strip()
        if not line:
            continue
        topic, _, slug = line.partition("|")
        topics.append((topic.strip(), slug.strip()))
    topics = topics[: args.count]
    print(f"주제 {len(topics)}개")

    def work(item: tuple[int, tuple[str, str]]):
        index, (topic, slug) = item
        # 이슈도 섞는다 — 저장소에 PR 만 있으면 그것부터가 실제와 다르다
        kind = "issue" if index % 4 == 3 else "pull_request"
        return generate_one(args.repo, agents_md, facts, overview, topic, slug, kind)

    with ThreadPoolExecutor(max_workers=args.parallelism) as pool:
        results = list(pool.map(work, enumerate(topics)))

    written = 0
    for result in results:
        if not result:
            continue
        name, document = result
        with open(os.path.join(base, name), "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
        written += 1
        print(f"  썼음 {name}")

    print(f"\n{written}/{len(topics)} 건 생성. 다음으로 검사하세요:")
    print("  python -m src.scripts.util_scripts.validate_github_shape")


if __name__ == "__main__":
    main()
