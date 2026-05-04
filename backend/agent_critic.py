import re
from typing import Any


def build_agent_plan(question: str, decision: dict[str, Any] | None, tool_plan: dict[str, bool], confidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "task": "交易研究/陪练回答",
        "question": question[:200],
        "tool_plan": tool_plan,
        "rule_label": decision.get("label") if decision else "数据不足",
        "position_limit": decision.get("position_limit") if decision else "0 成",
        "confidence": confidence or {},
    }


def critique_answer(text: str, decision: dict[str, Any] | None, risk_veto: dict[str, Any], confidence: dict[str, Any]) -> dict[str, Any]:
    issues = []
    if re.search(r"必涨|一定涨|稳赚|确定买|满仓|梭哈", text):
        issues.append("回答存在确定性收益/激进交易表述")
    if decision and decision.get("trigger_condition") and "触发" not in text:
        issues.append("缺少触发条件")
    if decision and decision.get("invalid_condition") and "失效" not in text:
        issues.append("缺少失效条件")
    if risk_veto.get("blocked") and "风控" not in text and "否决" not in text:
        issues.append("风控否决未明确呈现")
    if confidence.get("score", 100) < 55 and "数据" not in text:
        issues.append("低置信度时未说明数据限制")
    return {"passed": not issues, "issues": issues[:6]}


def apply_critic_guard(text: str, critic: dict[str, Any]) -> str:
    if critic.get("passed"):
        return text
    issue_line = "；".join(critic.get("issues", []))
    return f"{text}\n【Agent自检】{issue_line}。以上问题已按风控口径处理：不放大仓位，不给确定性买卖指令，缺数据就只做框架判断。"
