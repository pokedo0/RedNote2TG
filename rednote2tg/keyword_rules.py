from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


class KeywordRuleError(ValueError):
    pass


@dataclass(frozen=True)
class KeywordQuery:
    query: str
    note_time: int


@dataclass(frozen=True)
class KeywordRules:
    joiner: str
    length_weights: dict[int, float]
    required_pools: tuple[tuple[str, ...], ...]
    optional_groups: tuple["OptionalGroup", ...]
    time_weights: dict[str, float]


@dataclass(frozen=True)
class OptionalGroup:
    name: str
    weight: float
    pools: tuple[tuple[str, ...], ...]


NOTE_TIME_VALUES = {
    "unlimited": 0,
    "one_week": 2,
    "half_year": 3,
}

NOTE_TIME_LABELS = {
    0: "不限",
    1: "一天内",
    2: "一周内",
    3: "半年内",
}

_WEIGHT_TOLERANCE = 0.000001


def load_keyword_rules(path: str | Path) -> KeywordRules:
    rules_path = Path(path)
    if not rules_path.exists():
        raise KeywordRuleError(f"keyword rules file not found: {rules_path}")

    try:
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise KeywordRuleError(f"keyword rules YAML is invalid: {exc}") from exc

    if not isinstance(data, dict):
        raise KeywordRuleError("keyword rules root must be a mapping")
    return parse_keyword_rules(data)


def parse_keyword_rules(data: Mapping[str, Any]) -> KeywordRules:
    joiner = str(data.get("joiner", " "))
    length_weights = _parse_int_weights(data.get("length_weights"), "length_weights")
    if any(length < 3 or length > 6 for length in length_weights):
        raise KeywordRuleError("length_weights keys must be between 3 and 6")

    required_pools = tuple(
        _parse_term_pool(pool, f"required_pools[{index}]")
        for index, pool in enumerate(_require_sequence(data.get("required_pools"), "required_pools"))
    )
    if not required_pools:
        raise KeywordRuleError("required_pools must contain at least one pool")
    if min(length_weights) < len(required_pools):
        raise KeywordRuleError("length_weights minimum is smaller than required_pools count")

    optional_groups = _parse_optional_groups(data.get("optional_groups"))
    _validate_weight_sum({group.name: group.weight for group in optional_groups}, "optional_groups weights")

    time_weights = _parse_str_weights(data.get("time_weights"), "time_weights")
    unsupported = sorted(set(time_weights) - set(NOTE_TIME_VALUES))
    if unsupported:
        raise KeywordRuleError(f"unsupported time_weights keys: {', '.join(unsupported)}")

    return KeywordRules(
        joiner=joiner,
        length_weights=length_weights,
        required_pools=required_pools,
        optional_groups=optional_groups,
        time_weights=time_weights,
    )


def generate_keyword_query(rules: KeywordRules, rng: random.Random | None = None) -> KeywordQuery:
    rng = rng or random.Random()
    target_length = _weighted_choice(rules.length_weights, rng)
    selected_terms: list[str] = []

    for pool in rules.required_pools:
        selected_terms.append(_choose_term(pool, set(selected_terms), rng))

    used_pools: set[tuple[str, int]] = set()
    while len(selected_terms) < target_length:
        selected_set = set(selected_terms)
        available_groups = {
            group: group.weight
            for group in rules.optional_groups
            if _available_pool_indexes(group, used_pools, selected_set)
        }
        if not available_groups:
            raise KeywordRuleError(f"keyword rules cannot fill target length {target_length}")

        group = _weighted_choice(available_groups, rng)
        pool_indexes = _available_pool_indexes(group, used_pools, selected_set)
        pool_index = rng.choice(pool_indexes)
        pool = group.pools[pool_index]
        selected_terms.append(_choose_term(pool, selected_set, rng))
        used_pools.add((group.name, pool_index))

    time_key = _weighted_choice(rules.time_weights, rng)
    return KeywordQuery(rules.joiner.join(selected_terms), NOTE_TIME_VALUES[time_key])


def describe_note_time(note_time: int | None) -> str:
    if note_time is None:
        return "-"
    return NOTE_TIME_LABELS.get(note_time, f"未知({note_time})")


def _parse_optional_groups(value: object) -> tuple[OptionalGroup, ...]:
    if not isinstance(value, dict) or not value:
        raise KeywordRuleError("optional_groups must be a non-empty mapping")

    groups: list[OptionalGroup] = []
    for name, raw_group in value.items():
        if not isinstance(raw_group, dict):
            raise KeywordRuleError(f"optional_groups.{name} must be a mapping")
        weight = _parse_weight(raw_group.get("weight"), f"optional_groups.{name}.weight")
        raw_pools = _require_sequence(raw_group.get("pools"), f"optional_groups.{name}.pools")
        pools = tuple(
            _parse_term_pool(pool, f"optional_groups.{name}.pools[{index}]")
            for index, pool in enumerate(raw_pools)
        )
        if not pools:
            raise KeywordRuleError(f"optional_groups.{name}.pools must contain at least one pool")
        groups.append(OptionalGroup(str(name), weight, pools))
    return tuple(groups)


def _parse_int_weights(value: object, name: str) -> dict[int, float]:
    if not isinstance(value, dict) or not value:
        raise KeywordRuleError(f"{name} must be a non-empty mapping")
    weights: dict[int, float] = {}
    for raw_key, raw_weight in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise KeywordRuleError(f"{name} keys must be integers") from exc
        weights[key] = _parse_weight(raw_weight, f"{name}.{raw_key}")
    _validate_weight_sum(weights, name)
    return weights


def _parse_str_weights(value: object, name: str) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise KeywordRuleError(f"{name} must be a non-empty mapping")
    weights = {str(key): _parse_weight(weight, f"{name}.{key}") for key, weight in value.items()}
    _validate_weight_sum(weights, name)
    return weights


def _parse_weight(value: object, name: str) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise KeywordRuleError(f"{name} must be a number") from exc
    if weight <= 0:
        raise KeywordRuleError(f"{name} must be greater than zero")
    return weight


def _validate_weight_sum(weights: Mapping[Any, float], name: str) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > _WEIGHT_TOLERANCE:
        raise KeywordRuleError(f"{name} must sum to 1.0")


def _require_sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, list) or not value:
        raise KeywordRuleError(f"{name} must be a non-empty list")
    return value


def _parse_term_pool(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        terms = (value.strip(),)
    elif isinstance(value, list):
        terms = tuple(str(term).strip() for term in value if str(term).strip())
    else:
        raise KeywordRuleError(f"{name} must be a string or list of strings")

    if not terms:
        raise KeywordRuleError(f"{name} must contain at least one term")
    return terms


def _weighted_choice(weights: Mapping[Any, float], rng: random.Random) -> Any:
    total = sum(weights.values())
    point = rng.random() * total
    cumulative = 0.0
    last_key = None
    for key, weight in weights.items():
        cumulative += weight
        last_key = key
        if point <= cumulative:
            return key
    return last_key


def _available_pool_indexes(group: OptionalGroup, used_pools: set[tuple[str, int]], selected_terms: set[str]) -> list[int]:
    indexes = []
    for index, pool in enumerate(group.pools):
        if (group.name, index) in used_pools:
            continue
        if any(term not in selected_terms for term in pool):
            indexes.append(index)
    return indexes


def _choose_term(pool: tuple[str, ...], selected_terms: set[str], rng: random.Random) -> str:
    available_terms = [term for term in pool if term not in selected_terms]
    if not available_terms:
        raise KeywordRuleError("keyword rules selected a pool with no available terms")
    return rng.choice(available_terms)
