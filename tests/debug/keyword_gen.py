#!/usr/bin/env python3
"""Debug tool: generate keyword queries from local or remote keyword rules and print them.

Usage:
    python tests/debug/keyword_gen.py
"""
from __future__ import annotations

import random
import unicodedata
from pathlib import Path

from rednote2tg.config import load_config
from rednote2tg.keyword_rules import (
    describe_note_time,
    generate_keyword_query,
    load_keyword_rules,
)


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
    config_path = root / "config" / "config.yaml"
    
    if not config_path.exists():
        print(f"Error: config file not found at {config_path}")
        return
        
    app_config = load_config(config_path)
    keywords_config = app_config.sources.keywords
    
    if not keywords_config.enabled:
        print("Keyword source is not enabled in config.yaml")
        return

    # Pre-load rules to avoid fetching URL repeatedly
    loaded_rules = {}
    try:
        if keywords_config.rules:
            for rule in keywords_config.rules:
                # When multiple rules are used, allow_local_override is False to match production behavior
                loaded_rules[rule.name] = load_keyword_rules(rule.rules_path, allow_local_override=False)
        else:
            loaded_rules["default"] = load_keyword_rules(keywords_config.rules_path, allow_local_override=True)
    except Exception as exc:
        print(f"Failed to load keyword rules: {exc}")
        return

    print(f"\n{'='*95}")
    print(f"  keyword query generator debug")
    print(f"  config file: {config_path}")
    print(f"  count      : {count}")
    if keywords_config.rules:
        print(f"  rules      : {', '.join(r.name for r in keywords_config.rules)}")
    else:
        print(f"  rules file : {keywords_config.rules_path}")
    print(f"{'='*95}")

    header_num = visual_pad("#", 4, "right")
    header_query = visual_pad("keyword query", 50, "left")
    header_len = visual_pad("len", 5, "left")
    header_time = visual_pad("time filter", 15, "left")
    header_rule = visual_pad("rule name", 15, "left")

    print(f"{header_num}  {header_query} {header_len} {header_time} {header_rule}")
    print(f"{'-'*4}  {'-'*50} {'-'*5} {'-'*15} {'-'*15}")

    rng = random.Random()
    results = []
    
    for _ in range(count):
        if keywords_config.rules:
            threshold = rng.random()
            cumulative = 0.0
            selected_rule = keywords_config.rules[-1]
            for rule in keywords_config.rules:
                cumulative += rule.weight
                if threshold <= cumulative:
                    selected_rule = rule
                    break
            
            rule_name = selected_rule.name
            rules = loaded_rules[rule_name]
        else:
            rule_name = "default"
            rules = loaded_rules[rule_name]
            
        query = generate_keyword_query(rules, rng)
        word_count = len(query.query.split(rules.joiner)) if rules.joiner else len(query.query.split())
        time_label = describe_note_time(query.note_time)
        results.append((query.query, word_count, time_label, rule_name))

    # 按词数（len）升序排序
    results.sort(key=lambda x: x[1])

    for i, (q_str, w_count, t_label, r_name) in enumerate(results, 1):
        col_num = visual_pad(str(i), 4, "right")
        col_query = visual_pad(q_str, 50, "left")
        col_len = visual_pad(str(w_count), 5, "left")
        col_time = visual_pad(t_label, 15, "left")
        col_rule = visual_pad(r_name, 15, "left")

        print(f"{col_num}  {col_query} {col_len} {col_time} {col_rule}")

    print(f"{'='*95}\n")


if __name__ == "__main__":
    main()
