"""노후로 잡히는 문서를 심은 두 건만 남기고 날짜를 민다.

    python -m new_data.shift_dates --root new_data/sources --dry-run
    python -m new_data.shift_dates --root new_data/sources

**왜.** 린트의 노후 문턱은 근거 시점 기준 180일이다. 100건을 열 달에 걸쳐 뿌려 놨더니
절반이 문턱 밖으로 나가 노후가 176건 잡혔다. 심은 두 건이 그 안에 파묻힌다.

**무엇을.** 문턱 밖에 있는 문서 중 심은 노후 둘(`stale-*`)만 그 자리에 두고, 나머지를
문턱 안으로 옮긴다.

**어떻게.** 시간 순서를 지킨 채 구간만 압축한다 — 가장 이른 문서가 목표 구간 앞쪽,
가장 늦은 문서가 뒤쪽으로 간다. 옛 문서가 갑자기 제일 최근이 되면 업무의 흐름이 뒤집힌다.

**문서 하나는 통째로 같은 폭만큼 민다.** 스레드 시각만 옮기고 본문에 적힌 날짜를 두면
「2026-01-05 에 정했다」는 글이 4월에 쓰인 것이 된다. 슬랙 ts · 깃허브 created_at ·
본문의 ISO 날짜와 한글 날짜를 한 폭으로 민다.

「이번 주」 같은 상대 표현은 안 건드린다 — 밀어도 뜻이 안 변한다.
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

# 린트가 보는 기준. LintRuleChain.STALENESS_THRESHOLD 와 같아야 한다
STALENESS_DAYS = 180
BASIS = dt.datetime(2026, 8, 19, tzinfo=dt.UTC)
THRESHOLD = BASIS - dt.timedelta(days=STALENESS_DAYS)

# 옮긴 문서를 놓을 구간. 문턱에서 열흘 띄워 여유를 둔다 — 기준일이 며칠 지나도 안 넘어간다
TARGET_FROM = THRESHOLD + dt.timedelta(days=10)
TARGET_TO = BASIS - dt.timedelta(days=9)

KEEP = re.compile(r"^stale-")           # 심은 노후는 옛날이어야 한다

ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
YEAR_MONTH = re.compile(r"(\d{4})년\s*(\d{1,2})월")
MONTH_DAY = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
# 「10/6~10/12」 처럼 범위로 적힌 것만 날짜로 본다. 단독 「10/16」 은 손대지 않는다 —
# 이 데이터의 슬래시 숫자는 거의 다 CIDR 대역(10.30.0.0/16)이고 SEV-1/2 나 최소/최대 2/6 도 있다.
# 하나라도 밀면 네트워크 구성이 조용히 망가진다
SLASH_RANGE = re.compile(r"(\d{1,2})/(\d{1,2})\s*~\s*(\d{1,2})/(\d{1,2})")
# 「작년 3월」 처럼 해를 말로 적은 것. 추출기가 이것도 시점으로 읽어 근거 시점을 만든다
LAST_YEAR = re.compile(r"(작년|재작년|지난해)(\s*\d{1,2}월)?")


def doc_time(doc: dict) -> dt.datetime:
    if doc.get("kind") == "slack_thread":
        return dt.datetime.fromtimestamp(float(doc["messages"][0]["ts"]), dt.UTC)
    return dt.datetime.fromisoformat(doc["issue"]["created_at"].replace("Z", "+00:00"))


def shift_text(text: str, offset: dt.timedelta, year: int) -> str:
    """글 안에 적힌 날짜를 같은 폭만큼 민다.

    `year` 는 밀기 전 문서가 쓰인 해다. 「10/6」 이나 「11월 24일」 처럼 연도가 없는 표기는
    그 해의 날짜로 읽어야 몇 월로 밀지 정할 수 있다.
    """
    def iso(match: re.Match) -> str:
        try:
            return (dt.date(int(match[1]), int(match[2]), int(match[3])) + offset).isoformat()
        except ValueError:
            return match[0]

    def year_month(match: re.Match) -> str:
        # 달만 적힌 것은 그 달 1일로 읽고 민다
        moved = dt.date(int(match[1]), int(match[2]), 1) + offset
        return f"{moved.year}년 {moved.month}월"

    def month_day(match: re.Match) -> str:
        try:
            moved = dt.date(year, int(match[1]), int(match[2])) + offset
        except ValueError:
            return match[0]
        return f"{moved.month}월 {moved.day}일"

    def slash_range(match: re.Match) -> str:
        try:
            start = dt.date(year, int(match[1]), int(match[2])) + offset
            end = dt.date(year, int(match[3]), int(match[4])) + offset
        except ValueError:
            return match[0]
        # 「10/27~11/2」 처럼 해를 넘는 범위는 끝이 앞서 보이므로 한 해를 더한다
        if end < start:
            end = dt.date(year + 1, int(match[3]), int(match[4])) + offset
        return f"{start.month}/{start.day}~{end.month}/{end.day}"

    text = ISO_DATE.sub(iso, text)
    text = YEAR_MONTH.sub(year_month, text)
    text = MONTH_DAY.sub(month_day, text)
    return SLASH_RANGE.sub(slash_range, text)


def shift_doc(doc: dict, offset: dt.timedelta, year: int) -> dict:
    if doc.get("kind") == "slack_thread":
        for message in doc["messages"]:
            message["ts"] = f"{float(message['ts']) + offset.total_seconds():.6f}"
            message["text"] = shift_text(message["text"], offset, year)
        if doc.get("channel_info", {}).get("topic"):
            doc["channel_info"]["topic"] = shift_text(doc["channel_info"]["topic"], offset, year)
        return doc

    issue = doc["issue"]
    for field in ("created_at", "updated_at", "closed_at"):
        if issue.get(field):
            moved = dt.datetime.fromisoformat(issue[field].replace("Z", "+00:00")) + offset
            issue[field] = moved.strftime("%Y-%m-%dT%H:%M:%SZ")
    for field in ("title", "body"):
        if issue.get(field):
            issue[field] = shift_text(issue[field], offset, year)
    for bucket in ("comments", "review_comments"):
        for item in doc.get(bucket, []):
            item["body"] = shift_text(item["body"], offset, year)
    return doc


def pull_forward(doc: dict, at: dt.datetime) -> tuple[dict, int]:
    """문서 시각은 문턱 안인데 본문이 문턱 밖을 가리키는 경우를 당긴다.

    「버킷에 2025년 2월 것부터 있네요」 같은 회고 문장이다. 문서 자체는 최근인데 추출기가
    그 옛 시점을 근거 시점으로 뽑아 노후로 잡힌다.

    문서를 통째로 밀 수는 없다 — 시각은 이미 제자리다. 본문에 적힌 날짜 중 문턱보다 이른
    것만 문턱 안으로 당기고, <b>문서 시각과의 앞뒤 관계는 지킨다</b>. 회고가 미래를 가리키면
    글이 말이 안 된다.
    """
    limit = min(THRESHOLD + dt.timedelta(days=10), at - dt.timedelta(days=1))
    moved = 0

    def fix_iso(match: re.Match) -> str:
        nonlocal moved
        try:
            date = dt.date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return match[0]
        if date >= limit.date():
            return match[0]
        moved += 1
        return limit.date().isoformat()

    def fix_year_month(match: re.Match) -> str:
        nonlocal moved
        date = dt.date(int(match[1]), int(match[2]), 1)
        if date >= limit.date():
            return match[0]
        moved += 1
        return f"{limit.year}년 {limit.month}월"

    def fix_last_year(match: re.Match) -> str:
        # 문서가 최근인데 「작년 3월」 이라 적으면 그 시점이 문턱 밖이다. 달은 그대로 두고
        # 해만 올해로 당긴다 — 「올해 3월」 은 문서 시각보다 앞이라 회고로 읽힌다
        nonlocal moved
        month = (match[2] or "").strip()
        if month:
            try:
                spoken = dt.date(at.year - (1 if match[1] == "작년" else 2),
                                 int(month.rstrip("월")), 1)
            except ValueError:
                return match[0]
            if spoken >= limit.date():
                return match[0]
            moved += 1
            return f"올해 {month}"
        moved += 1
        return "올해"

    def walk(text: str) -> str:
        text = ISO_DATE.sub(fix_iso, text)
        text = YEAR_MONTH.sub(fix_year_month, text)
        return LAST_YEAR.sub(fix_last_year, text)

    if doc.get("kind") == "slack_thread":
        for message in doc["messages"]:
            message["text"] = walk(message["text"])
    else:
        issue = doc["issue"]
        for field in ("title", "body"):
            if issue.get(field):
                issue[field] = walk(issue[field])
        for bucket in ("comments", "review_comments"):
            for item in doc.get(bucket, []):
                item["body"] = walk(item["body"])
    return doc, moved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="new_data/sources")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)

    docs = []
    for path in sorted(root.rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs.append((path, doc, doc_time(doc)))

    stale_out = [row for row in docs if row[2] < THRESHOLD]
    keep = [row for row in stale_out if KEEP.match(row[0].name)]
    move = sorted((row for row in stale_out if not KEEP.match(row[0].name)),
                  key=lambda row: row[2])

    print(f"  문턱 {THRESHOLD:%Y-%m-%d} (기준일 {BASIS:%Y-%m-%d} · {STALENESS_DAYS}일)")
    print(f"  문턱 밖 {len(stale_out)}건 → 그대로 둘 것 {len(keep)}건 · 옮길 것 {len(move)}건")
    for path, _, at in keep:
        print(f"    그대로  {path.name:24} {at:%Y-%m-%d}")

    if not move:
        print("  옮길 것이 없습니다. 본문의 옛 시점만 봅니다.")

    span = (TARGET_TO - TARGET_FROM).total_seconds()
    for index, (path, doc, at) in enumerate(move):
        # 순서를 지킨 채 구간에만 고르게 놓는다. 하루 안에 몰리지 않게 칸을 나눠 쓴다
        target = TARGET_FROM + dt.timedelta(
            seconds=span * (index + 0.5) / len(move))
        offset = target - at
        if args.dry_run:
            print(f"    옮김    {path.name:24} {at:%Y-%m-%d} → {target:%Y-%m-%d}"
                  f"  ({offset.days:+}일)")
            continue
        path.write_text(
            json.dumps(shift_doc(doc, offset, at.year), ensure_ascii=False, indent=2),
            encoding="utf-8")

    if not args.dry_run:
        print(f"  {len(move)}건 옮겼습니다 "
              f"({TARGET_FROM:%Y-%m-%d} ~ {TARGET_TO:%Y-%m-%d})")

    # 옮기고 난 뒤 다시 읽는다. 방금 민 문서도 본문에 옛 시점이 남아 있을 수 있다
    pulled = 0
    for path in sorted(root.rglob("*.json")):
        if KEEP.match(path.name):
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        fixed, moved = pull_forward(doc, doc_time(doc))
        if not moved:
            continue
        pulled += moved
        if args.dry_run:
            print(f"    당김    {path.name:24} 옛 시점 {moved}곳")
            continue
        path.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
    if pulled:
        print(f"  본문의 옛 시점 {pulled}곳을 문턱 안으로 당겼습니다")


if __name__ == "__main__":
    main()
