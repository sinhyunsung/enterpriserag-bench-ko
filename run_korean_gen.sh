#!/usr/bin/env bash
# 한국어 사내 문서 생성 (기본 딥시크 Pro)
# 사전 조건: .env 에 DEEPSEEK_API_KEY (베드락으로 돌리려면 LLM_PROVIDER=bedrock)
set -euo pipefail
cd "$(dirname "$0")"

export LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"

yes | .venv/bin/python -m src.scripts.data_gen_stage_1_generate_clean_data.step_9_generate_volume_documents \
    --source-parallelism 4 \
    --topic-parallelism 4 \
    --doc-parallelism 10 \
    --doc-limit 1000
