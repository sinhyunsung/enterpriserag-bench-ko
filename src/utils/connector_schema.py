"""Baton 수집 커넥터가 읽는 export shape 검사.

**어디에 쓰나.** `WriteTool` 의 `validator` 훅에 물린다. 모델이 규격을 어긴 JSON 을 쓰려 하면
오류 문구가 그대로 모델에게 돌아가고, 모델이 **같은 대화 안에서 고쳐 쓴다.**
파일을 지우고 처음부터 다시 시키는 것보다 싸고 성공률이 높다.

**기준은 커넥터 코드다.** 문서 규격이 아니라 실제로 파싱하는 필드를 본다.

  깃허브  GitHubWrapperDetector · RawIssueParser
  슬랙    SlackWrapperDetector · RawSlackThreadParser
"""
import json
import re

_USER_ID = re.compile(r"^U[A-Z0-9]{6,12}$")
_CHANNEL_ID = re.compile(r"^C[A-Z0-9]{6,12}$")
_TS = re.compile(r"^\d{10}\.\d{6}$")


def _github(doc: dict) -> str | None:
    if doc.get("kind") not in ("issue", "pull_request"):
        return 'kind 는 "pull_request" 또는 "issue" 여야 합니다.'

    issue = doc.get("issue")
    if not isinstance(issue, dict):
        return (
            "issue 는 중첩 객체여야 합니다. issue_number·issue_title 처럼 평평하게 펴거나 "
            "문자열로 감싸면 안 됩니다."
        )
    for field in ("number", "title", "body", "state", "html_url"):
        if not issue.get(field):
            return f"issue.{field} 가 비었습니다."
    user = issue.get("user")
    if not isinstance(user, dict) or not user.get("login"):
        return 'issue.user 는 {"login": "..."} 형태여야 합니다. 문자열이면 안 됩니다.'
    for field in ("assignees", "requested_reviewers"):
        value = issue.get(field)
        if not isinstance(value, list):
            return f'issue.{field} 는 [{{"login": "..."}}] 배열이어야 합니다.'
        for item in value:
            if not isinstance(item, dict) or not item.get("login"):
                return f'issue.{field} 의 각 원소는 {{"login": "..."}} 여야 합니다.'

    for field in ("comments", "review_comments"):
        items = doc.get(field)
        if not isinstance(items, list):
            return f"{field} 는 배열이어야 합니다 (없으면 빈 배열)."
        for item in items:
            if not isinstance(item, dict):
                return f"{field} 의 각 원소는 객체여야 합니다."
            author = item.get("user")
            if not isinstance(author, dict) or not author.get("login"):
                return f'{field}[].user 는 {{"login": "..."}} 여야 합니다.'
            if not item.get("body"):
                return f"{field}[].body 가 비었습니다."
    return None


def _slack(doc: dict) -> str | None:
    if doc.get("kind") != "slack_thread":
        return 'kind 는 "slack_thread" 여야 합니다.'
    if doc.get("team_id") != "T0DURETECH":
        return 'team_id 는 "T0DURETECH" 여야 합니다.'

    channel = doc.get("channel")
    if not isinstance(channel, str) or not _CHANNEL_ID.match(channel):
        return 'channel 은 채널 ID 여야 합니다 (예: "C7E39E06F"). 채널명이 아닙니다.'

    info = doc.get("channel_info")
    if not isinstance(info, dict) or not info.get("name"):
        return 'channel_info 는 {"id": "C...", "name": "개발"} 형태여야 합니다.'
    if info.get("id") != channel:
        return "channel_info.id 는 channel 과 같아야 합니다."

    messages = doc.get("messages")
    if not isinstance(messages, list) or not messages:
        return "messages 는 비어 있으면 안 됩니다. 스레드의 모든 메시지를 넣으세요."

    previous = None
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            return "messages 의 각 원소는 객체여야 합니다."
        user = message.get("user")
        if not isinstance(user, str) or not _USER_ID.match(user):
            return f'messages[{index}].user 는 슬랙 user ID 여야 합니다 (예: "U8C86A147").'
        if not message.get("user_name"):
            return f"messages[{index}].user_name 이 비었습니다 (보낸 사람의 실제 이름)."
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return f"messages[{index}].text 가 비었습니다."
        if re.match(r"^\s*" + re.escape(str(message.get("user_name"))) + r"\s*:", text):
            return (
                f"messages[{index}].text 앞에 이름을 다시 적지 마세요. "
                "보낸 사람은 user_name 이 말해 줍니다."
            )
        ts = message.get("ts")
        if not isinstance(ts, str) or not _TS.match(ts):
            return f'messages[{index}].ts 는 "1740535447.000200" 형식이어야 합니다.'
        if previous is not None and float(ts) < previous:
            return "messages 의 ts 는 오름차순이어야 합니다."
        previous = float(ts)
    return None


def make_validator(source_type: str):
    """WriteTool 에 물릴 검증 함수. 통과면 None, 아니면 고칠 점을 문장으로 돌려준다."""

    def validate(content: str) -> str | None:
        try:
            doc = json.loads(content)
        except json.JSONDecodeError as error:
            return f"JSON 파싱이 안 됩니다: {error}"
        if not isinstance(doc, dict):
            return "최상위는 객체여야 합니다."
        if source_type == "github":
            return _github(doc)
        if source_type == "slack":
            return _slack(doc)
        return None

    return validate
