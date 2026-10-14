"""Baton 의 HTTP API. 벤치마크는 Baton 내부(DB · 코드)에 기대지 않고 사용자와 같은 문으로 들어간다."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class Answer:
    text: str
    evidences: list[dict] = field(default_factory=list)
    search_text: str | None = None
    ask_back: bool = False
    no_evidence: bool = False
    latency_ms: int = 0
    first_token_ms: int | None = None
    error: str | None = None


class Baton:
    def __init__(self, base: str, email: str, password: str, org: str):
        self.org = org
        self.http = httpx.Client(base_url=base, timeout=180)
        self.http.post("/api/auth/login", json={"email": email, "password": password}).raise_for_status()
        # 쓰기 요청은 CSRF 토큰이 필요하다. 로그인 뒤 한 번 읽으면 쿠키로 내려온다
        self.http.get("/api/auth/me")

    def documents(self) -> list[dict]:
        """조직의 문서 전부. 업로드 경로(sourceUrl)와 Baton 문서 id 가 같이 온다."""
        items, cursor = [], None
        while True:
            params = {"orgId": self.org, "scope": "org", "size": 100}
            if cursor:
                params["cursor"] = cursor
            data = self.http.get("/api/ingest/documents", params=params).raise_for_status().json()["data"]
            items += data["items"]
            cursor = data.get("nextCursor")
            if not cursor:
                return items

    def upload(self, files: list[Path], batch: int = 20) -> list[dict]:
        """파일 이름이 저장 경로(sourceUrl)가 된다.

        나눠 보낸다. 한 요청에 파트가 많으면 Tomcat 의 파트 수 상한에 걸려 500 이 난다(80건에서 겪음).
        """
        xsrf = self.http.cookies.get("XSRF-TOKEN") or ""
        stored = []
        for start in range(0, len(files), batch):
            payload = [("files", (path.name, path.read_bytes(), "application/json"))
                       for path in files[start:start + batch]]
            response = self.http.post("/api/ingest/documents", params={"orgId": self.org},
                                      files=payload, headers={"X-XSRF-TOKEN": xsrf})
            stored += response.raise_for_status().json()["data"]
        return stored

    def ask(self, question: str) -> Answer:
        """새 대화로 한 번 묻는다. 대화 이력이 섞이면 문항끼리 영향을 준다."""
        started = time.monotonic()
        tokens, answer, event = [], Answer(text=""), None
        try:
            with self.http.stream("GET", "/api/chat/answer",
                                  params={"orgId": self.org, "question": question}) as stream:
                for line in stream.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:]
                    if event == "token":
                        if answer.first_token_ms is None:
                            answer.first_token_ms = int((time.monotonic() - started) * 1000)
                        tokens.append(_token_text(data))
                    elif event == "evidences":
                        body = json.loads(data)
                        answer.evidences = body.get("evidences", [])
                        answer.search_text = body.get("searchText")
                        answer.ask_back = bool(body.get("askBack"))
                    elif event == "no_evidence":
                        answer.no_evidence = True
                    elif event == "error":
                        answer.error = data
        except httpx.HTTPError as error:
            answer.error = repr(error)
        answer.text = "".join(tokens)
        answer.latency_ms = int((time.monotonic() - started) * 1000)
        return answer


def _token_text(data: str) -> str:
    # 토큰은 JSON({"text": …})으로 오기도 하고 글자 그대로 오기도 한다
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        return data
    return body.get("text", "") if isinstance(body, dict) else str(body)
