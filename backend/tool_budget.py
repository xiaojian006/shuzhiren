from typing import Any

from backend.rules_config import get_tool_budget_policy


DEFAULT_BUDGET = {
    "max_remote_tools": 4,
    "review_priority": ["sentiment", "sector", "market", "quote", "kline"],
    "market_priority": ["market", "sentiment", "sector", "quote", "kline"],
    "stock_priority": ["quote", "kline", "market", "sentiment", "sector"],
}


def apply_tool_budget(intent: str, tool_plan: dict[str, bool], budget: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = {**DEFAULT_BUDGET, **get_tool_budget_policy(), **(budget or {})}
    if "复盘" in intent:
        priority = policy["review_priority"]
    elif "市场" in intent or "情绪" in intent:
        priority = policy["market_priority"]
    else:
        priority = policy["stock_priority"]
    max_remote = int(policy.get("max_remote_tools", 4))
    enabled = [name for name in priority if tool_plan.get(name)]
    allowed = set(enabled[:max_remote])
    adjusted = {name: bool(enabled and tool_plan.get(name) and name in allowed) for name in tool_plan}
    skipped = [name for name, enabled_flag in tool_plan.items() if enabled_flag and name not in allowed]
    messages = policy.get("degradation_messages", {})
    degradation = [messages.get(name, f"{name} 工具被预算跳过") for name in skipped]
    return {"plan": adjusted, "skipped": skipped, "budget": max_remote, "degradation": degradation}
