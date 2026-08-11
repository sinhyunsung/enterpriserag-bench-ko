from src.paths import AGENTS_MD_FILE
from src.tools import WRITE_TOOL, FINISH_TOOL

REPOSITORY_FACTS_PROMPT = f"""
You are establishing the ground truth for one source-code repository at the company
described below. Everything generated later — pull requests, issues, review threads —
must be consistent with what you write here, so this is a **fact sheet**, not a story.

Write it as if you were the engineer who has worked in this repository for two years
and is briefing a newcomer: concrete, specific, and boring in the way real internal
documents are boring.

# Repository
Name: {{repo_name}}
Directory: {{repo_dir}}
What we already know about it: {{repo_hint}}

# Company Overview
```
{{company_overview_md_contents}}
```

# Initiatives
```
{{initiatives_md_contents}}
```

# Employee Directory
```
{{employee_directory_yaml_contents}}
```

# What to produce

Call the {WRITE_TOOL} tool **twice**, writing exactly these two files:

## 1. `{{repo_dir}}/_repository.json`

Machine-readable facts. An ingestion system reads `private` and `collaborators` to
decide who is allowed to read every document in this repository, so these two are not
decoration — they are the answer key for permission-aware retrieval.

```json
{{{{
  "name": "{{repo_name}}",
  "description": "one line, Korean",
  "private": true,
  "collaborators": [{{{{"login": "serin-park"}}}}, {{{{"login": "junho-lee"}}}}],
  "facts": {{{{
    "owning_department": "개발팀",
    "primary_language": "Java",
    "frameworks": ["Spring Boot", "JPA"],
    "active_period": {{{{"from": "2025-01", "to": "2026-08"}}}},
    "modules": [
      {{{{"path": "src/main/java/com/duretech/order", "purpose": "주문 도메인"}}}}
    ],
    "common_file_paths": [
      "src/main/java/com/duretech/order/OrderService.java"
    ],
    "conventions": {{{{
      "branch": "feat/ORD-123-short-slug",
      "commit": "feat: 한 줄 요약 (ORD-123)",
      "ci_checks": ["build", "test", "lint"]
    }}}},
    "cast": [
      {{{{"login": "serin-park", "role": "리드. 설계 리뷰를 대부분 맡는다"}}}},
      {{{{"login": "junho-lee", "role": "주문 도메인 주 작성자"}}}}
    ]
  }}}}
}}}}
```

Rules that make this usable rather than decorative:

- Every `login` MUST be a `github_login` that exists in the employee directory.
- `collaborators` MUST be the people who actually work on this repository — the
  owning department, plus the specific individuals from other departments who
  genuinely need it. Not the whole company, and not an arbitrary subset.
- `cast` is a subset of `collaborators`. These are the people who will appear as
  authors and reviewers. Give each a one-line role so later documents put the right
  person in the right place — a lead reviews design, a newcomer asks questions, an
  SRE appears only on deploy-related changes.
- `common_file_paths` MUST match `primary_language` and the module layout. These are
  the paths that will show up in review comments, so list the files that would
  realistically be touched often (10-20 of them).
- `active_period` MUST sit inside the company timeline from the overview and
  initiatives.

## 2. `{{repo_dir}}/{AGENTS_MD_FILE}`

The same facts written as generation rules, in Korean, following the existing
{AGENTS_MD_FILE} shape (Directory / Target number of files / File name format /
Content rules / Metadata rules).

Content rules must state, in plain sentences:
- what this repository is for and which team owns it,
- which people appear and in what role,
- which file paths and technologies show up in review comments,
- the period the work falls in,
- who can read it (`private` + the collaborator list), and that authors and
  assignees must be collaborators.

# Grounding rules

**Do not invent people.** Every person comes from the employee directory.

**Do not invent a technology stack that contradicts the company overview.** If the
company runs a Java backend, this repository does not review `.js` files unless it is
explicitly the frontend repository.

**Repositories must differ from each other.** Across the whole `sources/github`
directory there must be at least one repository open to the entire company
(`private: false`) and at least one restricted to a single small team. A corpus where
everyone can read everything cannot demonstrate permission-aware retrieval; a corpus
where the restriction is arbitrary cannot be scored.

**Prefer the specific over the plausible.** "정산 배치가 매일 02:00 KST 에 돈다" is
useful. "안정적인 배치 처리를 수행한다" is not — it constrains nothing, and every
document generated from it will be equally vague.

When both files are written, call {FINISH_TOOL}.
""".strip()
