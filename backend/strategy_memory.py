from typing import Any

from backend.persistence import latest_events


def build_strategy_memory(session_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    scenario_counts = profile.get("scenario_counts", {}) or {}
    frequent_scenarios = sorted(scenario_counts.items(), key=lambda pair: pair[1], reverse=True)[:3]
    audits = latest_events(session_id, "audit", 5)
    feedback = latest_events(session_id, "feedback", 10)
    negative_feedback = [item for item in feedback if item.get("useful") is False]
    repeated_risks = []
    for audit in audits:
        repeated_risks.extend((audit.get("risk_veto") or {}).get("reasons", [])[:2])
    return {
        "frequent_scenarios": frequent_scenarios,
        "recent_negative_feedback_count": len(negative_feedback),
        "recent_risks": list(dict.fromkeys(repeated_risks))[:5],
    }


def build_strategy_memory_line(memory: dict[str, Any]) -> str:
    parts = []
    scenarios = memory.get("frequent_scenarios") or []
    if scenarios:
        parts.append("常见场景：" + "、".join(f"{name}{count}次" for name, count in scenarios))
    if memory.get("recent_risks"):
        parts.append("近期风险：" + "；".join(memory["recent_risks"][:3]))
    if memory.get("recent_negative_feedback_count"):
        parts.append(f"近期有 {memory['recent_negative_feedback_count']} 次负反馈，回答应更具体、更少模板化")
    return "【策略记忆】" + "；".join(parts) if parts else ""
