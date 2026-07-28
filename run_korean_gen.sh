#!/usr/bin/env bash
# 한국어 사내 문서 100건 생성 (Bedrock)
# 사전 조건: .env 작성 (cp .env.example .env) 또는 AWS 자격증명 환경변수 설정
set -euo pipefail
cd "$(dirname "$0")"

export LLM_PROVIDER="${LLM_PROVIDER:-bedrock}"

yes | .venv/bin/python -m src.scripts.data_gen_stage_1_generate_clean_data.step_9_generate_volume_documents \
    --source-parallelism 4 \
    --topic-parallelism 4 \
    --doc-parallelism 10 \
    --doc-limit 1000
