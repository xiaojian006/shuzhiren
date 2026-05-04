import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "backend" / "rules"


def load_rule_file(name: str, default: Any) -> Any:
    path = RULES_DIR / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


INTENT_PATTERNS = load_rule_file("intent_patterns.json", {})
RISK_VETO_RULES = load_rule_file("risk_veto.json", {})
POSITION_POLICY = load_rule_file("position_policy.json", {})


def get_intent_patterns() -> list[dict[str, str]]:
    return INTENT_PATTERNS.get("patterns", []) if isinstance(INTENT_PATTERNS, dict) else []


def get_tool_patterns() -> dict[str, str]:
    return INTENT_PATTERNS.get("tool_patterns", {}) if isinstance(INTENT_PATTERNS, dict) else {}


def get_position_mapping() -> dict[str, float]:
    mapping = POSITION_POLICY.get("position_mapping", {}) if isinstance(POSITION_POLICY, dict) else {}
    return {str(key): float(value) for key, value in mapping.items()}


def get_decision_policy() -> dict[str, Any]:
    return POSITION_POLICY if isinstance(POSITION_POLICY, dict) else {}


def get_tool_budget_policy() -> dict[str, Any]:
    value = load_rule_file("tool_budget.json", {})
    return value if isinstance(value, dict) else {}


def get_risk_policy() -> dict[str, Any]:
    return RISK_VETO_RULES if isinstance(RISK_VETO_RULES, dict) else {}
