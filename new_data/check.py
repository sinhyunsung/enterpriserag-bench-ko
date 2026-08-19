"""만들어 둔 문서를 다시 검사한다. 고친 뒤에 돌린다.

    python -m new_data.check --root new_data/sources

생성기가 쓰는 검사를 <b>파일에 대고 다시</b> 돈다. {@link new_data.fix_planted} 처럼
나중에 손으로 고치는 일이 생기면, 그 손질이 규격을 깨거나 심은 문장을 지웠는지
생성 없이 알 수 있어야 한다.

보는 것 셋 — 커넥터 규격 · 심은 문장이 그대로 있는지 · NUL 같은 못 쓸 글자.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.connector_schema import make_validator  # noqa: E402

from new_data import scenario  # noqa: E402

BAD = {"\x00": "NUL", "﻿": "BOM", "​": "폭없는공백"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="new_data/sources")
    args = parser.parse_args()
    root = Path(args.root)

    paths = sorted(root.rglob("*.json"))
    kinds = Counter()
    broken, dirty = [], []

    for path in paths:
        raw = path.read_text(encoding="utf-8")
        source = path.parent.name
        problem = make_validator(source)(raw)
        if problem:
            broken.append((path.name, problem))
        kinds[json.loads(raw).get("kind", "?")] += 1
        for glyph, label in BAD.items():
            if glyph in raw:
                dirty.append((path.name, label, raw.count(glyph)))

    print(f"  문서 {len(paths)}개  {dict(kinds)}")
    print(f"  규격 {len(paths) - len(broken)}/{len(paths)}")
    for name, problem in broken:
        print(f"    ! {name}: {problem}")
    for name, label, count in dirty:
        print(f"    ! {name}: {label} {count}개")

    # 심은 것. 문장과 함께 삼단 줄까지 본다 — 문장만으로는 주어·속성이 갈릴 수 있다
    print()
    blob = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    missing = 0
    for group in scenario.PLANTED:
        marks = []
        for doc in group["docs"]:
            ok = doc["fact"] in blob
            triple = scenario.triple_line(doc["triple"]) if "triple" in doc else None
            pinned = triple is None or triple in blob
            marks.append("O" if ok and pinned else ("문장없음" if not ok else "삼단없음"))
            missing += 0 if (ok and pinned) else 1
        print(f"  {group['id']:16} {group['kind']:12} {' '.join(marks)}   {group['why']}")

    print(f"\n  심은 문서 {sum(len(g['docs']) for g in scenario.PLANTED) - missing}"
          f"/{sum(len(g['docs']) for g in scenario.PLANTED)}")
    sys.exit(1 if (broken or dirty or missing) else 0)


if __name__ == "__main__":
    main()
