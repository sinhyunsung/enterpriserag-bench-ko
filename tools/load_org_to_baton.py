"""조직도 YAML 을 Baton 에 넣는다.

**왜 필요한가.** 클레임의 담당자가 사원과 이어져야 업무 이력과 인수인계가 돈다.
지금은 조직도가 안 들어와 있어서 28,009건 중 0건이 이어져 있다.

넣는 것은 둘이다.

    부서·사원   기존 관리자 API 로 (POST /api/org/departments · /api/org/employees)
    외부 계정   깃허브 로그인을 사원에 잇는 통로. 아직 API 가 없어 DB 로 직접 넣는다

**같은 걸 두 번 넣지 않는다.** 이미 있는 이름·이메일은 건너뛴다. 여러 번 돌려도 같다.

사용법:

    python tools/load_org_to_baton.py --base http://localhost:2350 \\
        --email admin@baton.local --password baton-local-admin --org org-baton
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests
import yaml


def login(session: requests.Session, base: str, email: str, password: str) -> None:
    response = session.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    if response.json().get("code") != "OK":
        raise SystemExit(f"로그인 실패: {response.text[:200]}")


def csrf(session: requests.Session) -> dict:
    """쓰기 요청은 XSRF 쿠키를 헤더로 되돌려줘야 통과한다."""
    token = session.cookies.get("XSRF-TOKEN")
    return {"X-XSRF-TOKEN": token} if token else {}


def envelope(response: requests.Response):
    """{code, message, data} 봉투를 벗긴다. 실패면 이유를 그대로 보여준다."""
    response.raise_for_status()
    body = response.json()
    if body.get("code") != "OK":
        raise SystemExit(f"{response.url} → {body.get('code')} {body.get('message')}")
    return body.get("data")


def load_departments(session, base, org, names):
    """부서를 이름으로 맞추고 없으면 만든다. 계층은 안 만든다 — YAML 에 부모가 없다."""
    existing = {}
    tree = envelope(session.get(f"{base}/api/org/departments/tree", params={"orgId": org}))

    def walk(nodes):
        for node in nodes or []:
            existing[node["name"]] = node["id"]
            walk(node.get("children"))

    walk(tree if isinstance(tree, list) else tree.get("departments"))

    made = 0
    for name in names:
        if name in existing:
            continue
        data = envelope(session.post(
            f"{base}/api/org/departments",
            params={"orgId": org},
            headers={**csrf(session), "Content-Type": "application/json"},
            json={"name": name, "parentId": None},
            timeout=30,
        ))
        existing[name] = data["id"] if isinstance(data, dict) else data
        made += 1
    return existing, made


def load_employees(session, base, org, departments, people):
    """이메일을 열쇠로 쓴다. 이름은 겹치므로 열쇠가 못 된다."""
    seen = {}
    page = 0
    while True:
        data = envelope(session.get(
            f"{base}/api/org/employees", params={"orgId": org, "page": page, "size": 100}))
        for row in data.get("content", []):
            seen[row.get("companyEmail")] = row.get("id")
        if page + 1 >= data.get("totalPages", 1):
            break
        page += 1

    made, skipped = 0, 0
    issued: list[tuple[str, str, str | None]] = []
    for person, department in people:
        if person["email"] in seen:
            skipped += 1
            continue
        data = envelope(session.post(
            f"{base}/api/org/employees",
            params={"orgId": org},
            headers={**csrf(session), "Content-Type": "application/json"},
            json={
                "name": person["name"],
                "companyEmail": person["email"],
                "departmentId": departments[department],
                # 조직도에 권한 개념이 없다. 전부 MEMBER 로 넣고 권한은 따로 준다
                "grade": "MEMBER",
                # WDO-489 로 늘어난 칸들. 인수자 후보를 줄 세울 때와
                # 「입사 전 문서에 담당자로 잡힌 것」을 가릴 때 쓴다
                "position": person.get("title"),
                "hireDate": person.get("start_date"),
            },
            timeout=30,
        ))
        # 사원 생성은 employeeId 로 준다 (부서는 id). 이름이 달라 한 번 밟았다
        seen[person["email"]] = data["employeeId"]
        # 임시 비밀번호는 이 응답에만 실려 온다. 서버에 다시 물을 수 없고 관리자가
        # 초기화하는 경로도 없어서, 여기서 안 받아 두면 그 사람으로는 영영 못 들어간다
        issued.append((person["name"], person["email"], data.get("tempPassword")))
        made += 1
    return seen, made, skipped, issued


def link_managers(session, base, org, people, emails):
    """상사를 잇는다.

    조직도에 상사가 **이름**으로 적혀 있어서 사원을 다 만든 뒤에야 사원 번호를 안다.
    그래서 두 번에 나눠 넣는다 — 만들 때 한 번, 다 만든 뒤 이어 주는 데 한 번.

    이름이 겹치는 조직이면 이 방법이 틀린다. 지금 조직도에는 풀네임 중복이 없어서
    쓰지만, 겹치기 시작하면 조직도에 상사의 이메일을 적는 쪽으로 바꿔야 한다.
    """
    by_name = {}
    for person, _ in people:
        by_name.setdefault(person["name"], []).append(emails.get(person["email"]))

    linked, skipped = 0, []
    for person, _ in people:
        manager = person.get("manager")
        if not manager:
            continue
        candidates = [i for i in by_name.get(manager, []) if i]
        if len(candidates) != 1:
            # 못 찾거나 여럿이면 안 잇는다. 틀리게 잇는 것보다 안 잇는 게 낫다
            skipped.append(f"{person['name']}→{manager}")
            continue
        envelope(session.patch(
            f"{base}/api/org/employees/{emails[person['email']]}",
            params={"orgId": org},
            headers={**csrf(session), "Content-Type": "application/json"},
            json={"managerId": candidates[0]},
            timeout=30,
        ))
        linked += 1
    return linked, skipped


def link_github(container, org, people, emails):
    """깃허브 로그인을 사원에 잇는다.

    org 모듈에 이 통로를 만드는 API 가 아직 없어서 표에 직접 넣는다. 데이터만 넣는 것이고
    코드는 안 건드린다 — API 가 생기면 이 함수만 갈아 끼우면 된다.
    """
    rows = []
    for person, _ in people:
        login_name = person.get("github_login")
        employee_id = emails.get(person["email"])
        if login_name and employee_id:
            rows.append((employee_id, login_name))

    values = ",".join(
        f"({employee_id}, 'GITHUB', 'SOURCE', '{login_name}', now(), now(), now())"
        for employee_id, login_name in rows
    )
    sql = f"""
        INSERT INTO org.external_identity
            (employee_id, provider, purpose, external_id, connected_at, created_at, updated_at)
        VALUES {values}
        ON CONFLICT DO NOTHING;
    """
    subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "baton", "-d", "baton", "-q", "-c", sql],
        check=True,
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="조직도 YAML → Baton")
    parser.add_argument("--yaml", default="generated_data/employee_directory.yaml")
    parser.add_argument("--base", default="http://localhost:2350")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--org", default="org-baton")
    parser.add_argument("--pg-container", default="shs-postgres")
    parser.add_argument("--credentials", default="org-credentials.tsv",
                        help="새로 만든 사원의 임시 비밀번호를 적을 곳")
    args = parser.parse_args()

    directory = yaml.safe_load(Path(args.yaml).read_text(encoding="utf-8"))["departments"]
    people = [(person, department)
              for department, members in directory.items()
              for person in members]

    session = requests.Session()
    login(session, args.base, args.email, args.password)

    departments, made_departments = load_departments(
        session, args.base, args.org, list(directory))
    print(f"  부서 {len(departments)}개 (새로 {made_departments})")

    emails, made, skipped, issued = load_employees(
        session, args.base, args.org, departments, people)
    print(f"  사원 {len(people)}명 중 새로 {made} · 이미 있어 건너뜀 {skipped}")

    if issued:
        credentials = Path(args.credentials)
        credentials.write_text(
            "\n".join(f"{name}\t{email}\t{password}" for name, email, password in issued)
            + "\n",
            encoding="utf-8",
        )
        credentials.chmod(0o600)
        print(f"  임시 비밀번호 {len(issued)}개를 {credentials} 에 적었음 — 다시 못 받는 값이다")

    linked, skipped = link_managers(session, args.base, args.org, people, emails)
    print(f"  상사 {linked}명 이음" + (f" · 못 이은 것 {len(skipped)}건 {skipped}" if skipped else ""))

    github = link_github(args.pg_container, args.org, people, emails)
    print(f"  깃허브 로그인 {github}개 이음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
