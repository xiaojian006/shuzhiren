import time
from typing import Any, Callable


def compute_answer_confidence_score(
    quote: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    sector: dict[str, Any] | None,
    llm_status: dict[str, str],
    is_trading_time: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    score = 35
    items = []
    missing = []
    if quote:
        score += 20
        items.append("个股行情可用")
    else:
        missing.append("个股行情")
        items.append("个股行情缺失")
    if trend:
        score += 15
        items.append("历史K线可用")
    else:
        missing.append("历史K线")
        items.append("历史K线缺失")
    if market_context:
        score += 10
        items.append("指数数据可用")
    else:
        missing.append("指数数据")
    if sentiment and (sentiment.get("coverage") or 0) >= 0.95:
        score += 12
        items.append("情绪样本覆盖充分")
    elif sentiment:
        score += 5
        items.append("情绪样本覆盖不足")
    else:
        missing.append("情绪样本")
    if sector:
        score += 8
        items.append("板块数据可用")
    else:
        missing.append("板块数据")
    if llm_status.get("status") == "ok":
        score += 5
        items.append("LLM表达正常")
    if llm_status.get("structured") == "ok":
        score += 3
        items.append("LLM结构化输出已校验")
    label = "高" if score >= 82 else "中" if score >= 60 else "低"
    trading = is_trading_time() if is_trading_time else False
    return {
        "score": min(score, 100),
        "label": label,
        "items": items,
        "missing": missing[:6],
        "is_trading_time": trading,
        "computed_at": int(time.time()),
        "data_source": "腾讯/新浪/东方财富公开数据",
    }
