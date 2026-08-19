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
KOREAN_DATE = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")


def doc_time(doc: dict) -> dt.datetime:
    if doc.get("kind") == "slack_thread":
        return dt.datetime.fromtimestamp(float(doc["messages"][0]["ts"]), dt.UTC)
    return dt.datetime.fromisoformat(doc["issue"]["created_at"].replace("Z", "+00:00"))


def shift_text(text: str, offset: dt.timedelta) -> str:
    """글 안에 적힌 날짜를 같은 폭만큼 민다."""
    def iso(match: re.Match) -> str:
        try:
            moved = dt.date(int(match[1]), int(match[2]), int(match[3])) + offset
        except ValueError:
            return match[0]
        return moved.isoformat()

    def korean(match: re.Match) -> str:
        # 연도가 없다. 밀기 전 날짜가 어느 해였는지는 문서 시각으로만 알 수 있어
        # 며칠 밀리는 정도면 달·일만 다시 쓴다. 해를 넘기면 손대지 않는다
        base = dt.date(2026, int(match[1]), int(match[2]))
        moved = base + offset
        return f"{moved.month}월 {moved.day}일" if moved.year == 2026 else match[0]

    return KOREAN_DATE.sub(korean, ISO_DATE.sub(iso, text))


def shift_doc(doc: dict, offset: dt.timedelta) -> dict:
    if doc.get("kind") == "slack_thread":
        for message in doc["messages"]:
            message["ts"] = f"{float(message['ts']) + offset.total_seconds():.6f}"
            message["text"] = shift_text(message["text"], offset)
        if doc.get("channel_info", {}).get("topic"):
            doc["channel_info"]["topic"] = shift_text(doc["channel_info"]["topic"], offset)
        return doc

    issue = doc["issue"]
    for field in ("created_at", "updated_at", "closed_at"):
        if issue.get(field):
            moved = dt.datetime.fromisoformat(issue[field].replace("Z", "+00:00")) + offset
            issue[field] = moved.strftime("%Y-%m-%dT%H:%M:%SZ")
    for field in ("title", "body"):
        if issue.get(field):
            issue[field] = shift_text(issue[field], offset)
    for bucket in ("comments", "review_comments"):
        for item in doc.get(bucket, []):
            item["body"] = shift_text(item["body"], offset)
    return doc


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
        print("  옮길 것이 없습니다.")
        return

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
            json.dumps(shift_doc(doc, offset), ensure_ascii=False, indent=2),
            encoding="utf-8")

    if not args.dry_run:
        print(f"  {len(move)}건 옮겼습니다 "
              f"({TARGET_FROM:%Y-%m-%d} ~ {TARGET_TO:%Y-%m-%d})")


if __name__ == "__main__":
    main()
