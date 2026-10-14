"""비교 말뭉치 — Baton 에 들어간 문서와 똑같은 묶음을 읽고, 문서마다 Baton 문서 id 를 붙인다.

일반 RAG 와 Baton 이 **같은 글**을 보게 하려고 디스크의 원본을 그대로 읽는다(논문의 벡터 기준
시스템도 디스크에서 읽는다). id 를 붙여야 두 시스템이 같은 문서를 가져왔는지 견줄 수 있다.

- 시드(baton-be/tools/seed/data): 업로드 경로(sourceUrl) 로 짝짓는다
- 심은 문서(new_data/bench/sources): Baton 커넥터 형식이라 원천 id(gh:… / slack:…)로 짝짓는다
"""

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.document_content import extract_document_content  # noqa: E402


@dataclass(frozen=True)
class Doc:
    baton_id: str
    dsid: str  # 논문 채점기가 쓰는 문서 id(dataset_doc_uuid)
    path: str
    origin: str  # seed | bench
    title: str
    text: str


def connector_id(doc: dict) -> str | None:
    """Baton 커넥터가 매기는 원천 id. 깃허브 PR/이슈와 슬랙 스레드만."""
    issue = doc.get("issue")
    if isinstance(issue, dict) and issue.get("html_url"):
        match = re.match(r"https://github\.com/([^/]+)/([^/]+)/(pull|issues)/(\d+)", issue["html_url"])
        if match:
            kind = "pr" if match.group(3) == "pull" else "issue"
            return f"gh:{match.group(1)}/{match.group(2)}:{kind}:{match.group(4)}"
    if doc.get("team_id") and doc.get("channel") and doc.get("messages"):
        return f"slack:{doc['team_id']}:{doc['channel']}:thread:{doc['messages'][0]['ts']}"
    return None


def load(seed_root: Path, bench_root: Path, uploaded: list[dict]) -> list[Doc]:
    """`uploaded` 는 Baton 의 문서 목록(baton_api.Baton.documents)."""
    by_url = {item["sourceUrl"]: item["documentId"] for item in uploaded if item.get("sourceUrl")}
    known_ids = {item["documentId"] for item in uploaded}
    docs, missing = [], []

    for path in sorted(seed_root.glob("sources/**/*.json")):
        rel = str(path.relative_to(seed_root))
        doc = json.loads(path.read_text(encoding="utf-8"))
        baton_id = by_url.get(rel)
        if baton_id is None:
            missing.append(rel)
            continue
        title, text = extract_document_content(doc)
        docs.append(Doc(baton_id, doc["dataset_doc_uuid"], str(path), "seed", title, text))

    for path in sorted(bench_root.glob("**/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        baton_id = connector_id(doc)
        if baton_id is None or baton_id not in known_ids:
            missing.append(str(path))
            continue
        title, text = extract_document_content(doc)
        docs.append(Doc(baton_id, doc["dataset_doc_uuid"], str(path), "bench", title, text))

    if missing:
        # 한쪽에만 있는 문서가 있으면 비교가 공정하지 않다. 조용히 넘기지 않는다
        raise RuntimeError(f"Baton 에 없는 문서 {len(missing)}건: {missing[:5]}")
    return docs


def fingerprint(docs: list[Doc]) -> str:
    """말뭉치 지문. 문서가 하나라도 바뀌면 색인·문항·결과가 따로 갈린다."""
    digest = hashlib.sha256()
    for doc in sorted(docs, key=lambda d: d.baton_id):
        digest.update(doc.baton_id.encode())
        digest.update(hashlib.sha256(doc.text.encode()).digest())
    return digest.hexdigest()[:8]
