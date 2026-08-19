"""이미 만든 문서에 「주어 · 속성 = 값」 한 줄을 박는다.

    python -m new_data.fix_planted --root new_data/sources
    python -m new_data.fix_planted --root new_data/sources --dry-run

**왜 필요한가.** 같은 문장을 두 문서에 넣었는데 추출기가 주어·속성을 다르게 잘랐다.

    80억   subject=바로배송 인프라 증설             predicate=약정 계약 규모
    100억  subject=바로배송 인프라 증설 약정 계약 규모  predicate=규모

린트는 **주어·속성이 같아야** 짝으로 본다. 그래서 심어 둔 모순이 한 건도 안 잡혔다.
문장을 글자 그대로 넣는 것만으로는 모자랐다 — <b>같은 문장이라도 앞뒤 맥락이 다르면
다르게 잘린다.</b>

**무엇을 하나.** 심은 문장 바로 뒤에 자를 자리를 정해 주는 한 줄을 붙인다. Baton 이
클레임을 그 모양으로 그리므로 추출기가 같은 자리에서 자를 확률이 높다.

**잡음도 지운다.** 모델이 지어낸 다른 숫자가 같은 주어·속성으로 또 하나의 클레임이 되어,
어느 것이 심은 모순인지 흐린다.

다시 뽑지 않고 고치는 이유는 그게 빠르고, 이미 만든 글의 결을 안 버려도 되기 때문이다.
"""
import argparse
import json
import re
from pathlib import Path

from new_data import scenario

# 모델이 지어내 같은 자리를 차지하는 값들. (파일 조각, 지울 정규식, 바꿔 넣을 말)
NOISE = [
    ("contradiction-1-2",
     r"그거 위키에 73억으로 되어 있던 것 같은데 메일엔 80억이네요\.",
     "위키에 적힌 값이랑 메일이 다르네요."),
]


def inject(text: str, fact: str, line: str) -> tuple[str, bool]:
    """심은 문장이 든 글 <b>맨 뒤</b>에 삼단 한 줄을 붙인다.

    심은 문장 바로 뒤에 끼우면 글이 끊긴다 — 「…100억 원이다. / 바로배송 인프라 증설 ·
    약정 계약 규모 = 100억 원 / 이렇게 나와 있어요」 처럼 원래 문장의 뒷부분이 삼단 줄
    너머로 밀린다. 사람이 쓴 글로 안 보이면 데이터셋의 값이 떨어진다.

    맨 뒤에 붙이면 슬랙에서 흔히 보는 「요약 한 줄」로 읽히고, 추출기 입장에서는 같은
    문단 안이라 근거를 잇는 데 문제가 없다. 이미 있으면 그대로 둔다.
    """
    if fact not in text:
        return text, False
    if text.rstrip().endswith(line):
        return text, False                          # 이미 되어 있다
    body = text.replace(f"{fact}\n{line}", fact)   # 문장 사이에 끼워 둔 옛 자리를 걷는다
    return f"{body.rstrip()}\n{line}", True


def fix_doc(path: Path, fact: str, line: str) -> bool:
    doc = json.loads(path.read_text(encoding="utf-8"))
    touched = False

    if doc.get("kind") == "slack_thread":
        for message in doc["messages"]:
            message["text"], hit = inject(message["text"], fact, line)
            touched = touched or hit
    else:
        issue = doc["issue"]
        issue["body"], touched = inject(issue["body"], fact, line)
        if not touched:
            for bucket in ("comments", "review_comments"):
                for item in doc.get(bucket, []):
                    item["body"], hit = inject(item["body"], fact, line)
                    touched = touched or hit

    if touched:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return touched


def scrub(root: Path, dry: bool) -> int:
    """지어낸 값을 지운다. 남겨 두면 같은 자리에 클레임이 하나 더 생긴다."""
    done = 0
    for fragment, pattern, replacement in NOISE:
        for path in root.rglob(f"*{fragment}*.json"):
            raw = path.read_text(encoding="utf-8")
            fixed = re.sub(pattern, replacement, raw)
            if fixed != raw:
                if not dry:
                    path.write_text(fixed, encoding="utf-8")
                print(f"  잡음 지움  {path.name}")
                done += 1
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="new_data/sources")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)

    planted = 0
    for group in scenario.PLANTED:
        for doc in group["docs"]:
            if "triple" not in doc:
                continue
            line = scenario.triple_line(doc["triple"])
            # 문장을 아예 못 찾은 것과 이미 되어 있는 것은 다르다. 앞은 각본과 문서가
            # 어긋났다는 뜻이라 봐야 하고, 뒤는 두 번째 실행이라 조용해도 된다
            found = [p for p in root.rglob("*.json")
                     if doc["fact"] in p.read_text(encoding="utf-8")]
            if not found:
                print(f"  ! 문장을 못 찾음: {doc['fact']}")
                continue
            for path in found:
                if args.dry_run:
                    print(f"  (고치면) {path.name}  ← {line}")
                elif fix_doc(path, doc["fact"], line):
                    print(f"  삼단 박음  {path.name}  ← {line}")
                    planted += 1

    noise = scrub(root, args.dry_run)
    print(f"\n  삼단 {planted}건 · 잡음 {noise}건")


if __name__ == "__main__":
    main()
