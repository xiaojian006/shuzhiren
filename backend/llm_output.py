import json
import re
from typing import Any


REQUIRED_KEYS = {"verdict", "reasoning_summary", "trigger", "invalid", "position_limit", "risk_flags"}
FORBIDDEN_PATTERN = re.compile(r"必涨|一定涨|稳赚|确定买|满仓|梭哈")


def parse_structured_llm(text: str | None, fallback_decision: dict[str, Any] | None) -> dict[str, Any] | None:
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        raw = match.group(0)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not REQUIRED_KEYS.issubset(data):
        return None
    if not isinstance(data.get("risk_flags"), list):
        return None
    combined = " ".join(str(data.get(key, "")) for key in ["verdict", "reasoning_summary", "trigger", "invalid", "position_limit"])
    if FORBIDDEN_PATTERN.search(combined):
        return None
    if "数据不足" in combined and re.search(r"现价\s*[0-9]|涨跌幅\s*[0-9]", combined):
        return None
    if fallback_decision:
        expected_limit = fallback_decision.get("position_limit")
        if expected_limit and str(data.get("position_limit", "")).strip() != str(expected_limit).strip():
            data["position_limit"] = expected_limit
            data.setdefault("risk_flags", []).append("LLM仓位与规则不一致，已按规则覆盖")
    return data


def format_structured_llm(data: dict[str, Any], fallback_text: str) -> str:
    flags = "；".join(str(item) for item in data.get("risk_flags", [])[:5]) or "未触发额外风险"
    lines = [
        f"【直接结论】{data.get('verdict')}",
        f"【核心依据】{data.get('reasoning_summary')}",
        f"【仓位上限】{data.get('position_limit')}",
        f"【触发条件】{data.get('trigger')}",
        f"【失效条件】{data.get('invalid')}",
        f"【风险标记】{flags}",
    ]
    follow_up = data.get("follow_up")
    if follow_up:
        lines.append(f"【继续追问】{follow_up}")
    if len("\n".join(lines)) < 80:
        return fallback_text
    return "\n".join(lines)


def structured_output_instruction() -> str:
    return (
        "优先只返回一个 JSON 对象，不要 Markdown 代码块。字段必须包含："
        "verdict、reasoning_summary、trigger、invalid、position_limit、risk_flags、follow_up。"
        "position_limit 必须服从规则决策，不得自行放大仓位；数据不足时必须明说，不得编造行情。"
    )
