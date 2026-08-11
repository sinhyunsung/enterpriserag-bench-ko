"""Generate a per-repository fact sheet before any document is written.

Why this step exists: without it, every pull request is invented from the company
overview alone. The model re-imagines the technology stack, the file layout and the
cast of people for each document, so a corpus of a hundred documents reads like a
hundred different companies — file paths never repeat, the same person appears as a
backend author in one document and never again, and dates drift outside the company
timeline.

Real repositories are the opposite: the same files keep coming back, the same handful
of people review each other, and the work lines up with a schedule. This step pins
those facts once per repository so later generation has something to be consistent
with.

It writes two files per repository:

    _repository.json   machine-readable — including `private` and `collaborators`,
                       which decide who may read every document in that repository
    agents.md          the same facts as generation rules; the document generators
                       already pull in every agents.md along a file's path, so this
                       reaches them with no further wiring

Usage:
    python -m src.scripts.data_gen_stage_1_generate_clean_data.step_5b_generate_repository_facts
    python -m ...step_5b_generate_repository_facts --source github --yes
"""

import argparse
import json
import os

from src.llm import get_llm
from src.llm.conversation import Conversation
from src.paths import (
    AGENTS_MD_FILE,
    COMPANY_OVERVIEW_PATH,
    EMPLOYEE_DIRECTORY_PATH,
    INITIATIVES_PATH,
    SOURCES_DIR,
)
from src.prompts.repository_facts import REPOSITORY_FACTS_PROMPT
from src.tools.runner import ToolRunner
from src.tools import FINISH_TOOL
from src.tools.tool_implementations import FinishTool, WriteTool
from src.utils import load_file

REPOSITORY_METADATA_FILE = "_repository.json"

STEP_OVERVIEW = """\
Pins the ground truth for each repository before documents are generated:
owning team, technology, real file paths, the cast of people who appear, the
active period, and who is allowed to read it.

Later steps pull these in automatically, so documents stop re-imagining the
company for every file.
"""


def find_repositories(source: str) -> list[str]:
    """Directories one level under sources/<source>, each treated as a repository."""
    root = os.path.join(SOURCES_DIR, source)
    if not os.path.isdir(root):
        return []
    return sorted(
        os.path.join(source, name)
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
    )


def existing_hint(repo_dir: str) -> str:
    """
    Whatever the corpus already says about this repository.

    Re-running the step should sharpen the fact sheet rather than replace it with a
    different company, so anything already written is handed back to the model.
    """
    hints: list[str] = []

    metadata_path = os.path.join(SOURCES_DIR, repo_dir, REPOSITORY_METADATA_FILE)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as handle:
                hints.append(
                    "기존 _repository.json:\n"
                    + json.dumps(json.load(handle), ensure_ascii=False, indent=1)
                )
        except (OSError, json.JSONDecodeError):
            pass

    parent_agents = os.path.join(SOURCES_DIR, os.path.dirname(repo_dir), AGENTS_MD_FILE)
    if os.path.exists(parent_agents):
        hints.append(f"상위 {AGENTS_MD_FILE}:\n" + load_file(parent_agents))

    # 이미 있는 문서 제목은 이 저장소가 무엇을 다루는지 가장 짧게 말해 준다
    titles: list[str] = []
    repo_path = os.path.join(SOURCES_DIR, repo_dir)
    for name in sorted(os.listdir(repo_path))[:400]:
        if not name.endswith(".json") or name == REPOSITORY_METADATA_FILE:
            continue
        try:
            with open(os.path.join(repo_path, name), encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        title = payload.get("title") or (payload.get("issue") or {}).get("title")
        if title:
            titles.append(str(title))
        if len(titles) >= 25:
            break
    if titles:
        hints.append("이미 있는 문서 제목 일부:\n" + "\n".join(f"- {t}" for t in titles))

    return "\n\n".join(hints) if hints else "(아직 아무 정보도 없음)"


def generate_for(repo_dir: str, company_overview: str, initiatives: str,
                 directory_yaml: str) -> None:
    """Run one conversation that writes the two files for a single repository."""
    repo_name = os.path.basename(repo_dir)

    prompt = REPOSITORY_FACTS_PROMPT.format(
        repo_name=repo_name,
        repo_dir=repo_dir,
        repo_hint=existing_hint(repo_dir),
        company_overview_md_contents=company_overview,
        initiatives_md_contents=initiatives,
        employee_directory_yaml_contents=directory_yaml,
    )

    write_tool = WriteTool(base_dir=SOURCES_DIR, allow_create_dirs=False)
    finish_tool = FinishTool()

    llm = get_llm(tools=[write_tool.schema, finish_tool.schema])
    tool_runner = ToolRunner()
    tool_runner.register(write_tool)
    tool_runner.register(finish_tool)

    conversation = Conversation(llm=llm, tool_runner=tool_runner)
    conversation.add_system_message(prompt)
    conversation.run_turn(
        f"{repo_dir} 의 사실 시트를 만들어라. 두 파일을 쓰고 끝내라.",
        exit_on_tools=[FINISH_TOOL],
    )


CONTRACT_MARKER = "## 커넥터 계약 (자동 생성 — 손으로 고치지 않는다)"


def append_connector_contract(repo_dir: str) -> bool:
    """
    Append the connector contract to the repository's agents.md, from its own facts.

    The model writes a good prose brief but skips or paraphrases the mechanical
    parts — in testing it dropped the permission rules entirely.  Those are the
    parts that must be exact, so they are written here from ``_repository.json``
    instead of being asked for: the field shape the connector reads, the exact
    people who may appear, and the visibility that decides who can read the result.
    """
    base = os.path.join(SOURCES_DIR, repo_dir)
    metadata_path = os.path.join(base, REPOSITORY_METADATA_FILE)
    agents_path = os.path.join(base, AGENTS_MD_FILE)
    if not (os.path.exists(metadata_path) and os.path.exists(agents_path)):
        return False

    with open(metadata_path, encoding="utf-8") as handle:
        repo = json.load(handle)

    facts = repo.get("facts") or {}
    collaborators = [c.get("login") for c in repo.get("collaborators") or [] if c.get("login")]
    cast = [c for c in facts.get("cast") or [] if c.get("login")]
    paths = facts.get("common_file_paths") or []
    period = facts.get("active_period") or {}

    visibility = (
        "이 저장소는 공개(private=false)라 회사 전체가 읽을 수 있다."
        if repo.get("private") is False
        else "이 저장소는 비공개(private=true)라 아래 collaborator 와 그 문서의 참여자만 읽을 수 있다."
    )

    lines = [
        "",
        CONTRACT_MARKER,
        "",
        "사람은 이름이 아니라 github_login 으로만 적는다. 이름으로 적으면 수집 시스템이",
        "그 사람을 사원에 잇지 못하고, 그 사람 때문에 생기는 권한이 통째로 사라진다.",
        "",
        "### 파일 모양",
        "```json",
        '{"kind":"pull_request", "issue":{"number":0,"title":"","body":"","state":"",',
        ' "html_url":"","user":{"login":""},"assignees":[{"login":""}],',
        ' "requested_reviewers":[{"login":""}],"created_at":"","updated_at":""},',
        ' "comments":[{"user":{"login":""},"body":"","created_at":"","updated_at":""}],',
        ' "review_comments":[{"user":{"login":""},"body":"","path":"","line":0,',
        '                     "created_at":"","updated_at":""}]}',
        "```",
        "리뷰 대화는 화자와 시각을 가진 객체 배열이다. 한 덩어리 문자열로 적지 않는다.",
        "",
        "### 등장할 수 있는 사람",
    ]
    for member in cast:
        lines.append(f"- `{member['login']}` — {member.get('role', '')}")
    other = [login for login in collaborators if login not in {c["login"] for c in cast}]
    if other:
        lines.append("- 그 밖에 등장 가능: " + ", ".join(f"`{login}`" for login in other))
    lines += [
        "",
        "### 읽을 수 있는 사람",
        visibility,
        "- collaborators: " + (", ".join(f"`{login}`" for login in collaborators) or "(없음)"),
        "- **작성자와 담당자는 반드시 위 collaborator 안에서 고른다.** 자기가 못 읽는",
        "  저장소에 글을 쓴 것으로 나오면 정답지가 어긋난다.",
        "",
        "### 날짜와 파일 경로",
        f"- 모든 시각은 {period.get('from', '?')} ~ {period.get('to', '?')} 안이고 ISO 8601 Z 형식이다.",
    ]
    if paths:
        lines.append("- `review_comments[].path` 는 이 저장소에 실제로 있는 경로에서 고른다:")
        lines += [f"  - `{path}`" for path in paths[:20]]

    with open(agents_path, encoding="utf-8") as handle:
        existing = handle.read()
    if CONTRACT_MARKER in existing:
        existing = existing.split(CONTRACT_MARKER)[0].rstrip() + "\n"

    with open(agents_path, "w", encoding="utf-8") as handle:
        handle.write(existing.rstrip() + "\n" + "\n".join(lines) + "\n")
    return True


def written_files(repo_dir: str) -> tuple[bool, bool]:
    base = os.path.join(SOURCES_DIR, repo_dir)
    return (
        os.path.exists(os.path.join(base, REPOSITORY_METADATA_FILE)),
        os.path.exists(os.path.join(base, AGENTS_MD_FILE)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="github",
                        help="sources/ 아래 소스 이름 (기본 github)")
    parser.add_argument("--yes", action="store_true", help="확인 없이 진행")
    args = parser.parse_args()

    repositories = find_repositories(args.source)
    if not repositories:
        print(f"sources/{args.source} 아래에 저장소 디렉터리가 없습니다.")
        print("먼저 step_4 로 소스 구조를 만드세요.")
        return

    company_overview = load_file(COMPANY_OVERVIEW_PATH)
    initiatives = load_file(INITIATIVES_PATH)
    directory_yaml = load_file(EMPLOYEE_DIRECTORY_PATH)

    if "github_login" not in directory_yaml:
        print("employee_directory.yaml 에 github_login 이 없습니다.")
        print("사람을 계정으로 잇지 못하면 권한이 전부 미해결로 떨어집니다.")
        print("step_3 을 다시 돌려 디렉터리부터 만드세요.")
        return

    print("Step 5b: Repository Fact Sheets")
    print("=" * 40)
    print(STEP_OVERVIEW)
    print(f"대상 {len(repositories)}개: " + ", ".join(repositories))
    if not args.yes:
        input("\nPress Enter to begin...")

    for repo_dir in repositories:
        print(f"\n── {repo_dir}")
        generate_for(repo_dir, company_overview, initiatives, directory_yaml)
        metadata, agents = written_files(repo_dir)
        contract = append_connector_contract(repo_dir) if (metadata and agents) else False
        print(f"   {REPOSITORY_METADATA_FILE} {'있음' if metadata else '없음'}"
              f" · {AGENTS_MD_FILE} {'있음' if agents else '없음'}"
              f" · 커넥터 계약 {'붙임' if contract else '못 붙임'}")

    print("\n끝났습니다. 다음으로 검사하세요:")
    print("  python -m src.scripts.util_scripts.validate_github_shape")


if __name__ == "__main__":
    main()
