"""일반 RAG — EnterpriseRAG-Bench(arXiv 2605.05253) 의 벡터 기준 시스템을 그대로 옮긴 것.

논문 코드(src/scripts/answer_generation/{index_document_vectors,vector_retrieval}.py)와 같은 점:
- 문서를 512 토큰(cl100k) 조각으로, 10% 겹쳐 자른다
- 질문과 가까운 조각을 100개까지 가져와 문서 단위로 묶고 상위 10개 문서를 고른다
- 그 10개의 **전문**을 같은 형식(`--- Document i (ID: …) ---`)으로 넣고 같은 프롬프트로 답한다

다른 점 — Baton 과 조건을 맞추려고 바꾼 것만:
- 임베딩: OpenAI text-embedding-3-large → **Baton 과 같은 cohere.embed-multilingual-v3**
- 벡터 저장: Qdrant → 메모리 코사인. 수 백 건이라 결과는 같다
- 답변 모델: **Baton 과 같은 DeepSeek**
"""

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
import numpy as np
import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm import Message, get_llm  # noqa: E402
from src.prompts.vector_search_answer_gen import ANSWER_GEN_PROMPT  # noqa: E402

from baton_bench.corpus import Doc  # noqa: E402

EMBEDDING_MODEL = "cohere.embed-multilingual-v3"
CHUNK_TOKENS = 512
TOP_K = 10
CHUNK_LIMIT = 100

_encoding = tiktoken.get_encoding("cl100k_base")


def chunk_text(text: str, chunk_size: int = CHUNK_TOKENS, overlap_frac: float = 0.1) -> list[str]:
    """논문 코드의 chunk_text 그대로."""
    tokens = _encoding.encode(text, disallowed_special=())
    if len(tokens) <= chunk_size:
        return [text]
    overlap = max(1, int(chunk_size * overlap_frac))
    stride = chunk_size - overlap
    chunks = []
    for start in range(0, len(tokens), stride):
        chunks.append(_encoding.decode(tokens[start:start + chunk_size]))
        if start + chunk_size >= len(tokens):
            break
    return chunks


class Embedder:
    def __init__(self, region: str = "us-east-1"):
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def embed(self, texts: list[str], kind: str) -> np.ndarray:
        """kind: search_document | search_query — Baton 도 문서용/질의용을 갈라 쓴다."""
        vectors = []
        for start in range(0, len(texts), 96):  # Cohere 한 번에 96개까지
            body = json.dumps({"texts": texts[start:start + 96], "input_type": kind, "truncate": "END"})
            response = self.client.invoke_model(modelId=EMBEDDING_MODEL, body=body)
            vectors += json.loads(response["body"].read())["embeddings"]
        matrix = np.asarray(vectors, dtype=np.float32)
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


@dataclass
class Retrieved:
    doc_ids: list[str]
    context: str


class VectorRag:
    def __init__(self, docs: list[Doc], cache_dir: Path, fingerprint: str, embedder: Embedder):
        self.docs = {doc.baton_id: doc for doc in docs}
        self.embedder = embedder
        self.chunk_doc: list[str] = []
        texts: list[str] = []
        for doc in docs:
            # 논문 코드처럼 제목과 본문을 이어 붙여 자른다
            for piece in chunk_text(f"{doc.title}\n\n{doc.text}"):
                self.chunk_doc.append(doc.baton_id)
                texts.append(piece)
        cache = cache_dir / f"vectors-{fingerprint}.npy"
        if cache.exists():
            self.vectors = np.load(cache)
        else:
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.vectors = embedder.embed(texts, "search_document")
            np.save(cache, self.vectors)
        assert len(self.vectors) == len(texts), "색인이 말뭉치와 어긋난다 — 캐시를 지운다"

    def retrieve(self, question: str) -> Retrieved:
        query = self.embedder.embed([question], "search_query")[0]
        order = np.argsort(-(self.vectors @ query))[:CHUNK_LIMIT]
        seen: list[str] = []
        for index in order:
            doc_id = self.chunk_doc[index]
            if doc_id not in seen:
                seen.append(doc_id)
            if len(seen) >= TOP_K:
                break
        parts = [f"--- Document {i} (ID: {doc_id}) ---\nTitle: {self.docs[doc_id].title}\n\n{self.docs[doc_id].text}"
                 for i, doc_id in enumerate(seen, 1)]
        return Retrieved(seen, "\n\n".join(parts))

    def answer(self, question: str) -> dict:
        started = time.monotonic()
        retrieved = self.retrieve(question)
        prompt = ANSWER_GEN_PROMPT.format(context_documents=retrieved.context, question=question)
        llm = get_llm(tools=None, quiet=True)
        text = "".join(c for c in llm.generate([Message(role="user", content=prompt)]) if isinstance(c, str))
        return {"text": text.strip(), "doc_ids": retrieved.doc_ids, "prompt": prompt,
                "latency_ms": int((time.monotonic() - started) * 1000)}
