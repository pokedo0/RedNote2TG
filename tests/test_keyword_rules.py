import urllib.error
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

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


class FakeRandom:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)

    def choice(self, values):
        return values[0]


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.text.encode("utf-8")


class KeywordRulesTest(unittest.TestCase):
    def test_loads_rules_and_generates_query(self):
        rules = parse_keyword_rules(base_rules())

        query = generate_keyword_query(rules)

        self.assertEqual(query.query, "高跟 水晶 白色")
        self.assertEqual(query.note_time, 3)

    def test_allows_single_term_keyword_rules(self):
        data = {
            "joiner": " ",
            "length_weights": {1: 1.0},
            "required_pools": [["仙女", "女神"]],
            "optional_groups": {
                "attributes": {
                    "weight": 1.0,
                    "pools": ["蕾丝"],
                },
            },
            "time_weights": {"unlimited": 1.0},
        }
        rules = parse_keyword_rules(data)

        query = generate_keyword_query(rules)

        self.assertIn(query.query, {"仙女", "女神"})
        self.assertEqual(query.note_time, 0)

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

    def test_loads_rules_from_url(self):
        with TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "keyword_rules.yaml"
            response = FakeResponse(yaml.safe_dump(base_rules(), allow_unicode=True))

            with patch("rednote2tg.keyword_rules.LOCAL_RULES_OVERRIDE_PATH", override_path), patch(
                "rednote2tg.keyword_rules.urllib.request.urlopen",
                return_value=response,
            ) as urlopen:
                rules = load_keyword_rules("https://example.com/rules.yaml")

        self.assertEqual(generate_keyword_query(rules).query, "高跟 水晶 白色")
        urlopen.assert_called_once()

    def test_local_rules_override_url(self):
        with TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "keyword_rules.yaml"
            override_path.write_text(yaml.safe_dump(base_rules(), allow_unicode=True), encoding="utf-8")

            with patch("rednote2tg.keyword_rules.LOCAL_RULES_OVERRIDE_PATH", override_path), patch(
                "rednote2tg.keyword_rules.urllib.request.urlopen",
                side_effect=AssertionError("remote should not be fetched"),
            ):
                rules = load_keyword_rules("https://example.com/rules.yaml")

        self.assertEqual(generate_keyword_query(rules).query, "高跟 水晶 白色")

    def test_can_disable_local_rules_override_for_url(self):
        with TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "keyword_rules.yaml"
            override_path.write_text(yaml.safe_dump(base_rules(), allow_unicode=True), encoding="utf-8")
            remote_rules = base_rules()
            remote_rules["required_pools"] = [["凉鞋"], ["水晶"]]
            response = FakeResponse(yaml.safe_dump(remote_rules, allow_unicode=True))

            with patch("rednote2tg.keyword_rules.LOCAL_RULES_OVERRIDE_PATH", override_path), patch(
                "rednote2tg.keyword_rules.urllib.request.urlopen",
                return_value=response,
            ) as urlopen:
                rules = load_keyword_rules("https://example.com/rules.yaml", allow_local_override=False)

        self.assertEqual(generate_keyword_query(rules).query, "凉鞋 水晶 白色")
        urlopen.assert_called_once()

    def test_remote_rules_failure_raises_rule_error(self):
        with TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "keyword_rules.yaml"

            with patch("rednote2tg.keyword_rules.LOCAL_RULES_OVERRIDE_PATH", override_path), patch(
                "rednote2tg.keyword_rules.urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ):
                with self.assertRaises(KeywordRuleError):
                    load_keyword_rules("https://example.com/rules.yaml")

    def test_rejects_bad_weight_sum(self):
        data = base_rules()
        data["length_weights"] = {3: 0.5}

        with self.assertRaises(KeywordRuleError):
            parse_keyword_rules(data)

    def test_rejects_length_outside_supported_range(self):
        data = base_rules()
        data["length_weights"] = {0: 1.0}

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

    def test_max_total_length_keeps_late_brand_and_trims_other_optional_terms(self):
        data = {
            "joiner": " ",
            "length_weights": {6: 1.0},
            "required_pools": [["高跟"], ["水晶"]],
            "optional_groups": {
                "attributes": {
                    "weight": 0.5,
                    "pools": ["白色", "粗跟"],
                },
                "long_tail": {
                    "weight": 0.4,
                    "pools": ["夏日"],
                },
                "brands": {
                    "weight": 0.1,
                    "max_total_length": 4,
                    "pools": ["zara"],
                },
            },
            "time_weights": {"unlimited": 1.0},
        }
        rules = parse_keyword_rules(data)
        rng = FakeRandom([0.0, 0.1, 0.1, 0.6, 0.95, 0.0])

        query = generate_keyword_query(rules, rng)

        self.assertEqual(query.query, "高跟 水晶 白色 zara")

    def test_rejects_max_total_length_that_cannot_keep_required_and_group_term(self):
        data = base_rules()
        data["optional_groups"]["attributes"]["max_total_length"] = 2

        with self.assertRaises(KeywordRuleError):
            parse_keyword_rules(data)

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
