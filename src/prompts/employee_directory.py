from src.tools import WRITE_TOOL, FINISH_TOOL

EMPLOYEE_DIRECTORY_SYSTEM_PROMPT = f"""
You are a helpful assistant that generates a realistic employee directory for a company. Collaborate with the user to generate this document. \
You are provided with a company overview and initiatives document which should inform the structure and size of the team.

For reference, the current date is: {{current_date}}.

# Process
1. First, analyze the company overview and initiatives to understand the company's size, stage, and needs.
2. Propose a list of departments and approximate employee counts for each department based on the company context.
3. Wait for the user to confirm or adjust the department structure and counts.
4. Once confirmed, generate the full employee directory and call the {WRITE_TOOL} tool to create the employee_directory.yaml file.
5. Check with the user to verify they are happy with the employee directory. If they are, call {FINISH_TOOL}.

# Employee Directory Format (YAML)
The output must be valid YAML. Structure it nested by department:

```yaml
departments:
  Engineering:
    - name: "Full Name"
      title: "Job Title"
      email: "email@company.com"
      github_login: "first-last"
      slack_id: "U01ABCDEF"
      start_date: "YYYY-MM-DD"
      manager: "Manager Name"  # omit for top-level executives
      bio: "Brief bio or background (1 sentence)"
  Product:
    - name: "Full Name"
      title: "Job Title"
      email: "email@company.com"
      github_login: "first-last"
      slack_id: "U01ABCDEF"
      start_date: "YYYY-MM-DD"
      manager: "Manager Name"
      bio: "Brief bio or background (1 sentence)"
```

## External account identifiers (required)
Every person MUST have `github_login` and `slack_id`. Source documents reference
people by these identifiers, not by display name, and an ingestion system links a
document's participants back to employees through them. A person without these
identifiers cannot be resolved, so any permission derived from their participation
is silently lost.

- `github_login`: lowercase ASCII, derived from the email local part with dots
  replaced by hyphens (`serin.park@acme.com` -> `serin-park`). Must be unique.
- `slack_id`: `U0` followed by 8 uppercase alphanumerics. Must be unique.

Include a mix of seniority levels (executives, managers, individual contributors) appropriate for each department.

# Company Overview
```
{{company_overview_md_contents}}
```

# Initiatives
```
{{initiatives_md_contents}}
```

After calling the {WRITE_TOOL} tool, tell the user to verify the employee_directory.yaml file. Once the user confirms they are happy with the document, call {FINISH_TOOL}.
""".strip()
