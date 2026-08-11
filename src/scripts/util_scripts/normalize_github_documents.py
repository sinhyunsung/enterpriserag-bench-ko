"""
Move generated GitHub documents into the exact shape the connector reads.

Two things drift no matter how firmly the contract states them, and both are
placement problems rather than content problems — the writing is fine, it is sitting
in the wrong field:

1. **Review summaries land in ``review_comments``.** A summary ("전반적으로 좋습니다,
   머지하겠습니다") has no file and no line, but the connector reads that array as
   line comments and needs ``path``. The text belongs in ``comments``.

2. **Bots appear as participants.** ``github-actions`` posting a CI result is exactly
   what a real repository looks like, but the corpus resolves people through the
   employee directory, so a bot becomes an unresolvable principal and quietly widens
   the permission set it implies.

Anything this cannot fix by moving — a missing author, a missing timestamp — is
reported rather than invented, because those change what the document says.

Usage:
    python -m src.scripts.util_scripts.normalize_github_documents
    python -m src.scripts.util_scripts.normalize_github_documents --dry-run
"""

import argparse
import json
import os
import sys
from collections import Counter

import yaml

from src.paths import GENERATED_DATA_DIR

GITHUB_DIR = os.path.join(GENERATED_DATA_DIR, "sources", "github")
DIRECTORY_PATH = os.path.join(GENERATED_DATA_DIR, "employee_directory.yaml")
REPOSITORY_METADATA_FILE = "_repository.json"


def known_logins() -> set[str]:
    with open(DIRECTORY_PATH, encoding="utf-8") as handle:
        directory = yaml.safe_load(handle) or {}
    return {
        person["github_login"]
        for people in (directory.get("departments") or {}).values()
        for person in people or []
        if (person or {}).get("github_login")
    }


def normalize(doc: dict, valid: set[str], counts: Counter) -> bool:
    """Return True if the document changed."""
    changed = False

    comments = doc.get("comments")
    if not isinstance(comments, list):
        comments = []
        doc["comments"] = comments

    reviews = doc.get("review_comments")
    if isinstance(reviews, list):
        kept = []
        for entry in reviews:
            if not isinstance(entry, dict):
                counts["리뷰 항목이 객체가 아니라 버림"] += 1
                changed = True
                continue
            if entry.get("path"):
                kept.append(entry)
                continue
            # 줄에 안 달린 말 — 총평·승인·머지 알림. comments 가 제자리다
            moved = {key: value for key, value in entry.items()
                     if key not in ("path", "line")}
            comments.append(moved)
            counts["총평을 comments 로 옮김"] += 1
            changed = True
        if len(kept) != len(reviews):
            doc["review_comments"] = kept

    # 사람 아닌 참여자를 걷어낸다. 사원에 안 이어지면 권한 계산에 구멍이 된다
    for bucket in ("comments", "review_comments"):
        entries = doc.get(bucket)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            login = (entry.get("user") or {}).get("login") if isinstance(entry, dict) else None
            if login and login not in valid:
                counts[f"디렉터리에 없는 계정의 {bucket} 삭제"] += 1
                changed = True
                continue
            kept.append(entry)
        if len(kept) != len(entries):
            doc[bucket] = kept

    return changed


def unfixable(doc: dict, valid: set[str]) -> list[str]:
    """Problems that cannot be fixed by moving data around."""
    problems: list[str] = []
    issue = doc.get("issue")
    if not isinstance(issue, dict):
        return ["issue 객체 없음"]
    if not (issue.get("user") or {}).get("login"):
        problems.append("작성자 없음")
    elif issue["user"]["login"] not in valid:
        problems.append(f"작성자가 디렉터리에 없음 ({issue['user']['login']})")
    for field in ("title", "body", "created_at", "updated_at"):
        if not issue.get(field):
            problems.append(f"issue.{field} 없음")
    if not issue.get("assignees"):
        problems.append("assignees 비어 있음")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="고치지 않고 보기만")
    args = parser.parse_args()

    valid = known_logins()
    counts: Counter = Counter()
    changed_files = 0
    broken: list[tuple[str, list[str]]] = []

    for root, _, files in os.walk(GITHUB_DIR):
        for name in sorted(files):
            if not name.endswith(".json") or name == REPOSITORY_METADATA_FILE:
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle)

            if normalize(doc, valid, counts):
                changed_files += 1
                if not args.dry_run:
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(doc, handle, ensure_ascii=False, indent=2)

            problems = unfixable(doc, valid)
            if problems:
                broken.append((os.path.relpath(path, GITHUB_DIR), problems))

    print(f"고친 문서 {changed_files}건" + (" (시늉만)" if args.dry_run else ""))
    for message, count in counts.most_common():
        print(f"  {message}: {count}")

    if broken:
        print(f"\n옮기는 것으로는 못 고치는 문서 {len(broken)}건 — 다시 만들어야 합니다")
        for where, problems in broken:
            print(f"  {where}: {' · '.join(problems)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
