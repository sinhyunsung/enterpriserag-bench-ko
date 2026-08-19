"""생성기가 뱉은 깃허브 PR 문서를 수집 커넥터가 읽는 export shape 로 맞춘다.

모델이 규격을 매번 지키지는 않는다. 실측으로 네 가지 모양이 나왔다.

  1) 정상        {"issue": {...}, "comments": [...]}
  2) 문자열      {"issue": "{\"number\":...}"}            JSON 을 문자열로 감쌈
  3) 평평        {"number":..., "title":..., ...}          issue 로 안 감쌈
  4) 접두        {"issue_number":..., "issue_title":...}   키에 issue_ 를 붙임
  5) 경로        {"issue_user_login": "x"}                 중첩 경로를 통째로 폄

사람 필드도 {"login": "x"} 대신 "x" 나 {"user_login": "x"} 로 나온다.

이 스크립트는 넷을 모두 1) 로 되돌린다. 이미 맞는 파일은 건드리지 않는다.
"""
import json
import sys
from pathlib import Path

ISSUE_KEYS = [
    "number", "title", "body", "state", "html_url", "user",
    "assignees", "requested_reviewers", "created_at", "updated_at",
]


def as_user(value):
    """사람 하나를 {"login": ...} 로."""
    if isinstance(value, dict):
        for key in ("login", "user_login", "username", "name"):
            if value.get(key):
                return {"login": value[key]}
        return value
    return {"login": value} if value else None


def as_users(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    return [u for u in (as_user(v) for v in (value or [])) if u]


def as_comment(comment):
    if isinstance(comment, str):
        try:
            comment = json.loads(comment)
        except json.JSONDecodeError:
            return None
    if not isinstance(comment, dict):
        return None
    out = dict(comment)
    out["user"] = as_user(comment.get("user") or comment.get("user_login"))
    out.pop("user_login", None)
    return out if out["user"] else None


def normalize(doc: dict) -> dict:
    issue = doc.get("issue")
    if isinstance(issue, str):
        try:
            issue = json.loads(issue)
        except json.JSONDecodeError:
            issue = None

    if not isinstance(issue, dict):
        # 평평하거나 issue_ 접두로 나온 것을 모은다
        issue = {}
        for key in ISSUE_KEYS:
            if key in doc:
                issue[key] = doc.pop(key)
            elif f"issue_{key}" in doc:
                issue[key] = doc.pop(f"issue_{key}")

    # 5) issue_user_login 처럼 중첩 경로를 편 것을 되돌린다
    if not issue.get("user") and doc.get("issue_user_login"):
        issue["user"] = doc.pop("issue_user_login")
    for field in ("assignees", "requested_reviewers"):
        flat = doc.pop(f"issue_{field}_login", None)
        if not issue.get(field) and flat:
            issue[field] = flat

    issue["user"] = as_user(issue.get("user"))
    issue["assignees"] = as_users(issue.get("assignees"))
    issue["requested_reviewers"] = as_users(issue.get("requested_reviewers"))
    doc["issue"] = issue

    for key in ("comments", "review_comments"):
        items = doc.get(key)
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                items = []
        doc[key] = [c for c in (as_comment(c) for c in (items or [])) if c]

    doc["kind"] = "pull_request"
    return doc


def main(paths: list[str]) -> int:
    changed = 0
    for path in paths:
        file = Path(path)
        doc = json.loads(file.read_text())
        before = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        doc = normalize(doc)
        if json.dumps(doc, ensure_ascii=False, sort_keys=True) != before:
            file.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            changed += 1
    print(f"정규화한 파일: {changed}건 / 검사 {len(paths)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
