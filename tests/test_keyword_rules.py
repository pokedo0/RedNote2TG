import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rednote2tg.keyword_rules import KeywordRuleError, describe_note_time, generate_keyword_query, load_keyword_rules, parse_keyword_rules


def base_rules():
    return {
        "joiner": " ",
        "length_weights": {3: 1.0},
        "required_pools": [["高跟"], ["水晶"]],
        "optional_groups": {
            "attributes": {
                "weight": 1.0,
                "pools": ["白色"],
            },
        },
        "time_weights": {"half_year": 1.0},
    }


class KeywordRulesTest(unittest.TestCase):
    def test_loads_rules_and_generates_query(self):
        rules = parse_keyword_rules(base_rules())

        query = generate_keyword_query(rules)

        self.assertEqual(query.query, "高跟 水晶 白色")
        self.assertEqual(query.note_time, 3)

    def test_loads_rules_from_yaml_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "keyword_rules.yaml"
            path.write_text(
                """
joiner: " "
length_weights:
  3: 1.0
required_pools:
  - ["高跟"]
  - ["水晶"]
optional_groups:
  attributes:
    weight: 1.0
    pools:
      - "白色"
time_weights:
  unlimited: 1.0
""".strip(),
                encoding="utf-8",
            )

            rules = load_keyword_rules(path)

        self.assertEqual(generate_keyword_query(rules).query, "高跟 水晶 白色")

    def test_rejects_bad_weight_sum(self):
        data = base_rules()
        data["length_weights"] = {3: 0.5}

        with self.assertRaises(KeywordRuleError):
            parse_keyword_rules(data)

    def test_rejects_length_outside_supported_range(self):
        data = base_rules()
        data["length_weights"] = {2: 1.0}

        with self.assertRaises(KeywordRuleError):
            parse_keyword_rules(data)

    def test_array_pool_is_used_once_and_terms_do_not_repeat(self):
        data = {
            "joiner": " ",
            "length_weights": {4: 1.0},
            "required_pools": [["高跟"], ["水晶"]],
            "optional_groups": {
                "attributes": {
                    "weight": 1.0,
                    "pools": [["紫色", "白色"], "露脚趾"],
                },
            },
            "time_weights": {"unlimited": 1.0},
        }
        rules = parse_keyword_rules(data)

        query = generate_keyword_query(rules)
        terms = query.query.split(" ")

        self.assertEqual(len(terms), 4)
        self.assertEqual(len(set(terms)), 4)
        self.assertLessEqual(len({"紫色", "白色"} & set(terms)), 1)

    def test_exhausted_group_weight_renormalizes_to_remaining_groups(self):
        data = {
            "joiner": " ",
            "length_weights": {5: 1.0},
            "required_pools": [["高跟"], ["水晶"]],
            "optional_groups": {
                "brands": {
                    "weight": 0.9,
                    "pools": ["zara"],
                },
                "attributes": {
                    "weight": 0.1,
                    "pools": ["白色", "粗跟"],
                },
            },
            "time_weights": {"one_week": 1.0},
        }
        rules = parse_keyword_rules(data)

        query = generate_keyword_query(rules)

        self.assertEqual(len(query.query.split(" ")), 5)
        self.assertEqual(query.note_time, 2)

    def test_raises_when_target_length_cannot_be_filled(self):
        data = base_rules()
        data["length_weights"] = {4: 1.0}

        rules = parse_keyword_rules(data)

        with self.assertRaises(KeywordRuleError):
            generate_keyword_query(rules)

    def test_describes_note_time_values(self):
        self.assertEqual(describe_note_time(0), "不限")
        self.assertEqual(describe_note_time(2), "一周内")
        self.assertEqual(describe_note_time(3), "半年内")
        self.assertEqual(describe_note_time(None), "-")
        self.assertEqual(describe_note_time(99), "未知(99)")


if __name__ == "__main__":
    unittest.main()
