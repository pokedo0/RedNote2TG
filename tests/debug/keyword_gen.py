#!/usr/bin/env python3
"""Debug tool: generate keyword queries from local or remote keyword rules and print them.

Usage:
    python tests/debug/keyword_gen.py
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

from rednote2tg.keyword_rules import (
    describe_note_time,
    generate_keyword_query,
    load_keyword_rules,
)

RULES_URL = "https://gist.github.com/pokedo0/a917d3642838c36e32fda292fc291078/raw/5953718ec0cb432ccbe598017581501416f0de90/xhs_keyword_rules.yaml"


def visual_pad(text: str, width: int, align: str = "left") -> str:
    """Pad string considering double-width (CJK) characters."""
    display_len = sum(2 if unicodedata.east_asian_width(c) in ("W", "F", "A") else 1 for c in text)
    padding = max(0, width - display_len)
    if align == "right":
        return " " * padding + text
    else:
        return text + " " * padding


def main() -> None:
    # 默认直接生成 20 次，无需任何参数或交互输入
    count = 20
    root = Path(__file__).resolve().parent.parent.parent
    local_rules_path = root / "config" / "keyword_rules.yaml"
    rules_source = str(local_rules_path) if local_rules_path.exists() else RULES_URL
    rules = load_keyword_rules(rules_source)

    print(f"\n{'='*80}")
    print(f"  keyword query generator debug")
    print(f"  rules file : {rules_source}")
    print(f"  count      : {count}")
    print(f"{'='*80}")

    header_num = visual_pad("#", 4, "right")
    header_query = visual_pad("keyword query", 50, "left")
    header_len = visual_pad("len", 5, "left")
    header_time = visual_pad("time filter", 15, "left")

    print(f"{header_num}  {header_query} {header_len} {header_time}")
    print(f"{'-'*4}  {'-'*50} {'-'*5} {'-'*15}")

    results = []
    for _ in range(count):
        query = generate_keyword_query(rules)
        word_count = len(query.query.split(rules.joiner)) if rules.joiner else len(query.query.split())
        time_label = describe_note_time(query.note_time)
        results.append((query.query, word_count, time_label))

    # 按词数（len）升序排序
    results.sort(key=lambda x: x[1])

    for i, (q_str, w_count, t_label) in enumerate(results, 1):
        col_num = visual_pad(str(i), 4, "right")
        col_query = visual_pad(q_str, 50, "left")
        col_len = visual_pad(str(w_count), 5, "left")
        col_time = visual_pad(t_label, 15, "left")

        print(f"{col_num}  {col_query} {col_len} {col_time}")

    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
