"""Utility for extracting content from labeled documents."""


class DocumentFieldError(Exception):
    """Raised when document field labels are missing or invalid."""

    pass


def extract_document_content(doc_data: dict) -> tuple[str, str]:
    """
    Extract title and content from a document using field labels.

    Expects the document to have:
    - title_field_name: Name of the field containing the title
    - content_field_names: List of field names containing the content

    Args:
        doc_data: Parsed JSON document data.

    Returns:
        (title, content) tuple.

    Raises:
        DocumentFieldError: If field labels are missing or invalid.
    """
    # Validate title_field_name exists
    if "title_field_name" not in doc_data:
        unlabeled = _extract_unlabeled(doc_data)
        if unlabeled is not None:
            return unlabeled
        raise DocumentFieldError("Document missing 'title_field_name' field")

    title_field_name = doc_data["title_field_name"]
    if title_field_name not in doc_data:
        raise DocumentFieldError(
            f"title_field_name '{title_field_name}' not found in document"
        )

    title = str(doc_data[title_field_name])

    # Validate content_field_names exists
    if "content_field_names" not in doc_data:
        raise DocumentFieldError("Document missing 'content_field_names' field")

    content_field_names = doc_data["content_field_names"]
    if not isinstance(content_field_names, list):
        raise DocumentFieldError("'content_field_names' must be a list")

    if not content_field_names:
        raise DocumentFieldError("'content_field_names' is empty")

    # Validate all content field names exist in document
    for field_name in content_field_names:
        if field_name not in doc_data:
            raise DocumentFieldError(
                f"content_field_name '{field_name}' not found in document"
            )

    # Build content string
    if len(content_field_names) == 1:
        # Single content field - just use the value
        content = str(doc_data[content_field_names[0]])
    else:
        # Multiple content fields - include keys as headers
        content_parts = []
        for field_name in content_field_names:
            value = doc_data[field_name]
            # Handle list values
            if isinstance(value, list):
                value = "\n".join(str(v) for v in value)
            content_parts.append(f"{field_name}:\n{value}")
        content = "\n\n".join(content_parts)

    return (title, content)


def _extract_unlabeled(doc_data: dict) -> tuple[str, str] | None:
    """
    Read documents that never went through field labeling.

    한국어 말뭉치는 대량 생성(1단계 step 9)만 돌려서 라벨 붙이는 단계를 안 거쳤다. 깃허브는
    Baton 커넥터에 넣으려고 GitHub API 모양(issue / comments / review_comments)으로 만들어서
    평평한 칸 이름이 없다. 그대로 두면 깃허브 490건이 질문 재료에서 빠져 슬랙만 재게 된다.

    Returns None for shapes this does not recognise, so the caller raises as before.
    """
    # 깃허브 PR·이슈
    issue = doc_data.get("issue")
    if isinstance(issue, dict):
        parts = [str(issue.get("body") or "")]
        for key in ("comments", "review_comments"):
            for comment in doc_data.get(key) or []:
                user = comment.get("user")
                who = user.get("login") if isinstance(user, dict) else user
                parts.append(f"[{key}] {who}: {comment.get('body') or ''}")
        return str(issue.get("title") or ""), "\n\n".join(p for p in parts if p)

    # 라벨이 안 붙은 슬랙 — 라벨 붙은 슬랙(channel_info / messages)과 같은 모양으로 낸다.
    # 다르게 읽으면 같은 슬랙인데 질문 재료가 두 모양으로 갈린다
    if isinstance(doc_data.get("messages"), list) and "channel" in doc_data:
        title = doc_data.get("channel_info", doc_data["channel"])
        return str(title), str(doc_data["messages"])

    # 깃허브 저장소 정보
    if "facts" in doc_data and "name" in doc_data:
        facts = doc_data["facts"]
        if isinstance(facts, list):
            facts = "\n".join(str(f) for f in facts)
        return str(doc_data["name"]), f"{doc_data.get('description') or ''}\n\n{facts}"

    return None
