"""
lead_rules.py — single source of truth for CRM lead categorisation.

WHY THIS EXISTS
---------------
Categorisation used to live in two places with a subtle, dangerous bug: the "good"
status list was checked *before* the "bad" list using substring matching, so a
Client Status of "not interested" matched the good term "interested" and was
classified GOOD — polluting the lookalike seed and hiding those leads from the
bad-lead exclusion audience. This module replaces that with an ORDERED rule engine.

THE MODEL (matches the portal's Categorization Settings editor)
---------------------------------------------------------------
  • A RULE is a list of CONDITIONS AND-ed together, plus a target category.
  • A CONDITION is a column (field) + a list of values. It matches when the lead's
    value for that column CONTAINS any of the listed values (case-insensitive
    substring) — so compound statuses like "follow up (warm)" still match "warm".
  • The rule LIST is evaluated top-to-bottom; the FIRST fully-matching rule wins
    (this is the OR across rules). No match → "unclassified".

Because bad/broker rules are ordered BEFORE good rules, "not interested" is caught
by the bad rule and never reaches the "interested" good rule. Ordering — not
special-casing — is what makes substring matching safe.

Categories: "good" | "bad" | "broker" | "unclassified".

Rules persist as JSON in outputs/categorization_rules.json (editable in the
portal). When the file is absent or invalid, DEFAULT_RULES is used — a correct,
ordered reproduction of the legacy lead_categories.yaml intent, plus the
HWC = Hot → Good default the user configured in the editor.

Standalone: stdlib only. Operates on rows already normalised to canonical keys by
crm_analytics._normalize (clientstatus, buyingstatus, hwc, ...), so it never
imports crm_analytics (no cycle).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parents[3] / "outputs" / "categorization_rules.json"

# ── Field + category vocabulary (drives the editor's dropdowns) ────────────────
# canonical key (must match crm_analytics._CANON) -> human label
FIELD_LABELS: dict[str, str] = {
    "clientstatus": "Client Status",
    "buyingstatus": "Buying Status",
    "hwc": "HWC",
    "callstatus": "Call Status",
    "sitevisitstatus": "Site Visit Status",
    "source": "Source",
    "status": "Status",
    "city": "City",
    "currentcity": "Current City",
    "budget": "Budget",
    "configuration": "Configuration",
}

CATEGORY_LABELS: dict[str, str] = {
    "good": "Good Lead",
    "bad": "Bad Lead",
    "broker": "Broker",
    "unclassified": "Unclassified",
}

_VALID_FIELDS = set(FIELD_LABELS)
_VALID_CATEGORIES = set(CATEGORY_LABELS)
_VALID_OPS = {"contains_any", "equals_any", "not_contains_any"}


# ── Default rules (correct, ordered) ──────────────────────────────────────────
# ORDER MATTERS. Broker → bad(client) → good(client) → bad(buying) → good(buying)
#   → hwc hot → site visit. Client-status rules precede buying-status rules so an
# explicit sales-team disposition wins over the raw inbound buying field. Within
# each field, BAD precedes GOOD so "not interested" can never match "interested".
def _rule(category: str, field: str, values: list[str]) -> dict:
    return {"id": uuid.uuid4().hex[:8], "category": category,
            "conditions": [{"field": field, "op": "contains_any", "values": values}]}


DEFAULT_RULES: list[dict] = [
    _rule("broker", "clientstatus", ["broker"]),
    _rule("bad",    "clientstatus", ["not interested", "cold", "lost", "low budget"]),
    _rule("good",   "clientstatus", ["warm", "interested"]),
    _rule("bad",    "buyingstatus", ["not_ready", "not ready", "cold", "not interested",
                                      "no interest", "dead", "lost", "spam", "duplicate",
                                      "invalid", "not_interested"]),
    _rule("good",   "buyingstatus", ["exploring", "hot", "warm", "interested", "active",
                                      "qualified", "follow up", "postponed", "still searching"]),
    _rule("good",   "hwc",          ["hot"]),
    _rule("good",   "sitevisitstatus", ["visited", "completed", "confirmed",
                                        "visit date confirmed"]),
]


# ── Matching ──────────────────────────────────────────────────────────────────
def _match_condition(norm_row: dict, cond: dict) -> bool:
    """True when the lead's value for cond['field'] matches cond['values'] per op."""
    field = cond.get("field", "")
    values = [str(v).strip().lower() for v in (cond.get("values") or []) if str(v).strip()]
    if not field or not values:
        return False
    hay = str(norm_row.get(field, "") or "").strip().lower()
    op = cond.get("op", "contains_any")
    if not hay:
        # An empty field can only satisfy a "not_contains_any" condition.
        return op == "not_contains_any"
    if op == "equals_any":
        return hay in values
    if op == "not_contains_any":
        return not any(v in hay for v in values)
    return any(v in hay for v in values)  # contains_any (default)


def _match_rule(norm_row: dict, rule: dict) -> bool:
    """A rule matches when ALL its conditions match (AND). Empty rule never matches."""
    conditions = rule.get("conditions") or []
    if not conditions:
        return False
    return all(_match_condition(norm_row, c) for c in conditions)


def classify(norm_row: dict, rules: list[dict] | None = None) -> str:
    """Return the category for a normalised lead row. First matching rule wins."""
    rules = rules if rules is not None else load_rules()
    for rule in rules:
        if _match_rule(norm_row, rule):
            cat = rule.get("category", "unclassified")
            return cat if cat in _VALID_CATEGORIES else "unclassified"
    return "unclassified"


def is_good(norm_row: dict, rules: list[dict] | None = None) -> bool:
    """A lead is a quality/good lead when it classifies as 'good'."""
    return classify(norm_row, rules) == "good"


# ── Persistence ───────────────────────────────────────────────────────────────
def validate_rules(rules) -> list[dict]:
    """Coerce arbitrary input into a clean, safe rule list. Raises ValueError on
    structural problems so the API can return a 400 instead of silently persisting
    garbage that would break categorisation for every lead."""
    if not isinstance(rules, list):
        raise ValueError("Rules must be a list.")
    clean: list[dict] = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError(f"Rule {i + 1} is not an object.")
        category = r.get("category")
        if category not in _VALID_CATEGORIES:
            raise ValueError(f"Rule {i + 1} has invalid category {category!r}.")
        raw_conditions = r.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ValueError(f"Rule {i + 1} must have at least one condition.")
        conditions = []
        for j, c in enumerate(raw_conditions):
            if not isinstance(c, dict):
                raise ValueError(f"Rule {i + 1} condition {j + 1} is not an object.")
            field = c.get("field")
            if field not in _VALID_FIELDS:
                raise ValueError(f"Rule {i + 1} condition {j + 1} has invalid field {field!r}.")
            op = c.get("op", "contains_any")
            if op not in _VALID_OPS:
                op = "contains_any"
            values = [str(v).strip() for v in (c.get("values") or []) if str(v).strip()]
            if not values:
                raise ValueError(f"Rule {i + 1} condition {j + 1} has no values.")
            conditions.append({"field": field, "op": op, "values": values})
        clean.append({
            "id": str(r.get("id") or uuid.uuid4().hex[:8]),
            "category": category,
            "conditions": conditions,
        })
    return clean


# Small in-process cache keyed by the rules file's mtime. classify()/is_good() run
# in per-lead loops across the analytics layer, so re-reading + re-validating the
# JSON for every lead would be needlessly expensive. The cache invalidates whenever
# the file is written (save_rules bumps mtime) or removed (reset).
_CACHE: dict = {"key": None, "rules": None}


def load_rules() -> list[dict]:
    """Return the persisted rules, or a copy of DEFAULT_RULES when none exist / invalid.
    Cached by (path, mtime) so tight per-lead loops don't re-read the file each time."""
    try:
        mtime = _RULES_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is None:
        return [dict(r) for r in DEFAULT_RULES]
    key = (str(_RULES_PATH), mtime)
    if _CACHE["key"] == key and _CACHE["rules"] is not None:
        return _CACHE["rules"]
    try:
        raw = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
        rules = raw.get("rules") if isinstance(raw, dict) else raw
        clean = validate_rules(rules)
    except Exception:
        return [dict(r) for r in DEFAULT_RULES]
    _CACHE.update({"key": key, "rules": clean})
    return clean


def save_rules(rules) -> list[dict]:
    """Validate and persist rules to disk. Returns the cleaned rules. Raises ValueError
    if the rules are structurally invalid (so nothing invalid is ever persisted)."""
    clean = validate_rules(rules)
    _RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RULES_PATH.write_text(json.dumps({"version": 1, "rules": clean}, indent=2,
                                      ensure_ascii=False), encoding="utf-8")
    return clean


def is_using_defaults() -> bool:
    """True when no persisted rules file exists (the editor shows DEFAULT_RULES)."""
    return not _RULES_PATH.exists()


def editor_metadata() -> dict:
    """Everything the editor UI needs to render: field + category option lists."""
    return {
        "fields": [{"key": k, "label": v} for k, v in FIELD_LABELS.items()],
        "categories": [{"key": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "ops": [
            {"key": "contains_any", "label": "contains any of"},
            {"key": "equals_any", "label": "is exactly one of"},
            {"key": "not_contains_any", "label": "does not contain"},
        ],
    }
