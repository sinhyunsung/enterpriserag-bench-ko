"""
Check that generated GitHub documents match the ingestion connector's contract.

Why this exists: the export shape is described in prompts, and a language model
follows prose approximately.  A field that drifts (``author`` instead of
``user.login``) does not crash anything — the connector reads a missing path,
gets nothing, and the document silently loses every participant.  That failure is
invisible until retrieval scoring is already wrong, so it is cheaper to fail here.

The connector reads these paths, and nothing else:

    wrapper  kind, issue, comments[], review_comments[]
    issue    title, body, user.login, assignees[].login,
             requested_reviewers[].login, created_at, updated_at
    comment  user.login, body, created_at, updated_at
    review   the above plus path, line
    repo     private, collaborators[].login

Access is derived as:

    private == false  ->  readable company-wide
    private == true   ->  collaborators + the document's participants

Usage:
    python -m src.scripts.util_scripts.validate_github_shape
"""

import json
import os
import re
import sys
from collections import Counter

import yaml

from src.paths import GENERATED_DATA_DIR


GITHUB_DIR = os.path.join(GENERATED_DATA_DIR, "sources", "github")
DIRECTORY_PATH = os.path.join(GENERATED_DATA_DIR, "employee_directory.yaml")
REPO_METADATA_FILE = "_repository.json"

ISO_8601 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?")


def known_logins() -> set[str]:
    """Every github_login in the employee directory."""
    with open(DIRECTORY_PATH, encoding="utf-8") as handle:
        directory = yaml.safe_load(handle) or {}

    logins: set[str] = set()
    for people in (directory.get("departments") or {}).values():
        for person in people or []:
            login = (person or {}).get("github_login")
            if login:
                logins.add(login)
    return logins


def logins_in(node: object) -> list[str]:
    """Pull every ``login`` value out of a nested structure."""
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("login"), str):
            found.append(node["login"])
        for value in node.values():
            found.extend(logins_in(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(logins_in(item))
    return found


def check_document(path: str, doc: dict, valid: set[str], problems: Counter) -> None:
    """Check one issue/PR wrapper."""
    where = os.path.relpath(path, GITHUB_DIR)

    if doc.get("kind") not in ("issue", "pull_request"):
        problems[f"{where}: kind 가 issue/pull_request 가 아님 ({doc.get('kind')!r})"] += 1

    issue = doc.get("issue")
    if not isinstance(issue, dict):
        problems[f"{where}: issue 객체가 없음 — 커넥터가 문서 전체를 버린다"] += 1
        return

    for field in ("title", "body", "created_at", "updated_at"):
        if not issue.get(field):
            problems[f"{where}: issue.{field} 없음"] += 1

    for field in ("created_at", "updated_at"):
        value = issue.get(field)
        if isinstance(value, str) and not ISO_8601.match(value):
            problems[f"{where}: issue.{field} 가 ISO 8601 이 아님 ({value!r})"] += 1

    if not (issue.get("user") or {}).get("login"):
        problems[f"{where}: issue.user.login 없음 — 작성자를 사원에 못 잇는다"] += 1

    # 담당자가 비면 비공개 저장소에서 읽을 수 있는 사람이 그만큼 좁아진다
    if not issue.get("assignees"):
        problems[f"{where}: assignees 비어 있음"] += 1

    for bucket in ("comments", "review_comments"):
        entries = doc.get(bucket)
        if not isinstance(entries, list):
            problems[f"{where}: {bucket} 가 배열이 아님 — 통짜 문자열이면 화자를 못 가른다"] += 1
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                problems[f"{where}: {bucket}[{index}] 가 객체가 아님"] += 1
                continue
            if not (entry.get("user") or {}).get("login"):
                problems[f"{where}: {bucket}[{index}].user.login 없음"] += 1
            if not entry.get("created_at"):
                problems[f"{where}: {bucket}[{index}].created_at 없음"] += 1
        if bucket == "review_comments":
            for index, entry in enumerate(entries):
                if isinstance(entry, dict) and not entry.get("path"):
                    problems[f"{where}: review_comments[{index}].path 없음"] += 1

    for login in logins_in(doc):
        if login not in valid:
            problems[f"{where}: 사원 디렉터리에 없는 login {login!r}"] += 1


def check_repository(path: str, repo: dict, valid: set[str], problems: Counter) -> None:
    """Check one repository metadata file."""
    where = os.path.relpath(path, GITHUB_DIR)

    if not isinstance(repo.get("private"), bool):
        problems[f"{where}: private 가 참/거짓이 아님 — 공개 범위를 못 정한다"] += 1

    collaborators = repo.get("collaborators")
    if not isinstance(collaborators, list) or not collaborators:
        problems[f"{where}: collaborators 가 비어 있음"] += 1
        return

    for login in logins_in(collaborators):
        if login not in valid:
            problems[f"{where}: 사원 디렉터리에 없는 collaborator {login!r}"] += 1


def check_author_can_read(
    path: str, doc: dict, repos: dict[str, dict], problems: Counter
) -> None:
    """
    On a private repository the author and assignees must be collaborators.

    A document whose own author cannot read the repository it sits in is not a
    permission edge case, it is a contradiction — and it quietly corrupts the
    answer key, because the person the corpus says wrote it would be denied it.
    """
    directory = os.path.relpath(os.path.dirname(path), GITHUB_DIR)
    repo = repos.get(directory)
    if not repo or not repo.get("private"):
        return

    allowed = set(logins_in(repo.get("collaborators") or []))
    issue = doc.get("issue") or {}
    where = os.path.relpath(path, GITHUB_DIR)

    author = (issue.get("user") or {}).get("login")
    if author and author not in allowed:
        problems[f"{where}: 작성자 {author!r} 가 비공개 저장소의 collaborator 가 아님"] += 1

    for assignee in logins_in(issue.get("assignees") or []):
        if assignee not in allowed:
            problems[f"{where}: 담당자 {assignee!r} 가 비공개 저장소의 collaborator 가 아님"] += 1


def main() -> int:
    if not os.path.isdir(GITHUB_DIR):
        print(f"github 디렉터리가 없습니다: {GITHUB_DIR}")
        return 1

    valid = known_logins()
    if not valid:
        print("employee_directory.yaml 에 github_login 이 하나도 없습니다.")
        print("이대로 생성하면 모든 참여자가 미해결로 떨어져 권한 판정이 무의미해집니다.")
        return 1

    problems: Counter = Counter()
    documents = 0
    repositories: dict[str, dict] = {}
    repository_dirs: set[str] = set()
    document_paths: list[tuple[str, dict]] = []

    # 저장소 메타를 먼저 다 모은다 — 작성자가 읽을 수 있는지 보려면 그것이 먼저 필요하다
    for root, _, files in os.walk(GITHUB_DIR):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except json.JSONDecodeError as error:
                problems[f"{os.path.relpath(path, GITHUB_DIR)}: JSON 이 깨짐 ({error})"] += 1
                continue

            if name == REPO_METADATA_FILE:
                repositories[os.path.relpath(root, GITHUB_DIR)] = payload
                check_repository(path, payload, valid, problems)
            else:
                documents += 1
                repository_dirs.add(os.path.relpath(root, GITHUB_DIR))
                document_paths.append((path, payload))

    for path, payload in document_paths:
        check_document(path, payload, valid, problems)
        check_author_can_read(path, payload, repositories, problems)

    missing_metadata = repository_dirs - set(repositories)
    for directory in sorted(missing_metadata):
        problems[f"{directory}: {REPO_METADATA_FILE} 없음 — 이 저장소의 공개 범위를 알 수 없다"] += 1

    print(f"사원 디렉터리의 github_login  {len(valid)}개")
    print(f"문서                          {documents}건")
    print(f"저장소 메타                   {len(repositories)}개")

    if not problems:
        print("\n커넥터 계약을 전부 지킵니다.")
        return 0

    print(f"\n어긋난 곳 {sum(problems.values())}건 (종류 {len(problems)}가지)")
    for message, count in problems.most_common(40):
        print(f"  {message}" + (f"  x{count}" if count > 1 else ""))
    if len(problems) > 40:
        print(f"  … 그 외 {len(problems) - 40}가지")
    return 1


if __name__ == "__main__":
    sys.exit(main())
