from typing import Any

from backend.rules_config import get_decision_policy


DEFAULT_ACTIONS = {
    "可小仓试错": "评分达标，但仍必须等触发条件，不允许满仓追。",
    "纳入观察": "等待触发条件出现再考虑试错。",
    "等待确认": "现在不动，等指数、板块、承接三项确认。",
    "明确回避": "当前不符合高胜率短线框架。",
    "减压防守": "不做摊薄，反抽优先降风险。",
}


def classify_decision_by_policy(score: int, analysis: dict[str, Any], user_state: dict[str, Any], opportunity: dict[str, Any] | None = None) -> tuple[str, str, str]:
    pct = analysis.get("pct") or 0
    risk = analysis.get("risk_level")
    scenario = user_state.get("scenario")
    policy = get_decision_policy()

    if risk == "高" and pct < 0:
        return "明确回避", "0 成", "不抄底、不加仓；已有仓位优先等反抽减压或按纪律止损。"

    opportunity_rule = policy.get("opportunity_rule", {}) if isinstance(policy, dict) else {}
    if opportunity and score >= int(opportunity_rule.get("min_score", 68)):
        return (
            opportunity_rule.get("label", "可小仓试错"),
            opportunity_rule.get("position_limit", "1-2 成"),
            opportunity_rule.get("action", "符合强势/承接条件，可以按触发条件小仓试错，错了立刻撤。"),
        )

    for band in policy.get("decision_bands", []):
        if score >= int(band.get("min_score", 0)):
            label = band.get("label", "等待确认")
            return label, band.get("position_limit", "0 成"), band.get("action", DEFAULT_ACTIONS.get(label, "按规则执行。"))

    if scenario == "被套处理":
        return "减压防守", "不加仓", DEFAULT_ACTIONS["减压防守"]
    return "明确回避", "0 成", DEFAULT_ACTIONS["明确回避"]
