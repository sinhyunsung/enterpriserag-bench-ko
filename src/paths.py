import os

"""Shared file paths for the data generation flow."""

AGENTS_MD_FILE = "agents.md"

GENERATED_DATA_DIR = "generated_data"
GENERATION_CACHE_DIR = "generation_cache"

COMPANY_OVERVIEW_PATH = f"{GENERATED_DATA_DIR}/company_overview.md"
INITIATIVES_PATH = f"{GENERATED_DATA_DIR}/initiatives.md"
EMPLOYEE_DIRECTORY_PATH = f"{GENERATED_DATA_DIR}/employee_directory.yaml"
VISUAL_EMPLOYEE_DIRECTORY_PATH = f"{GENERATED_DATA_DIR}/visual_employee_directory.txt"
# 다른 말뭉치(예: Baton 시드)에서 질문을 만들 때 SOURCES_DIR 로 덮는다
SOURCES_DIR = os.environ.get("SOURCES_DIR", f"{GENERATED_DATA_DIR}/sources")
SOURCE_TREE_PATH = f"{GENERATED_DATA_DIR}/source_tree.txt"

PROJECTS_DIR = f"{GENERATION_CACHE_DIR}/projects"
PROJECT_LIST_PATH = f"{GENERATED_DATA_DIR}/project_list.txt"

DEBUG_DIR = f"{GENERATED_DATA_DIR}/debug"
AGGREGATE_STATISTICS_PATH = f"{GENERATED_DATA_DIR}/aggregate_statistics.json"
COMPLETENESS_DIR = f"{GENERATED_DATA_DIR}/completeness"
VOLUME_DIR = f"{GENERATION_CACHE_DIR}/volume"
UUID_INDEX_PATH = f"{GENERATED_DATA_DIR}/uuid_index.json"
# 한국어 말뭉치용 질문은 따로 둔다 — 기본 파일에는 원본 영어 질문 500개가 있어서, 섞이면
# 이미 채워진 것으로 세어 새로 안 만든다. QUESTIONS_PATH=questions_ko.jsonl 로 덮는다
QUESTIONS_PATH = os.environ.get("QUESTIONS_PATH", "questions.jsonl")
EXTRA_QUESTIONS_PATH = "extra_questions.jsonl"

EXPORT_DATA_DIR = "export_data"
