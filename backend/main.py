import json
import os
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.agent_runtime import run_parallel_tools
from backend.llm_client import call_openai_compatible


ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

app = FastAPI(title="AI 游资框架集合体数字人", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=ROOT), name="static")


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    role: str = "cycle"


class FeedbackRequest(BaseModel):
    session_id: str = "default"
    question: str
    answer: str
    useful: bool


class WatchlistRequest(BaseModel):
    session_id: str = "default"
    question: str = ""
    code: str | None = None


LOCAL_STOCK_ALIASES = {
    "豫能控股": "001896",
    "贵州茅台": "600519",
    "平安银行": "000001",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "东方财富": "300059",
    "中信证券": "600030",
    "浪潮信息": "000977",
    "中际旭创": "300308",
    "工业富联": "601138",
}

STOCK_SECTOR_ALIASES = {
    "001896": "燃气电力",
    "600519": "白酒",
    "000001": "银行",
    "300750": "锂电池",
    "002594": "新能源汽车",
    "300059": "互联网金融",
    "600030": "证券",
    "000977": "算力",
    "300308": "CPO",
    "601138": "AI算力",
}

SYSTEM_PROMPT = """
你是“游资思维集合体数字人”，不是任何真实人物本人。
底层认知：市场情绪第一，技术指标第二，基本面最后；只做情绪拐点和主线核心，不做弱势反弹、不做杂毛补涨；风控永远第一，不懂不做，退潮期管住手；不预测点位，不给确定买卖点，不承诺收益。
固定思考：每个股票问题必须按 6 步推演：市场情绪周期 -> 问题类型 -> 标的层级 -> 资金/情绪/筹码三维判断 -> 明确结论 -> 游资口语输出。
表达方式：像真人交易教练一样沟通，先回应用户真实问题，再给判断；短句、直接、少套话，但不要机械堆模板。
对话能力：允许回答关于系统能力、使用方法、模型限制、信息不足、复盘方法、心态纪律等问题；普通闲聊要自然承接，再引导回交易纪律。
追问能力：如果缺少成本、仓位、持股/空仓、周期、具体标的等关键变量，先明确说明缺什么，并给一个可继续追问的问题。
禁忌：不鼓励越跌越补，不输出确定性荐股/买卖点/收益承诺，不盲目看多，不编造不存在的数据。
""".strip()

COGNITIVE_RULES = [
    "市场情绪第一，技术指标第二，基本面最后。",
    "只做主线和辨识度，不碰弱势杂毛。",
    "风控优先，退潮期管住手。",
    "不预测，只应对；不幻想，只执行条件。",
]

SLANG_DICT = {
    "核按钮": "不计成本砸出，通常出现在恐慌退潮期。",
    "吃面": "短线大亏，说明交易节奏或风控出问题。",
    "吃肉": "短线盈利，但不能因为一次盈利破坏纪律。",
    "龙头": "能带动板块、分歧有人接、修复先回流的核心票。",
    "杂毛": "没有地位、没有带动性、只跟风套利的弱标的。",
    "换手": "筹码交换程度，强势核心需要健康换手。",
    "卡位": "同题材内后排反超前排，常发生在分歧阶段。",
}

MEMORY_CASES = [
    "经历过大幅波动后，短线资金更重视亏钱效应是否扩散，而不是单只票的故事。",
    "成熟游资常见做法是只在主线、辨识度、承接、买点同时成立时进攻，退潮期主动降低频率。",
    "打板、低吸、半路都不是核心，核心是情绪阶段和风险收益比是否匹配。",
]

ROLE_PROFILES = {
    "cycle": {
        "name": "情绪周期型",
        "style": "沉稳，先看市场周期，再看主线和承接。",
        "focus": "情绪周期、亏钱效应、主线持续性",
    },
    "leader": {
        "name": "龙头进攻型",
        "style": "更果断，重视辨识度和强者恒强，但严格控制追高。",
        "focus": "龙头地位、弱转强、分歧转一致",
    },
    "defense": {
        "name": "风控防守型",
        "style": "更保守，优先保本金，先判断失效条件。",
        "focus": "仓位、止损、回撤控制",
    },
    "first_board": {
        "name": "低位试错型",
        "style": "重视低位首板和性价比，不追高位一致。",
        "focus": "低位启动、量能异动、首板试错",
    },
}

SESSION_MEMORY: dict[str, dict[str, Any]] = {}
DEFAULT_LLM_STATUS: dict[str, str] = {"provider": "none", "status": "not_called", "detail": "未调用大模型"}
DATA_CACHE: dict[str, dict[str, Any]] = {}
DATA_SOURCE_HEALTH: dict[str, dict[str, Any]] = {}


def cache_get(key: str) -> Any | None:
    item = DATA_CACHE.get(key)
    if not item or item["expires_at"] < time.time():
        DATA_CACHE.pop(key, None)
        return None
    return item["value"]


def cache_set(key: str, value: Any, ttl: int) -> Any:
    DATA_CACHE[key] = {"value": value, "expires_at": time.time() + ttl}
    return value


def record_source_health(source: str, ok: bool, elapsed_ms: int, error: str | None = None) -> None:
    item = DATA_SOURCE_HEALTH.setdefault(
        source,
        {"source": source, "success": 0, "failure": 0, "total_ms": 0, "last_ok": None, "last_error": None, "last_elapsed_ms": None, "updated_at": None},
    )
    if ok:
        item["success"] += 1
        item["last_ok"] = int(time.time())
        item["last_error"] = None
    else:
        item["failure"] += 1
        item["last_error"] = error[:180] if error else "unknown"
    item["total_ms"] += elapsed_ms
    item["last_elapsed_ms"] = elapsed_ms
    item["updated_at"] = int(time.time())


def source_name_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "eastmoney" in host:
        return "东方财富"
    if "gtimg" in host:
        return "腾讯行情"
    if "sina" in host:
        return "新浪行情"
    if "sse.com" in host:
        return "上交所"
    if "szse.cn" in host:
        return "深交所"
    if "bse.cn" in host:
        return "北交所"
    return host or "未知数据源"


def get_data_source_health() -> list[dict[str, Any]]:
    result = []
    for item in DATA_SOURCE_HEALTH.values():
        total = item["success"] + item["failure"]
        result.append(
            {
                **item,
                "success_rate": round(item["success"] / total, 3) if total else None,
                "avg_elapsed_ms": round(item["total_ms"] / total) if total else None,
                "status": "健康" if total and item["failure"] == 0 else "降级" if total and item["success"] else "异常",
            }
        )
    result.sort(key=lambda row: (row.get("status") != "健康", row.get("source", "")))
    return result


def load_user_profiles() -> dict[str, Any]:
    path = ROOT / "backend" / "user_profiles.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_user_profiles() -> None:
    path = ROOT / "backend" / "user_profiles.json"
    path.write_text(json.dumps(SESSION_MEMORY, ensure_ascii=False, indent=2), encoding="utf-8")


SESSION_MEMORY.update(load_user_profiles())


def load_knowledge_base() -> list[dict[str, Any]]:
    path = ROOT / "backend" / "knowledge_base.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


KNOWLEDGE_BASE = load_knowledge_base()


def load_trader_experience_cases() -> list[dict[str, Any]]:
    path = ROOT / "backend" / "trader_experience_cases.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


TRADER_EXPERIENCE_CASES = load_trader_experience_cases()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-digital-human"}


@app.get("/app.js")
def frontend_script() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def frontend_style() -> FileResponse:
    return FileResponse(ROOT / "styles.css", media_type="text/css")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    question = request.question.strip()
    session_id = request.session_id.strip() or "default"
    role = request.role if request.role in ROLE_PROFILES else "cycle"
    shortcut = handle_shortcut_command(question, session_id, role)
    if shortcut:
        return shortcut
    agent_result = run_agent(question, session_id, role)
    stock = agent_result["stock"]
    quote = agent_result["quote"]
    analysis = agent_result["analysis"]
    trend = agent_result["trend"]
    market_context = agent_result["market_context"]
    market_sentiment = agent_result["market_sentiment"]
    tool_plan = agent_result["tool_plan"]
    market_dashboard = fetch_market_dashboard(market_sentiment) if tool_plan["sentiment"] else None
    mainline_rank = fetch_mainline_rank() if tool_plan["sector"] else []
    sector_context = agent_result["sector_context"]
    user_state = agent_result["user_state"]
    decision = agent_result["decision"]
    experience_cases = agent_result["experience_cases"]
    risk_veto = evaluate_risk_veto(question, quote, analysis, trend, market_context, market_sentiment, sector_context, user_state)
    original_decision = decision.copy() if decision else None
    if decision and risk_veto["blocked"]:
        decision = apply_risk_veto(decision, risk_veto)
    decision_panel = build_decision_panel(decision, market_context, market_sentiment, sector_context, trend, user_state)
    related_knowledge = agent_result["related_knowledge"]
    thinking_framework = agent_result["thinking_framework"]
    emotion = agent_result["emotion"]
    slang_notes = agent_result["slang_notes"]
    role_profile = ROLE_PROFILES[role]
    agent_steps = agent_result["agent_steps"]
    rule_text = build_agent_reply(question, stock, quote, analysis, trend, market_context, user_state, decision, related_knowledge, thinking_framework, emotion, role_profile, slang_notes, sector_context, market_sentiment, mainline_rank, experience_cases)
    prompt_context = build_prompt_context(session_id, question, stock, quote, analysis, trend, market_context, market_sentiment, sector_context, user_state, decision, related_knowledge, experience_cases, thinking_framework, emotion, role_profile, slang_notes, agent_steps, rule_text)
    llm_text, llm_status = call_llm(prompt_context)
    text = llm_text or rule_text
    if quote and analysis and decision and "【交易剧本】" not in text:
        text = f"{text}\n{build_execution_matrix(question, quote, analysis, trend, user_state, decision)}"
    reminder = build_personal_risk_reminder(session_id, question)
    if reminder:
        text = f"{text}\n{reminder}"
    action = select_action(text, analysis)
    update_session_memory(session_id, question, text, stock, quote, user_state, role)
    confidence = compute_answer_confidence(quote, trend, market_context, market_sentiment, sector_context, llm_status)
    data_lineage = build_data_lineage(quote, trend, market_context, market_sentiment, sector_context, market_dashboard, mainline_rank, confidence, llm_status)
    decision_audit = build_decision_audit(session_id, question, role, stock, original_decision, decision, risk_veto, confidence, data_lineage, agent_steps)
    persist_decision_audit(decision_audit)

    return {
        "text": text,
        "intent": "个股行情研究" if quote else detect_intent(question),
        "mood": action["mood"],
        "gesture": action["gesture"],
        "motion": action["motion"],
        "quote": quote,
        "analysis": analysis,
        "trend": trend,
        "market_context": market_context,
        "market_sentiment": market_sentiment,
        "market_dashboard": market_dashboard,
        "mainline_rank": mainline_rank,
        "sector_context": sector_context,
        "user_state": user_state,
        "decision": decision,
        "decision_panel": decision_panel,
        "related_knowledge": related_knowledge,
        "experience_cases": experience_cases,
        "thinking_framework": thinking_framework,
        "emotion": emotion,
        "slang_notes": slang_notes,
        "role_profile": role_profile,
        "agent_steps": agent_steps,
        "tool_plan": tool_plan,
        "llm_status": llm_status,
        "answer_confidence": confidence,
        "data_lineage": data_lineage,
        "risk_veto": risk_veto,
        "decision_audit": decision_audit,
        "source_health": get_data_source_health(),
        "watchlist": get_profile(session_id).get("watchlist", []),
        "audio_url": None,
        "tts_provider": "browser",
    }


@app.get("/api/chat/progress")
def chat_progress(question: str, session_id: str = "default", role: str = "cycle") -> StreamingResponse:
    def event_stream():
        clean_question = question.strip()
        clean_role = role if role in ROLE_PROFILES else "cycle"
        intent = detect_intent(clean_question)
        emotion = detect_user_emotion(clean_question)
        stock = resolve_stock(clean_question) or recall_last_stock(session_id, clean_question)
        tool_plan = build_tool_plan(clean_question, intent, stock, parse_user_state(clean_question))
        events = [
            {"state": "thinking", "intent": "理解问题", "gesture": "分析语义", "detail": f"识别意图：{intent}"},
            {"state": "thinking", "intent": "识别情绪", "gesture": "观察语气", "detail": f"用户情绪：{emotion['label']}"},
            {"state": "thinking", "intent": "解析标的", "gesture": "锁定股票", "detail": f"标的：{(stock or {}).get('name') or (stock or {}).get('code') or '未识别'}"},
            {"state": "thinking", "intent": "工具路由", "gesture": "调度数据源", "detail": "启用：" + ("、".join(name for name, enabled in tool_plan.items() if enabled) or "本地规则")},
            {"state": "thinking", "intent": "并发取数", "gesture": "拉取行情", "detail": "行情、K线、情绪、板块并发获取"},
            {"state": "thinking", "intent": "风控校验", "gesture": "检查风险", "detail": f"角色：{ROLE_PROFILES[clean_role]['name']}"},
            {"state": "calm", "intent": "生成回答", "gesture": "组织预案", "detail": "输出结论、触发条件和失效条件"},
        ]
        for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            time.sleep(0.25)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/feedback")
def feedback(request: FeedbackRequest) -> dict[str, str]:
    record = {
        "time": int(time.time()),
        "session_id": request.session_id,
        "question": request.question,
        "answer": request.answer[:1000],
        "useful": request.useful,
    }
    path = ROOT / "backend" / "feedback.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    profile = SESSION_MEMORY.setdefault(request.session_id, {"turns": []})
    key = "useful_count" if request.useful else "not_useful_count"
    profile[key] = profile.get(key, 0) + 1
    save_user_profiles()
    return {"status": "ok"}


@app.get("/api/watchlist")
def get_watchlist(session_id: str = "default", role: str = "cycle") -> dict[str, Any]:
    role = role if role in ROLE_PROFILES else "cycle"
    return {"items": refresh_watchlist(session_id, role)}


@app.get("/api/market-dashboard")
def market_dashboard() -> dict[str, Any]:
    sentiment = fetch_market_sentiment()
    return {"dashboard": fetch_market_dashboard(sentiment), "mainline_rank": fetch_mainline_rank(), "source_health": get_data_source_health()}


@app.get("/api/premarket-plan")
def premarket_plan(session_id: str = "default", role: str = "cycle") -> dict[str, Any]:
    role = role if role in ROLE_PROFILES else "cycle"
    tool_results = run_parallel_tools(
        {
            "market_context": fetch_market_context,
            "market_sentiment": fetch_market_sentiment,
            "mainline_rank": fetch_mainline_rank,
        }
    )
    market_context = tool_results.get("market_context") or []
    sentiment = tool_results.get("market_sentiment")
    mainline_rank = tool_results.get("mainline_rank") or []
    profile = get_profile(session_id)
    mistake_profile = build_mistake_profile(session_id)
    plan = build_premarket_plan(market_context, sentiment, mainline_rank, profile.get("watchlist", []), mistake_profile, role)
    return {"plan": plan, "market_context": market_context, "market_sentiment": sentiment, "mainline_rank": mainline_rank, "mistake_profile": mistake_profile, "source_health": get_data_source_health()}


@app.get("/api/source-health")
def source_health() -> dict[str, Any]:
    return {"items": get_data_source_health()}


@app.get("/api/audit/latest")
def latest_audits(session_id: str = "default", limit: int = 5) -> dict[str, Any]:
    return {"items": load_latest_audits(session_id, limit)}


@app.get("/api/watchlist/alerts")
def watchlist_alerts(session_id: str = "default", role: str = "cycle") -> dict[str, Any]:
    role = role if role in ROLE_PROFILES else "cycle"
    items = refresh_watchlist(session_id, role)
    alerts = build_watchlist_alerts(items)
    return {"items": items, "alerts": alerts, "is_trading_time": is_a_share_trading_time(), "updated_at": int(time.time())}


@app.post("/api/watchlist/add")
def api_add_watchlist(request: WatchlistRequest) -> dict[str, Any]:
    stock = resolve_stock(request.question) if request.question else None
    if request.code:
        stock = {"code": request.code, "name": "", "source": "直接代码"}
    if not stock:
        return {"status": "error", "message": "未识别到股票"}
    quote = fetch_quote(stock["code"])
    analysis = analyze_quote(quote) if quote else None
    trigger = build_trigger_condition(quote, analysis, []) if quote and analysis else "放量转强再看"
    invalid = build_invalid_condition(quote, analysis) if quote and analysis else "跌破关键位移出"
    item = add_watchlist_item(request.session_id, stock, quote, trigger, invalid)
    return {"status": "ok", "item": item, "items": get_profile(request.session_id).get("watchlist", [])}


@app.post("/api/watchlist/remove")
def api_remove_watchlist(request: WatchlistRequest) -> dict[str, Any]:
    code = request.code
    if not code and request.question:
        stock = resolve_stock(request.question)
        code = stock["code"] if stock else None
    if not code:
        return {"status": "error", "message": "未识别到股票"}
    items = remove_watchlist_item(request.session_id, code)
    return {"status": "ok", "items": items}


def run_agent(question: str, session_id: str, role: str) -> dict[str, Any]:
    steps: list[dict[str, str]] = []
    intent = detect_intent(question)
    steps.append({"name": "任务识别", "status": "done", "detail": f"识别为：{intent}"})
    role_profile = ROLE_PROFILES[role]
    steps.append({"name": "角色框架", "status": "done", "detail": f"{role_profile['name']}：{role_profile['focus']}"})

    emotion = detect_user_emotion(question)
    steps.append({"name": "情绪识别", "status": "done", "detail": emotion["label"]})
    slang_notes = translate_slang(question)
    if slang_notes:
        steps.append({"name": "黑话识别", "status": "done", "detail": "；".join(slang_notes)})

    user_state = parse_user_state(question)
    if user_state:
        detail = "，".join(f"{key}={value}" for key, value in user_state.items())
        steps.append({"name": "用户状态", "status": "done", "detail": detail})

    skip_stock_resolution = should_skip_stock_resolution(question, intent)
    stock = None if skip_stock_resolution else resolve_stock(question)
    if not stock:
        stock = None if skip_stock_resolution else recall_last_stock(session_id, question)
        if stock:
            steps.append({"name": "会话记忆", "status": "done", "detail": f"沿用上次讨论标的：{stock.get('name') or stock['code']}"})
    if stock:
        steps.append({"name": "标的解析", "status": "done", "detail": f"{stock.get('name') or stock['code']} -> {stock['code']}（{stock.get('source', '未知来源')}）"})
    else:
        steps.append({"name": "标的解析", "status": "skip", "detail": "未识别到明确 A 股标的，转为通用短线框架"})

    tool_plan = build_tool_plan(question, intent, stock, user_state)
    enabled_tools = "、".join(name for name, enabled in tool_plan.items() if enabled) or "仅本地规则"
    steps.append({"name": "工具路由", "status": "done", "detail": f"启用：{enabled_tools}"})

    tool_specs = {}
    if tool_plan["market"]:
        tool_specs["market_context"] = fetch_market_context
    if tool_plan["sentiment"]:
        tool_specs["market_sentiment"] = fetch_market_sentiment
    if stock and tool_plan["quote"]:
        tool_specs["quote"] = lambda: fetch_quote(stock["code"])
    if stock and tool_plan["kline"]:
        tool_specs["kline"] = lambda: fetch_kline(stock["code"])
    if tool_plan["sector"]:
        tool_specs["sector_context"] = lambda: fetch_sector_context(stock["code"] if stock else None)

    tool_results = run_parallel_tools(tool_specs)

    market_context = tool_results.get("market_context") or []
    if market_context:
        summary = "；".join(f"{item['name']} {fmt(item.get('change_percent'))}%" for item in market_context)
        steps.append({"name": "市场环境", "status": "done", "detail": summary})
    elif tool_plan["market"]:
        steps.append({"name": "市场环境", "status": "partial", "detail": "指数行情暂不可用，降低结论强度"})
    else:
        steps.append({"name": "市场环境", "status": "skip", "detail": "当前问题不需要实时指数，跳过远程行情"})

    market_sentiment = tool_results.get("market_sentiment")
    if market_sentiment:
        steps.append({"name": "情绪温度", "status": "done", "detail": f"上涨{market_sentiment['up_count']}，下跌{market_sentiment['down_count']}，涨停{market_sentiment['limit_up_count']}，跌停{market_sentiment['limit_down_count']}，样本{market_sentiment.get('sample_count', '--')}/{market_sentiment.get('total_count', '--')}"})
    elif not tool_plan["sentiment"]:
        steps.append({"name": "情绪温度", "status": "skip", "detail": "当前问题不需要全市场情绪统计"})

    quote = tool_results.get("quote")
    if quote:
        steps.append({"name": "个股行情", "status": "done", "detail": f"{quote['name']} 现价 {fmt(quote.get('price'))}，涨跌幅 {fmt(quote.get('change_percent'))}%"})
    elif stock and tool_plan["quote"]:
        steps.append({"name": "个股行情", "status": "partial", "detail": "已识别标的，但行情源暂未返回有效数据"})
    elif stock:
        steps.append({"name": "个股行情", "status": "skip", "detail": "当前问题不需要实时个股行情"})

    analysis = analyze_quote(quote) if quote else None
    if analysis:
        steps.append({"name": "结构评估", "status": "done", "detail": f"{analysis['bias']}，风险 {analysis['risk_level']}，位置 {fmt(analysis.get('position_in_range'))}%"})

    kline = tool_results.get("kline") or []
    trend = analyze_trend(kline, quote)
    if trend:
        steps.append({"name": "趋势验证", "status": "done", "detail": f"{trend['trend_label']}，量能 {trend['volume_label']}，支撑 {fmt(trend.get('support'))}，压力 {fmt(trend.get('resistance'))}"})
    elif stock and not tool_plan["kline"]:
        steps.append({"name": "趋势验证", "status": "skip", "detail": "当前问题不需要历史K线"})

    thinking_framework = build_thinking_framework(question, intent, quote, analysis, market_context)
    steps.append({"name": "思维复刻6步", "status": "done", "detail": f"{thinking_framework['cycle']} -> {thinking_framework['question_type']} -> {thinking_framework['stock_tier']} -> {thinking_framework['conclusion']}"})

    sector_context = tool_results.get("sector_context")
    if sector_context:
        steps.append({"name": "板块主线", "status": "done", "detail": f"所属{sector_context['sector']}；板块热度{sector_context['heat_label']}；榜首{sector_context.get('top_sector', '--')}"})
    elif not tool_plan["sector"]:
        steps.append({"name": "板块主线", "status": "skip", "detail": "当前问题不需要板块主线工具"})

    experience_cases = retrieve_experience_cases(question, intent, quote, analysis, trend, market_context, market_sentiment, sector_context, user_state)
    if experience_cases:
        steps.append({"name": "游资经验匹配", "status": "done", "detail": "；".join(item["title"] for item in experience_cases[:3])})

    decision = make_decision(question, quote, analysis, market_context, user_state, role, trend, sector_context, market_sentiment, experience_cases)
    if decision:
        steps.append({"name": "规则决策", "status": "done", "detail": f"{decision['label']}，评分 {decision['score']}，仓位建议 {decision['position_limit']}"})

    decision_panel = build_decision_panel(decision, market_context, market_sentiment, sector_context, trend, user_state)

    related_knowledge = retrieve_knowledge(question, intent, stock)
    if related_knowledge:
        steps.append({"name": "知识检索", "status": "done", "detail": "；".join(item["title"] for item in related_knowledge[:3])})

    steps.append({"name": "预案生成", "status": "done", "detail": "输出明确结论、触发条件、失效条件和追问"})

    return {
        "stock": stock,
        "quote": quote,
        "analysis": analysis,
        "trend": trend,
        "market_context": market_context,
        "market_sentiment": market_sentiment,
        "sector_context": sector_context,
        "user_state": user_state,
        "decision": decision,
        "decision_panel": decision_panel,
        "related_knowledge": related_knowledge,
        "experience_cases": experience_cases,
        "thinking_framework": thinking_framework,
        "emotion": emotion,
        "slang_notes": slang_notes,
        "agent_steps": steps,
        "tool_plan": tool_plan,
    }


def should_skip_stock_resolution(question: str, intent: str) -> bool:
    if intent in {"系统能力沟通", "自然对话", "市场机会扫描", "观察池", "复盘方法"} and not re.search(r"\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b", question):
        return not any(name in question for name in LOCAL_STOCK_ALIASES)
    return False


def build_tool_plan(question: str, intent: str, stock: dict[str, str] | None, user_state: dict[str, Any]) -> dict[str, bool]:
    asks_market = bool(re.search(r"大盘|指数|市场|行情|情绪|周期|主线|板块|题材|方向|机会|买什么|买哪些|做什么|看什么|进攻|防守|退潮|赚钱效应|亏钱效应|复盘观察池", question))
    asks_trade = bool(re.search(r"买|卖|涨|跌|持有|减仓|止损|被套|成本|仓|追高|打板|半路|走势|怎么看|怎么办|后面|如何|分析|看一下|看看", question))
    asks_review = bool(re.search(r"复盘|错在哪|为什么亏|吃面|被套原因", question))
    has_stock = bool(stock)
    needs_stock_data = has_stock and (asks_trade or asks_review or "个股" in intent)
    needs_market = asks_market or needs_stock_data
    return {
        "market": needs_market,
        "sentiment": needs_market or asks_review,
        "quote": needs_stock_data,
        "kline": needs_stock_data,
        "sector": needs_stock_data or asks_market,
    }


def parse_user_state(question: str) -> dict[str, Any]:
    state: dict[str, Any] = {}
    cost_match = re.search(r"(?:成本|本钱|买入价|持仓价)\s*([0-9]+(?:\.[0-9]+)?)", question)
    if cost_match:
        state["cost"] = float(cost_match.group(1))

    if re.search(r"满仓|梭哈|all\s*in", question, re.IGNORECASE):
        state["position_level"] = 1.0
    elif re.search(r"重仓", question):
        state["position_level"] = 0.8
    elif re.search(r"半仓", question):
        state["position_level"] = 0.5

    position_match = re.search(r"([一二三四五六七八九十半0-9]+)\s*成", question)
    if position_match:
        state["position_level"] = parse_position_level(position_match.group(1))

    if re.search(r"被套|套了|亏|亏损", question):
        state["scenario"] = "被套处理"
    elif re.search(r"追高|打板|半路", question):
        state["scenario"] = "追高决策"
    elif re.search(r"买|买入|能不能进|可以进", question):
        state["scenario"] = "买入决策"
    elif re.search(r"卖|止损|割肉|减仓", question):
        state["scenario"] = "卖出风控"
    else:
        state["scenario"] = "综合分析"

    return state


def parse_position_level(value: str) -> float | None:
    mapping = {"一": 0.1, "二": 0.2, "三": 0.3, "四": 0.4, "五": 0.5, "半": 0.5, "六": 0.6, "七": 0.7, "八": 0.8, "九": 0.9, "十": 1.0}
    if value in mapping:
        return mapping[value]
    try:
        number = float(value)
        return min(number / 10, 1.0)
    except ValueError:
        return None


def detect_user_emotion(question: str) -> dict[str, str]:
    if re.search(r"你错了|说错|判断错|不对|打脸", question):
        return {"label": "纠错", "tone": "先承认可能看错，再按新数据重算"}
    if re.search(r"慌|急|怎么办|救命|崩了|亏惨|睡不着", question):
        return {"label": "焦虑", "tone": "先安抚，再给明确风控动作"}
    if re.search(r"梭哈|满仓|闭眼|干|冲|发财", question):
        return {"label": "兴奋", "tone": "降温，强调仓位纪律"}
    if re.search(r"不懂|为什么|怎么看|能解释", question):
        return {"label": "疑惑", "tone": "拆步骤，解释依据"}
    return {"label": "冷静", "tone": "直接给结论和执行条件"}


def recall_last_stock(session_id: str, question: str) -> dict[str, str] | None:
    if not re.search(r"它|这个|那|继续|还能|怎么办|要不要", question):
        return None
    memory = SESSION_MEMORY.get(session_id, {})
    return memory.get("last_stock")


def update_session_memory(session_id: str, question: str, answer: str, stock: dict[str, str] | None, quote: dict[str, Any] | None, user_state: dict[str, Any], role: str) -> None:
    memory = SESSION_MEMORY.setdefault(session_id, {"turns": []})
    if stock:
        memory["last_stock"] = {"code": stock["code"], "name": quote.get("name") if quote else stock.get("name", ""), "source": "会话记忆"}
    if user_state.get("cost"):
        memory["last_cost"] = user_state["cost"]
    if user_state.get("position_level"):
        memory["last_position_level"] = user_state["position_level"]
    if user_state.get("scenario"):
        scenarios = memory.setdefault("scenario_counts", {})
        scenarios[user_state["scenario"]] = scenarios.get(user_state["scenario"], 0) + 1
    if stock:
        watched = memory.setdefault("watched_stocks", {})
        watched[stock["code"]] = {"name": quote.get("name") if quote else stock.get("name", ""), "last_time": int(time.time())}
    memory["role"] = role
    memory["turns"].append({"question": question, "answer": compact_answer_for_memory(answer), "stock": memory.get("last_stock"), "time": int(time.time())})
    memory["turns"] = memory["turns"][-10:]
    save_user_profiles()


def compact_answer_for_memory(answer: str) -> str:
    cleaned = re.sub(r"\s+", " ", answer).strip()
    return cleaned[:260]


def build_conversation_context(session_id: str) -> list[dict[str, Any]]:
    turns = get_profile(session_id).get("turns", [])[-5:]
    context = []
    for turn in turns:
        context.append(
            {
                "user": turn.get("question", "")[:160],
                "assistant": turn.get("answer", "")[:220],
                "stock": turn.get("stock") or {},
            }
        )
    return context


def get_profile(session_id: str) -> dict[str, Any]:
    profile = SESSION_MEMORY.setdefault(session_id, {"turns": []})
    profile.setdefault("turns", [])
    profile.setdefault("watchlist", [])
    return profile


def add_watchlist_item(session_id: str, stock: dict[str, str], quote: dict[str, Any] | None, trigger: str, invalid: str) -> dict[str, Any]:
    profile = get_profile(session_id)
    items = profile.setdefault("watchlist", [])
    code = stock["code"]
    existing = next((item for item in items if item.get("code") == code), None)
    item = {
        "code": code,
        "name": quote.get("name") if quote else stock.get("name", ""),
        "price": quote.get("price") if quote else None,
        "change_percent": quote.get("change_percent") if quote else None,
        "trigger_condition": trigger,
        "invalid_condition": invalid,
        "added_at": int(time.time()) if not existing else existing.get("added_at", int(time.time())),
        "updated_at": int(time.time()),
    }
    if existing:
        existing.update(item)
        item = existing
    else:
        items.append(item)
    profile["last_stock"] = {"code": code, "name": item.get("name", ""), "source": "观察池"}
    save_user_profiles()
    return item


def remove_watchlist_item(session_id: str, code: str) -> list[dict[str, Any]]:
    profile = get_profile(session_id)
    profile["watchlist"] = [item for item in profile.get("watchlist", []) if item.get("code") != code]
    save_user_profiles()
    return profile["watchlist"]


def refresh_watchlist(session_id: str, role: str) -> list[dict[str, Any]]:
    refreshed = []
    today = time.strftime("%Y-%m-%d")
    for item in get_profile(session_id).get("watchlist", []):
        stock = {"code": item["code"], "name": item.get("name", ""), "source": "观察池"}
        quote = fetch_quote(item["code"])
        analysis = analyze_quote(quote) if quote else None
        trend = analyze_trend(fetch_kline(item["code"]), quote)
        market_context = fetch_market_context()
        market_sentiment = fetch_market_sentiment()
        sector_context = fetch_sector_context(item["code"])
        decision = make_decision("观察池复盘", quote, analysis, market_context, {}, role, trend, sector_context, market_sentiment) if quote and analysis else None
        auto_review = build_watchlist_auto_review(item, quote, analysis, decision, trend)
        refreshed.append({
            **item,
            "name": quote.get("name") if quote else item.get("name", ""),
            "price": quote.get("price") if quote else item.get("price"),
            "change_percent": quote.get("change_percent") if quote else item.get("change_percent"),
            "score": decision.get("score") if decision else None,
            "label": decision.get("label") if decision else "数据不足",
            "trigger_condition": decision.get("trigger_condition") if decision else item.get("trigger_condition"),
            "invalid_condition": decision.get("invalid_condition") if decision else item.get("invalid_condition"),
            "auto_review": auto_review,
            "last_review_date": today,
            "updated_at": int(time.time()),
        })
    get_profile(session_id)["watchlist"] = refreshed
    save_user_profiles()
    return refreshed


def build_watchlist_auto_review(item: dict[str, Any], quote: dict[str, Any] | None, analysis: dict[str, Any] | None, decision: dict[str, Any] | None, trend: dict[str, Any] | None) -> dict[str, str]:
    if not quote or not analysis or not decision:
        return {"status": "数据不足", "detail": "行情或规则评分不可用，暂不调整。"}
    pct = analysis.get("pct") or 0
    label = decision.get("label", "")
    trend_label = trend.get("trend_label") if trend else "未知趋势"
    if label in {"明确回避", "减压防守"} or pct <= -5:
        return {"status": "建议移除/降级", "detail": f"{label}，涨跌幅{fmt(pct)}%，{trend_label}，先按失效处理。"}
    if label == "可小仓试错":
        return {"status": "触发复核", "detail": "评分进入试错区，但仍要盘中确认板块、量能和承接。"}
    if label == "纳入观察":
        return {"status": "继续观察", "detail": "仍在观察区，等待触发条件，不提前动手。"}
    return {"status": "等待确认", "detail": "条件不完整，继续看触发/失效哪边先出现。"}


def build_watchlist_alerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for item in items:
        review = item.get("auto_review") or {}
        status = review.get("status", "")
        pct = item.get("change_percent")
        if status in {"触发复核", "建议移除/降级"}:
            alerts.append(
                {
                    "level": "danger" if status == "建议移除/降级" else "info",
                    "code": item.get("code"),
                    "name": item.get("name") or item.get("code"),
                    "status": status,
                    "message": review.get("detail") or "观察池状态变化",
                    "price": item.get("price"),
                    "change_percent": pct,
                    "updated_at": item.get("updated_at"),
                }
            )
        elif pct is not None and abs(pct) >= 5:
            alerts.append(
                {
                    "level": "warning",
                    "code": item.get("code"),
                    "name": item.get("name") or item.get("code"),
                    "status": "大幅波动",
                    "message": f"涨跌幅 {fmt(pct)}%，建议重新复核触发/失效条件。",
                    "price": item.get("price"),
                    "change_percent": pct,
                    "updated_at": item.get("updated_at"),
                }
            )
    return alerts[:8]


def build_premarket_plan(
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    mainline_rank: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    mistake_profile: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    temperature = compute_market_temperature(market_context, sentiment, mainline_rank)
    if temperature >= 80:
        stance = "进攻期"
        action = "可围绕主线核心做计划，但只做触发后的确认动作。"
        position = "单票不超过 3 成，总仓按纪律分散。"
    elif temperature >= 60:
        stance = "试错期"
        action = "只做前排和容量核心，后排冲高不追。"
        position = "单票 1-2 成试错，错了当天降风险。"
    elif temperature >= 40:
        stance = "混沌期"
        action = "降低频率，观察主线是否延续，不做临盘情绪单。"
        position = "轻仓观察，等待方向选择。"
    elif temperature >= 20:
        stance = "退潮防守期"
        action = "防守为主，禁止追高和补仓摊薄。"
        position = "空仓或低仓位，持仓反抽先减压。"
    else:
        stance = "冰点等待期"
        action = "不急着抄底，只等亏钱效应收敛后的修复确认。"
        position = "默认空仓等待。"

    top_lines = [item.get("name") for item in mainline_rank[:3] if item.get("name")]
    watch_actions = []
    for item in watchlist[:5]:
        watch_actions.append(
            {
                "name": item.get("name") or item.get("code"),
                "code": item.get("code"),
                "plan": f"触发：{item.get('trigger_condition') or '放量转强再看'}；失效：{item.get('invalid_condition') or '跌破关键位或板块不跟'}",
            }
        )

    blockers = build_profile_blockers(mistake_profile)
    return {
        "date": time.strftime("%Y-%m-%d"),
        "role": ROLE_PROFILES[role]["name"],
        "temperature": temperature,
        "stance": stance,
        "action": action,
        "position_rule": position,
        "market_line": judge_market_tone(market_context),
        "sentiment_line": build_sentiment_line(sentiment),
        "mainlines": top_lines,
        "watch_actions": watch_actions,
        "blockers": blockers,
        "summary": f"今日总开关：{stance}，{action} 仓位规则：{position}",
    }


def compute_market_temperature(market_context: list[dict[str, Any]], sentiment: dict[str, Any] | None, mainline_rank: list[dict[str, Any]]) -> int:
    score = 50
    if market_context:
        avg_pct = sum(item.get("change_percent") or 0 for item in market_context) / len(market_context)
        score += int(max(-20, min(20, avg_pct * 12)))
    else:
        score -= 8
    if sentiment:
        up = sentiment.get("up_count") or 0
        down = sentiment.get("down_count") or 0
        total = max(up + down, 1)
        score += int(((up - down) / total) * 20)
        score += sentiment_score_value(sentiment)
    else:
        score -= 8
    if mainline_rank:
        top_score = mainline_rank[0].get("strength_score") or 0
        score += int(max(0, min(12, top_score / 8)))
    return max(0, min(100, score))


def build_profile_blockers(mistake_profile: dict[str, Any]) -> list[str]:
    active = set(mistake_profile.get("active", []))
    blockers = []
    if "爱追高型" in active:
        blockers.append("看到拉升先问：有没有分歧承接？没有就不追。")
    if "爱补仓型" in active:
        blockers.append("亏损票先问：逻辑失效没有？失效就不补。")
    if "重仓冲动型" in active:
        blockers.append("任何新计划先限定仓位，单笔不许重仓证明判断。")
    if "题材后排型" in active:
        blockers.append("非主线、非前排、无带动性，直接降级观察。")
    if not blockers:
        blockers.append("没有稳定错因画像，今日仍按触发/失效执行，不临盘改计划。")
    return blockers[:4]


def handle_shortcut_command(question: str, session_id: str, role: str) -> dict[str, Any] | None:
    if is_watchlist_view_question(question):
        items = refresh_watchlist(session_id, role)
        return build_command_response(build_watchlist_reply(items), "观察池", "thinking", "复盘观察池", items)
    if is_watchlist_remove_question(question):
        stock = resolve_stock(question) or recall_last_stock(session_id, question)
        if not stock:
            return build_command_response("没识别到要移出的股票。请带上股票名或 6 位代码。", "观察池", "alert", "等待标的")
        items = remove_watchlist_item(session_id, stock["code"])
        return build_command_response(f"已把 {stock.get('name') or stock['code']} 移出观察池。\n\n{build_watchlist_reply(items)}", "观察池", "calm", "移出观察")
    if is_watchlist_add_question(question):
        stock = resolve_stock(question) or recall_last_stock(session_id, question)
        if not stock:
            return build_command_response("没识别到要加入观察池的股票。请带上股票名或 6 位代码。", "观察池", "alert", "等待标的")
        quote = fetch_quote(stock["code"])
        analysis = analyze_quote(quote) if quote else None
        market_context = fetch_market_context()
        trigger = build_trigger_condition(quote, analysis, market_context) if quote and analysis else "放量转强、板块同步回流后再看。"
        invalid = build_invalid_condition(quote, analysis) if quote and analysis else "板块不跟、成交萎缩或跌破关键位则失效。"
        item = add_watchlist_item(session_id, stock, quote, trigger, invalid)
        return build_command_response(build_watchlist_added_reply(item), "观察池", "thinking", "加入观察", get_profile(session_id).get("watchlist", []))
    if is_review_question(question):
        stock = resolve_stock(question) or recall_last_stock(session_id, question)
        review_text = build_review_reply(question, session_id, stock, role)
        return build_command_response(review_text, "交易复盘", "thinking", "记录复盘", get_profile(session_id).get("watchlist", []))
    return None


def build_command_response(text: str, intent: str, mood: str, gesture: str, watchlist: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sentiment = fetch_market_sentiment()
    market_context = fetch_market_context()
    market_dashboard = fetch_market_dashboard(sentiment)
    mainline_rank = fetch_mainline_rank()
    confidence = compute_answer_confidence(None, None, market_context, sentiment, None, DEFAULT_LLM_STATUS)
    return {
        "text": text,
        "intent": intent,
        "mood": mood,
        "gesture": gesture,
        "motion": "think" if mood == "thinking" else "warn" if mood == "alert" else "point",
        "quote": None,
        "analysis": None,
        "trend": None,
        "market_context": market_context,
        "market_sentiment": sentiment,
        "market_dashboard": market_dashboard,
        "mainline_rank": mainline_rank,
        "sector_context": None,
        "user_state": {},
        "decision": None,
        "decision_panel": {"score": 0, "label": intent, "items": []},
        "related_knowledge": [],
        "thinking_framework": {},
        "emotion": {"label": "冷静", "tone": "直接给结论"},
        "slang_notes": [],
        "role_profile": ROLE_PROFILES.get("cycle"),
        "agent_steps": [{"name": intent, "status": "done", "detail": "已执行自然语言快捷指令"}],
        "llm_status": DEFAULT_LLM_STATUS,
        "answer_confidence": confidence,
        "data_lineage": build_data_lineage(None, None, market_context, sentiment, None, market_dashboard, mainline_rank, confidence, DEFAULT_LLM_STATUS),
        "risk_veto": {"blocked": False, "level": "none", "reasons": [], "action": "未触发风控否决"},
        "decision_audit": None,
        "source_health": get_data_source_health(),
        "watchlist": watchlist or [],
        "audio_url": None,
        "tts_provider": "browser",
    }


def is_watchlist_add_question(question: str) -> bool:
    return bool(re.search(r"加入观察池|加到观察池|放进观察池|纳入观察池|加入自选|加自选", question))


def is_watchlist_remove_question(question: str) -> bool:
    return bool(re.search(r"移出观察池|移除观察池|从观察池删|删除观察|取消观察|移出自选|删自选", question))


def is_watchlist_view_question(question: str) -> bool:
    return bool(re.search(r"查看观察池|看看观察池|观察池|自选股|复盘观察池", question)) and not is_watchlist_add_question(question) and not is_watchlist_remove_question(question)


def is_review_question(question: str) -> bool:
    return bool(re.search(r"复盘|错在哪|错哪里|为什么亏|亏在哪|被套原因|追高了|吃面", question))


def build_watchlist_reply(items: list[dict[str, Any]]) -> str:
    if not items:
        return "观察池现在是空的。看到“纳入观察”或“等待确认”的票，可以直接说：把它加入观察池。"
    lines = ["【观察池复盘】只看触发和失效，不做幻想。"]
    for index, item in enumerate(items, 1):
        pct = item.get("change_percent")
        score = item.get("score")
        lines.extend(
            [
                f"{index}. {item.get('name') or item.get('code')}（{item.get('code')}）",
                f"现价 {fmt(item.get('price'))}，涨跌幅 {fmt(pct)}%，结论 {item.get('label', '观察')}，评分 {score if score is not None else '--'}/100。",
                f"自动复盘：{(item.get('auto_review') or {}).get('status', '待复盘')}，{(item.get('auto_review') or {}).get('detail', '')}",
                f"触发：{item.get('trigger_condition') or '放量转强再看。'}",
                f"失效：{item.get('invalid_condition') or '跌破关键位或板块不跟。'}",
            ]
        )
    lines.append("【执行纪律】只在触发条件出现时重新评估；失效条件先出现，就从观察池降级或移除。")
    return "\n".join(lines)


def build_watchlist_added_reply(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"已加入观察池：{item.get('name') or item.get('code')}（{item.get('code')}）。",
            f"现价 {fmt(item.get('price'))}，涨跌幅 {fmt(item.get('change_percent'))}%。",
            f"触发条件：{item.get('trigger_condition')}",
            f"失效条件：{item.get('invalid_condition')}",
            "记住：观察池不是买入池，只是提醒你等条件。",
        ]
    )


def build_review_reply(question: str, session_id: str, stock: dict[str, str] | None, role: str) -> str:
    user_state = parse_user_state(question)
    quote = fetch_quote(stock["code"]) if stock else None
    analysis = analyze_quote(quote) if quote else None
    trend = analyze_trend(fetch_kline(stock["code"]), quote) if stock else None
    market_context = fetch_market_context()
    market_sentiment = fetch_market_sentiment()
    sector_context = fetch_sector_context(stock["code"] if stock else None)
    decision = make_decision(question, quote, analysis, market_context, user_state, role, trend, sector_context, market_sentiment) if quote and analysis else None
    mistakes = diagnose_trade_mistakes(question, analysis, trend, market_sentiment, sector_context, user_state)
    record_trade_error(session_id, question, stock, mistakes, user_state)
    rules = build_next_time_rules(mistakes)
    target = f"{quote['name']}（{quote['code']}）" if quote else stock.get("name") or stock.get("code") if stock else "这笔交易"
    lines = [
        f"【复盘结论】{target} 的重点不是找借口，是找规则漏洞。",
        f"【可能错因】{'；'.join(mistakes)}。",
        f"【下次规则】{'；'.join(rules)}。",
    ]
    if decision:
        lines.extend(
            [
                f"【当前重算】{decision['label']}，评分 {decision['score']}/100，仓位上限 {decision['position_limit']}。",
                f"【现在失效线】{decision['invalid_condition']}",
            ]
        )
    if quote and analysis:
        lines.append(f"【行情证据】现价 {fmt(quote.get('price'))}，涨跌幅 {fmt(analysis.get('pct'))}%，结构 {analysis.get('bias')}，风险 {analysis.get('risk_level')}。")
    lines.append("【一句话】复盘不是证明自己没错，而是把下一次亏损提前挡住。")
    return "\n".join(lines)


def record_trade_error(session_id: str, question: str, stock: dict[str, str] | None, mistakes: list[str], user_state: dict[str, Any]) -> None:
    if not mistakes:
        return
    profile = get_profile(session_id)
    logs = profile.setdefault("trade_error_logs", [])
    logs.append({
        "time": int(time.time()),
        "question": question[:300],
        "stock": stock,
        "mistakes": mistakes,
        "scenario": user_state.get("scenario"),
    })
    profile["trade_error_logs"] = logs[-50:]
    profile["mistake_profile"] = build_mistake_profile_from_logs(profile["trade_error_logs"])
    save_user_profiles()


def build_mistake_profile(session_id: str) -> dict[str, Any]:
    profile = get_profile(session_id)
    stored = profile.get("mistake_profile")
    if stored:
        return stored
    return build_mistake_profile_from_logs(profile.get("trade_error_logs", []))


def build_mistake_profile_from_logs(logs: list[dict[str, Any]]) -> dict[str, Any]:
    tags = {
        "爱追高型": ["追高", "一致加速", "打板"],
        "爱补仓型": ["补仓", "摊薄"],
        "重仓冲动型": ["仓位", "重仓", "满仓", "梭哈"],
        "止损拖延型": ["止损", "失效", "扛单"],
        "题材后排型": ["主线", "杂毛", "后排"],
        "逆势幻想型": ["退潮", "逆趋势", "弱势"],
    }
    counts = {name: 0 for name in tags}
    for log in logs[-50:]:
        text = f"{log.get('question', '')}；{'；'.join(log.get('mistakes', []))}"
        for name, keywords in tags.items():
            if any(keyword in text for keyword in keywords):
                counts[name] += 1
    active = [name for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True) if count > 0]
    primary = active[0] if active else "暂无稳定错因"
    return {"primary": primary, "active": active[:3], "counts": counts, "sample_size": len(logs[-50:])}


def build_personal_risk_reminder(session_id: str, question: str) -> str:
    logs = get_profile(session_id).get("trade_error_logs", [])
    mistake_profile = build_mistake_profile(session_id)
    if not logs and mistake_profile.get("primary") == "暂无稳定错因":
        return ""
    recent = logs[-8:]
    mistake_text = "；".join("；".join(item.get("mistakes", [])) for item in recent)
    reminders = []
    if "追高" in mistake_text and re.search(r"买|进|追|打板|半路|冲", question):
        reminders.append("你最近复盘里出现过追高问题，这次先等分歧承接，不要看红盘就冲。")
    if "仓位" in mistake_text and re.search(r"仓|买|进|加", question):
        reminders.append("你最近的问题里仓位偏重，试错前先把单笔风险压下来。")
    if "补仓" in mistake_text and re.search(r"补仓|加仓|摊薄|被套", question):
        reminders.append("你最近复盘过补仓摊薄问题，亏损票先看逻辑是否失效，不用补仓证明自己。")
    active = set(mistake_profile.get("active", []))
    if "爱追高型" in active and re.search(r"买|进|追|打板|半路|冲|龙头", question):
        reminders.append("错因画像显示你偏爱追高，这次必须先等分歧承接，不允许红盘情绪单。")
    if "重仓冲动型" in active and re.search(r"买|进|加|仓|满仓|梭哈", question):
        reminders.append("错因画像显示你容易仓位冲动，单笔先按试错仓，不把判断变成赌博。")
    if "爱补仓型" in active and re.search(r"补仓|加仓|摊薄|被套|亏", question):
        reminders.append("错因画像显示你容易亏损后补仓，先确认逻辑没有失效，否则只能等反抽减压。")
    if "题材后排型" in active and re.search(r"买|进|追|题材|板块|主线", question):
        reminders.append("错因画像显示你容易做后排，非主线前排不提高仓位。")
    if not reminders:
        return ""
    profile_text = "、".join(mistake_profile.get("active", []) or [mistake_profile.get("primary", "暂无稳定错因")])
    return f"【错因画像拦截】当前画像：{profile_text}。" + "；".join(dict.fromkeys(reminders))


def compute_answer_confidence(quote: dict[str, Any] | None, trend: dict[str, Any] | None, market_context: list[dict[str, Any]], sentiment: dict[str, Any] | None, sector: dict[str, Any] | None, llm_status: dict[str, str]) -> dict[str, Any]:
    score = 35
    items = []
    if quote:
        score += 20
        items.append("个股行情可用")
    else:
        items.append("个股行情缺失")
    if trend:
        score += 15
        items.append("历史K线可用")
    else:
        items.append("历史K线缺失")
    if market_context:
        score += 10
        items.append("指数数据可用")
    if sentiment and (sentiment.get("coverage") or 0) >= 0.95:
        score += 12
        items.append("情绪样本覆盖充分")
    elif sentiment:
        score += 5
        items.append("情绪样本覆盖不足")
    if sector:
        score += 8
        items.append("板块数据可用")
    if llm_status.get("status") == "ok":
        score += 5
        items.append("LLM表达正常")
    label = "高" if score >= 82 else "中" if score >= 60 else "低"
    return {"score": min(score, 100), "label": label, "items": items, "is_trading_time": is_a_share_trading_time(), "data_source": "腾讯/新浪/东方财富公开数据"}


def evaluate_risk_veto(
    question: str,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    sector: dict[str, Any] | None,
    user_state: dict[str, Any],
) -> dict[str, Any]:
    reasons = []
    level = "none"
    pct = analysis.get("pct") if analysis else None
    position = analysis.get("position_in_range") if analysis else None
    if not quote and re.search(r"买|进|加仓|满仓|梭哈|打板|半路", question):
        reasons.append("个股行情缺失，禁止给进攻结论")
    if sentiment and sentiment.get("sentiment_label") == "退潮" and re.search(r"买|进|加仓|追|打板|半路|梭哈", question):
        reasons.append("市场情绪退潮，进攻动作一票否决")
    if user_state.get("position_level") and user_state["position_level"] >= 0.7:
        reasons.append("用户仓位已过重，禁止继续提高风险暴露")
    if re.search(r"满仓|梭哈| all in |重仓", question, re.IGNORECASE):
        reasons.append("出现满仓/梭哈倾向，触发强制降温")
    if user_state.get("scenario") == "被套处理" and re.search(r"补仓|加仓|摊薄|再买", question):
        reasons.append("被套场景下补仓摊薄，一票否决")
    if pct is not None and pct <= -7 and re.search(r"买|进|抄底|补仓|加仓", question):
        reasons.append("个股接近极端杀跌，禁止冲动抄底")
    if trend and trend.get("trend_label") == "空头趋势" and re.search(r"买|进|补仓|加仓|抄底", question):
        reasons.append("K线为空头趋势，逆势进攻被否决")
    if sector and sector.get("heat_label") in {"非主线", "未知"} and re.search(r"追|打板|半路|重仓|梭哈", question):
        reasons.append("标的不在清晰主线，禁止高风险追击")
    if position is not None and position <= 20 and pct is not None and pct < 0 and re.search(r"买|进|补仓|加仓", question):
        reasons.append("价格贴近日内低位且下跌，抛压未释放")

    if any("满仓" in item or "梭哈" in item or "一票否决" in item for item in reasons):
        level = "hard"
    elif reasons:
        level = "soft"
    return {
        "blocked": bool(reasons),
        "level": level,
        "reasons": reasons,
        "action": "风控一票否决：不允许买入/加仓/追高，只能等待或降风险。" if reasons else "未触发风控否决",
    }


def apply_risk_veto(decision: dict[str, Any], veto: dict[str, Any]) -> dict[str, Any]:
    updated = decision.copy()
    updated["pre_veto_label"] = decision.get("label")
    updated["pre_veto_score"] = decision.get("score")
    updated["label"] = "风控否决"
    updated["score"] = min(decision.get("score", 0), 30 if veto.get("level") == "hard" else 40)
    updated["position_limit"] = "0 成"
    updated["action"] = veto.get("action") or "风控否决，不执行进攻动作。"
    updated.setdefault("warnings", [])
    updated["warnings"] = list(dict.fromkeys(updated["warnings"] + veto.get("reasons", [])))
    return updated


def build_data_lineage(
    quote: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    sector: dict[str, Any] | None,
    dashboard: dict[str, Any] | None,
    mainline_rank: list[dict[str, Any]],
    confidence: dict[str, Any],
    llm_status: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        build_lineage_item("个股实时行情", quote.get("source") if quote else None, "公开行情", bool(quote), "原始行情字段", None),
        build_lineage_item("历史K线/趋势", "东方财富历史K线" if trend else None, "公开行情", bool(trend), "系统计算MA/支撑/压力/趋势", None),
        build_lineage_item("指数环境", "腾讯指数行情" if market_context else None, "公开行情", bool(market_context), "系统计算指数环境评分", len(market_context) if market_context else 0),
        build_lineage_item("市场情绪", sentiment.get("source") if sentiment else None, "公开行情+系统计算", bool(sentiment), "分页统计涨跌、涨跌停、炸板率、连板高度", sentiment.get("coverage") if sentiment else None),
        build_lineage_item("板块/主线", "东方财富板块榜" if sector or mainline_rank else None, "公开行情+系统计算", bool(sector or mainline_rank), "系统计算主线强度和持续天数", len(mainline_rank)),
        build_lineage_item("回答置信度", "本地Agent规则", "系统计算", True, f"数据完整度评分={confidence.get('score')}", None),
        build_lineage_item("LLM表达", llm_status.get("provider"), "模型生成", llm_status.get("status") == "ok", "只润色表达，不决定买卖", None),
    ]


def build_lineage_item(name: str, source: str | None, source_type: str, available: bool, method: str, coverage: Any) -> dict[str, Any]:
    return {
        "name": name,
        "source": source or "不可用",
        "source_type": source_type,
        "available": available,
        "method": method,
        "coverage": coverage,
        "official": source_type == "交易所官方",
        "updated_at": int(time.time()),
    }


def build_decision_audit(
    session_id: str,
    question: str,
    role: str,
    stock: dict[str, str] | None,
    original_decision: dict[str, Any] | None,
    final_decision: dict[str, Any] | None,
    risk_veto: dict[str, Any],
    confidence: dict[str, Any],
    data_lineage: list[dict[str, Any]],
    agent_steps: list[dict[str, str]],
) -> dict[str, Any]:
    audit_id = f"audit-{int(time.time())}-{abs(hash(session_id + question)) % 1000000}"
    return {
        "id": audit_id,
        "time": int(time.time()),
        "session_id": session_id,
        "question": question[:300],
        "role": role,
        "stock": stock,
        "original_label": original_decision.get("label") if original_decision else None,
        "original_score": original_decision.get("score") if original_decision else None,
        "final_label": final_decision.get("label") if final_decision else "数据不足",
        "final_score": final_decision.get("score") if final_decision else 0,
        "position_limit": final_decision.get("position_limit") if final_decision else "0 成",
        "risk_veto": risk_veto,
        "confidence": confidence,
        "data_lineage": data_lineage,
        "evidence": build_audit_evidence(final_decision, agent_steps),
    }


def build_audit_evidence(decision: dict[str, Any] | None, agent_steps: list[dict[str, str]]) -> list[str]:
    evidence = []
    if decision:
        evidence.extend(decision.get("reasons", [])[:4])
        evidence.extend(decision.get("warnings", [])[:4])
    evidence.extend(f"{step['name']}：{step['detail']}" for step in agent_steps[-4:])
    return evidence[:10]


def persist_decision_audit(audit: dict[str, Any]) -> None:
    path = ROOT / "backend" / "decision_audit.jsonl"
    record = audit.copy()
    record["data_lineage"] = audit.get("data_lineage", [])[:8]
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_latest_audits(session_id: str, limit: int) -> list[dict[str, Any]]:
    path = ROOT / "backend" / "decision_audit.jsonl"
    if not path.exists():
        return []
    limit = max(1, min(limit, 20))
    result = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if session_id and item.get("session_id") != session_id:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def is_a_share_trading_time() -> bool:
    now = time.localtime()
    if now.tm_wday >= 5:
        return False
    minutes = now.tm_hour * 60 + now.tm_min
    return 9 * 60 + 30 <= minutes <= 11 * 60 + 30 or 13 * 60 <= minutes <= 15 * 60


def diagnose_trade_mistakes(question: str, analysis: dict[str, Any] | None, trend: dict[str, Any] | None, sentiment: dict[str, Any] | None, sector: dict[str, Any] | None, user_state: dict[str, Any]) -> list[str]:
    mistakes = []
    if re.search(r"追高|冲进去|打板|半路", question):
        mistakes.append("追高，没有等分歧承接")
    if re.search(r"补仓|加仓|摊薄", question):
        mistakes.append("用补仓替代止损，扩大了错误")
    if user_state.get("position_level") and user_state["position_level"] >= 0.5:
        mistakes.append("仓位过重，容错率不够")
    if re.search(r"没止损|扛|拿着|舍不得", question):
        mistakes.append("没有提前写失效条件")
    if sentiment and sentiment.get("sentiment_label") == "退潮":
        mistakes.append("退潮期还按进攻节奏做")
    if sector and sector.get("heat_label") in {"非主线", "未知"}:
        mistakes.append("标的不在主线，容易变成杂毛交易")
    if analysis and (analysis.get("pct") or 0) <= -3:
        mistakes.append("走弱后没有先降风险")
    if trend and trend.get("trend_label") in {"空头趋势", "弱势修复"}:
        mistakes.append("逆趋势博修复，赔率不够")
    if not mistakes:
        mistakes.append("交易前预案不够清晰，触发和失效没有量化")
    return list(dict.fromkeys(mistakes))[:5]


def build_next_time_rules(mistakes: list[str]) -> list[str]:
    rules = []
    joined = "；".join(mistakes)
    if "追高" in joined:
        rules.append("一致加速不追，只等分歧承接或弱转强确认")
    if "补仓" in joined:
        rules.append("亏损票不补仓摊薄，先看逻辑是否失效")
    if "仓位" in joined:
        rules.append("单笔试错先压到 1-2 成，错了还能处理")
    if "退潮" in joined:
        rules.append("退潮期默认防守，减少交易频率")
    if "主线" in joined or "杂毛" in joined:
        rules.append("非主线、非前排、无带动性的票不碰")
    rules.append("每笔交易前写清触发条件、失效条件、仓位上限")
    return list(dict.fromkeys(rules))[:5]


def retrieve_knowledge(question: str, intent: str, stock: dict[str, str] | None) -> list[dict[str, str]]:
    words = set(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", question + " " + intent))
    scored = []
    for item in KNOWLEDGE_BASE:
        tags = set(item.get("tags", []))
        score = len(words & tags)
        for tag in tags:
            if tag in question:
                score += 2
        if stock and any(tag in question for tag in ["被套", "买入", "追高", "止损"]):
            if "散户" in tags or "仓位" in tags or "被套" in tags:
                score += 1
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:3]]


def retrieve_experience_cases(
    question: str,
    intent: str,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    sector_context: dict[str, Any] | None,
    user_state: dict[str, Any],
) -> list[dict[str, Any]]:
    cycle = classify_market_cycle(market_context, analysis)
    text = " ".join(
        [
            question,
            intent,
            cycle,
            user_state.get("scenario", ""),
            analysis.get("bias", "") if analysis else "",
            analysis.get("risk_level", "") if analysis else "",
            trend.get("trend_label", "") if trend else "",
            sentiment.get("sentiment_label", "") if sentiment else "",
            sector_context.get("heat_label", "") if sector_context else "",
        ]
    )
    scored: list[tuple[int, dict[str, Any]]] = []
    for case in TRADER_EXPERIENCE_CASES:
        score = 0
        for tag in case.get("tags", []):
            if tag and tag in text:
                score += 2
        if case.get("scenario") and case["scenario"] in text:
            score += 3
        score += score_experience_by_market_state(case, analysis, sentiment, sector_context, user_state)
        if score > 0:
            scored.append((score, case))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [case for _, case in scored[:3]]


def score_experience_by_market_state(case: dict[str, Any], analysis: dict[str, Any] | None, sentiment: dict[str, Any] | None, sector_context: dict[str, Any] | None, user_state: dict[str, Any]) -> int:
    title = case.get("title", "")
    pct = analysis.get("pct") if analysis else None
    position = analysis.get("position_in_range") if analysis else None
    score = 0
    if sentiment and sentiment.get("sentiment_label") == "退潮" and "退潮" in title:
        score += 5
    if user_state.get("scenario") == "被套处理" and "被套" in title:
        score += 5
    if sector_context and sector_context.get("heat_label") in {"非主线", "未知"} and "跟风" in title:
        score += 3
    if pct is not None and pct >= 5 and "高潮" in title:
        score += 2
    if pct is not None and 1 <= pct <= 7 and position is not None and position >= 60 and "主线核心" in title:
        score += 2
    return score


def translate_slang(question: str) -> list[str]:
    return [f"{word}={meaning}" for word, meaning in SLANG_DICT.items() if word in question]


def resolve_stock(question: str) -> dict[str, str] | None:
    code_match = re.search(r"\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b", question)
    if code_match:
        return {"code": code_match.group(1), "name": "", "source": "直接代码"}

    for name, code in LOCAL_STOCK_ALIASES.items():
        if name in question:
            return {"code": code, "name": name, "source": "本地名称表"}

    for keyword in extract_stock_keywords(question):
        try:
            stock = search_sina_stock(keyword)
            if stock:
                return stock
        except Exception:
            continue
    return None


def extract_stock_keyword(question: str) -> str | None:
    keywords = extract_stock_keywords(question)
    return keywords[0] if keywords else None


def extract_stock_keywords(question: str) -> list[str]:
    cleaned = re.sub(r"[，。！？,.?!；;：:\s]", " ", question)
    cleaned = re.sub(
        r"五一|节后|过后|之后|以前|现在|今天|明天|后面|当前|这个|那个|这只|那只|股票|个股|标的|帮我|帮忙|看一下|看看|分析|研究|一下|请问|老师|数字人|会涨吗|会跌吗|可以买入吗|可以买|买入|卖出|持有|加仓|减仓|止损|被套|成本|仓位|如何|怎么样|怎么办|能不能|可不可以|要不要|适不适合|后面|走势|判断",
        " ",
        cleaned,
    )
    raw_candidates = re.findall(r"[\u4e00-\u9fa5A-Za-z]{2,12}", cleaned)
    candidates: list[str] = []
    for raw in raw_candidates:
        if raw in candidates:
            continue
        candidates.append(raw)
        if re.search(r"[\u4e00-\u9fa5]", raw) and len(raw) > 4:
            for size in range(min(6, len(raw)), 1, -1):
                for start in range(0, len(raw) - size + 1):
                    piece = raw[start:start + size]
                    if piece not in candidates:
                        candidates.append(piece)
    candidates.sort(key=lambda item: (len(item), item in LOCAL_STOCK_ALIASES), reverse=True)
    return candidates[:12]


def search_sina_stock(keyword: str) -> dict[str, str] | None:
    url = "https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key=" + urllib.parse.quote(keyword) + "&name=suggestdata"
    raw = http_get(url, encoding="gbk")
    match = re.search(r'var suggestdata="(.*)";', raw)
    if not match or not match.group(1):
        return None

    best: dict[str, str] | None = None
    for item in match.group(1).split(";"):
        fields = item.split(",")
        code = next((field for field in fields if re.match(r"^(00|30|60|68)\d{4}$", field)), None)
        if not code:
            continue
        name = next((field for field in fields if re.search(r"[\u4e00-\u9fa5]", field)), keyword)
        candidate = {"code": code, "name": name, "source": f"新浪名称搜索:{keyword}"}
        if keyword in name or name in keyword:
            return candidate
        best = best or candidate

    return best


def fetch_quote(code: str) -> dict[str, Any] | None:
    cached = cache_get(f"quote:{code}")
    if cached:
        return cached
    for fetcher in (fetch_tencent_quote, fetch_sina_quote):
        try:
            quote = fetcher(code)
            if quote and quote.get("price"):
                return cache_set(f"quote:{code}", quote, 12)
        except Exception:
            continue

    return None


def fetch_market_context() -> list[dict[str, Any]]:
    cached = cache_get("market_context")
    if cached:
        return cached
    indices = [("000001", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]
    result = []
    for code, fallback_name in indices:
        try:
            quote = fetch_index_quote(code, fallback_name)
            if quote:
                result.append(quote)
        except Exception:
            continue
    return cache_set("market_context", result, 60)


def fetch_market_sentiment() -> dict[str, Any] | None:
    cached = cache_get("market_sentiment")
    if cached:
        return cached
    try:
        first_page = fetch_market_sentiment_page(1)
        total = first_page.get("total") or 0
        rows = first_page.get("rows") or []
        page_size = 100
        page_count = min((total + page_size - 1) // page_size, 80)
        if page_count > 1:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(fetch_market_sentiment_page, page) for page in range(2, page_count + 1)]
                for future in as_completed(futures):
                    rows.extend(future.result().get("rows") or [])
    except Exception:
        return None
    up = down = limit_up = limit_down = fried_board = 0
    limit_rows = []
    for row in rows:
        pct = row.get("f3")
        code = str(row.get("f12") or "")
        high = row.get("f15")
        if pct is None or pct == "-":
            continue
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        limit_threshold = 19.5 if code.startswith(("30", "68")) else 9.8
        if pct >= limit_threshold:
            limit_up += 1
            limit_rows.append(row)
        if pct <= -limit_threshold:
            limit_down += 1
        if high not in (None, "-") and high >= limit_threshold and pct < limit_threshold:
            fried_board += 1
    ratio = up / max(down, 1)
    if limit_down >= 20 or ratio < 0.6:
        label = "退潮"
    elif limit_up >= 60 and ratio > 1.5:
        label = "发酵"
    elif ratio > 1.1:
        label = "回暖"
    else:
        label = "混沌"
    sample_count = len(rows)
    return cache_set(
        "market_sentiment",
        {
            "up_count": up,
            "down_count": down,
            "flat_count": max(sample_count - up - down, 0),
            "limit_up_count": limit_up,
            "limit_down_count": limit_down,
            "fried_board_count": fried_board,
            "fried_board_rate": round(fried_board / max(fried_board + limit_up, 1), 3),
            "limit_height": estimate_limit_height(limit_rows),
            "yesterday_limit_performance": estimate_yesterday_limit_performance(rows),
            "up_down_ratio": ratio,
            "sentiment_label": label,
            "sample_count": sample_count,
            "total_count": total or sample_count,
            "coverage": round(sample_count / max(total or sample_count, 1), 3),
            "source": "东方财富沪深京A股分页统计",
            "updated_at": int(time.time()),
        },
        60,
    )


def fetch_market_sentiment_page(page: int) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "pn": str(page),
            "pz": "100",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f12,f14,f15",
        }
    )
    data = json.loads(http_get("https://push2.eastmoney.com/api/qt/clist/get?" + params))
    payload = data.get("data") or {}
    return {"total": payload.get("total") or 0, "rows": payload.get("diff") or []}


def estimate_limit_height(limit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not limit_rows:
        return {"height": 0, "leaders": []}
    leaders = []
    max_height = 1
    for row in limit_rows[:80]:
        code = str(row.get("f12") or "")
        height = count_consecutive_limit_days(code)
        max_height = max(max_height, height)
        if height >= max_height:
            leaders.append({"code": code, "name": row.get("f14"), "height": height})
    leaders.sort(key=lambda item: item["height"], reverse=True)
    return {"height": max_height, "leaders": leaders[:5]}


def count_consecutive_limit_days(code: str) -> int:
    kline = fetch_kline(code, limit=8)
    if not kline:
        return 1
    height = 0
    for item in reversed(kline):
        pct = item.get("pct")
        threshold = 19.5 if code.startswith(("30", "68")) else 9.8
        if pct is not None and pct >= threshold:
            height += 1
        else:
            break
    return max(height, 1)


def estimate_yesterday_limit_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cached = cache_get("yesterday_limit_performance")
    if cached:
        return cached
    candidates = rows[:260]
    matched = []
    for row in candidates:
        code = str(row.get("f12") or "")
        kline = fetch_kline(code, limit=3)
        if len(kline) < 2:
            continue
        threshold = 19.5 if code.startswith(("30", "68")) else 9.8
        if (kline[-2].get("pct") or 0) >= threshold:
            matched.append(row)
    if not matched:
        result = {"sample_count": 0, "avg_pct": None, "red_rate": None, "note": "样本不足，昨日涨停表现暂不可用"}
    else:
        avg_pct = sum(row.get("f3") or 0 for row in matched) / len(matched)
        red_rate = sum(1 for row in matched if (row.get("f3") or 0) > 0) / len(matched)
        result = {"sample_count": len(matched), "avg_pct": round(avg_pct, 2), "red_rate": round(red_rate, 3), "note": "基于当前列表前260只活跃样本估算"}
    return cache_set("yesterday_limit_performance", result, 600)


def fetch_market_dashboard(sentiment: dict[str, Any] | None = None) -> dict[str, Any] | None:
    sentiment = sentiment or fetch_market_sentiment()
    if not sentiment:
        return None
    height = sentiment.get("limit_height") or {}
    yesterday = sentiment.get("yesterday_limit_performance") or {}
    return {
        "label": sentiment.get("sentiment_label"),
        "up_count": sentiment.get("up_count"),
        "down_count": sentiment.get("down_count"),
        "flat_count": sentiment.get("flat_count"),
        "limit_up_count": sentiment.get("limit_up_count"),
        "limit_down_count": sentiment.get("limit_down_count"),
        "fried_board_count": sentiment.get("fried_board_count"),
        "fried_board_rate": sentiment.get("fried_board_rate"),
        "limit_height": height.get("height", 0),
        "limit_height_leaders": height.get("leaders", []),
        "yesterday_limit_performance": yesterday,
        "sample_count": sentiment.get("sample_count"),
        "total_count": sentiment.get("total_count"),
        "coverage": sentiment.get("coverage"),
        "source": sentiment.get("source"),
        "updated_at": sentiment.get("updated_at"),
    }


def fetch_sector_context(code: str | None) -> dict[str, Any] | None:
    sector = STOCK_SECTOR_ALIASES.get(code or "", "未知板块")
    ranks = fetch_sector_rank()
    top_sector = ranks[0]["name"] if ranks else "--"
    matched = next((item for item in ranks if sector != "未知板块" and sector in item["name"]), None)
    rank = matched["rank"] if matched else None
    pct = matched["pct"] if matched else None
    if rank is not None and rank <= 10:
        heat = "主线/强势"
    elif rank is not None and rank <= 30:
        heat = "支线活跃"
    elif sector == "未知板块":
        heat = "未知"
    else:
        heat = "非主线"
    return {"sector": sector, "rank": rank, "pct": pct, "heat_label": heat, "top_sector": top_sector, "top_sectors": ranks[:5]}


def fetch_sector_rank() -> list[dict[str, Any]]:
    cached = cache_get("sector_rank")
    if cached:
        return cached
    params = urllib.parse.urlencode(
        {
            "pn": "1",
            "pz": "50",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f3,f12,f14,f62,f6,f104,f105,f128,f136",
        }
    )
    try:
        data = json.loads(http_get("https://push2.eastmoney.com/api/qt/clist/get?" + params))
        rows = data.get("data", {}).get("diff") or []
    except Exception:
        return []
    return cache_set("sector_rank", [
        {"rank": index + 1, "code": row.get("f12"), "name": row.get("f14"), "pct": row.get("f3"), "main_flow": row.get("f62"), "amount": row.get("f6"), "limit_up_count": row.get("f104"), "limit_down_count": row.get("f105"), "leader": row.get("f128"), "leader_pct": row.get("f136")}
        for index, row in enumerate(rows)
    ], 180)


def fetch_mainline_rank() -> list[dict[str, Any]]:
    cached = cache_get("mainline_rank")
    if cached:
        return cached
    ranks = fetch_sector_rank()[:30]
    history = load_sector_history()
    today = time.strftime("%Y-%m-%d")
    result = []
    for row in ranks:
        code = row.get("code") or row.get("name")
        prev = history.get(code, {})
        prev_days = prev.get("active_days", 0) if prev.get("last_date") != today else max(prev.get("active_days", 1) - 1, 0)
        active = (row.get("pct") or 0) > 1.5 or (row.get("limit_up_count") or 0) > 0
        active_days = prev_days + 1 if active else 0
        history[code] = {"last_date": today, "active_days": active_days}
        strength = round((row.get("pct") or 0) * 3 + min((row.get("amount") or 0) / 100000000, 30) + (row.get("limit_up_count") or 0) * 4 + active_days * 3, 2)
        result.append({**row, "active_days": active_days, "strength_score": strength})
    save_sector_history(history)
    result.sort(key=lambda item: item.get("strength_score") or 0, reverse=True)
    return cache_set("mainline_rank", result[:10], 180)


def load_sector_history() -> dict[str, Any]:
    path = ROOT / "backend" / "sector_history.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_sector_history(data: dict[str, Any]) -> None:
    path = ROOT / "backend" / "sector_history.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_kline(code: str, limit: int = 40) -> list[dict[str, Any]]:
    cached = cache_get(f"kline:{code}:{limit}")
    if cached:
        return cached
    secid = ("1." if code.startswith("6") else "0.") + code
    params = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(limit),
        }
    )
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + params
    try:
        data = json.loads(http_get(url, encoding="utf-8"))
        klines = data.get("data", {}).get("klines") or []
    except Exception:
        return []

    result = []
    for item in klines:
        fields = item.split(",")
        if len(fields) < 7:
            continue
        result.append(
            {
                "date": fields[0],
                "open": to_float(fields[1]),
                "close": to_float(fields[2]),
                "high": to_float(fields[3]),
                "low": to_float(fields[4]),
                "volume": to_float(fields[5]),
                "amount": to_float(fields[6]),
                "pct": to_float(fields[8]) if len(fields) > 8 else None,
            }
        )
    return cache_set(f"kline:{code}:{limit}", result, 900)


def analyze_trend(kline: list[dict[str, Any]], quote: dict[str, Any] | None) -> dict[str, Any] | None:
    if len(kline) < 10:
        return None

    closes = [item["close"] for item in kline if item.get("close") is not None]
    volumes = [item["volume"] for item in kline if item.get("volume") is not None]
    if len(closes) < 10:
        return None

    price = quote.get("price") if quote else closes[-1]
    ma5 = average(closes[-5:])
    ma10 = average(closes[-10:])
    ma20 = average(closes[-20:]) if len(closes) >= 20 else None
    recent_high = max(item["high"] for item in kline[-20:] if item.get("high") is not None)
    recent_low = min(item["low"] for item in kline[-20:] if item.get("low") is not None)
    support = max([value for value in [recent_low, ma10, ma20] if value is not None and value <= price], default=recent_low)
    resistance = min([value for value in [recent_high, ma5, ma10, ma20] if value is not None and value >= price], default=recent_high)
    volume_ratio = None
    if len(volumes) >= 6 and average(volumes[-6:-1]):
        volume_ratio = volumes[-1] / average(volumes[-6:-1])

    trend_score = 0
    if price > ma5:
        trend_score += 12
    else:
        trend_score -= 10
    if price > ma10:
        trend_score += 10
    else:
        trend_score -= 10
    if ma20:
        if price > ma20:
            trend_score += 8
        else:
            trend_score -= 8
    if ma5 > ma10:
        trend_score += 8
    else:
        trend_score -= 6
    if volume_ratio and volume_ratio >= 1.4 and price >= closes[-2]:
        trend_score += 8
    elif volume_ratio and volume_ratio >= 1.6 and price < closes[-2]:
        trend_score -= 10

    if trend_score >= 28:
        trend_label = "多头趋势"
    elif trend_score >= 12:
        trend_label = "转强观察"
    elif trend_score <= -24:
        trend_label = "空头趋势"
    elif trend_score <= -10:
        trend_label = "弱势修复"
    else:
        trend_label = "震荡趋势"

    volume_label = "放量承接" if volume_ratio and volume_ratio >= 1.3 and price >= closes[-2] else "放量分歧" if volume_ratio and volume_ratio >= 1.3 else "量能一般"
    scenario = build_scenario(price, support, resistance, trend_label)
    confidence = min(85, max(35, 55 + trend_score // 2))

    return {
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "support": support,
        "resistance": resistance,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "volume_ratio": volume_ratio,
        "trend_score": trend_score,
        "trend_label": trend_label,
        "volume_label": volume_label,
        "scenario": scenario,
        "confidence": confidence,
    }


def average(values: list[float]) -> float | None:
    valid = [value for value in values if value is not None]
    return sum(valid) / len(valid) if valid else None


def build_scenario(price: float, support: float | None, resistance: float | None, trend_label: str) -> dict[str, str]:
    up = f"放量站上压力 {fmt(resistance)}，才算转强确认。" if resistance else "放量突破前高才算转强。"
    down = f"跌破支撑 {fmt(support)}，弱势延续，先防守。" if support else "跌破近端低点，先防守。"
    base = "不突破不追，不破位不恐慌，按条件走。" if "震荡" in trend_label else "顺趋势处理，别逆势幻想。"
    return {"bull": up, "bear": down, "base": base}


def fetch_index_quote(code: str, fallback_name: str) -> dict[str, Any] | None:
    symbol = ("sh" if code.startswith("000") else "sz") + code
    raw = http_get(f"https://qt.gtimg.cn/q={symbol}", encoding="gbk")
    if '="' not in raw:
        return None
    data = raw.split('="', 1)[1].rsplit('"', 1)[0]
    fields = data.split("~")
    if len(fields) < 6:
        return None
    price = to_float(fields[3])
    previous_close = to_float(fields[4])
    change = price - previous_close if price is not None and previous_close else to_float(fields[31] if len(fields) > 31 else None)
    change_percent = (change / previous_close * 100) if previous_close else to_float(fields[32] if len(fields) > 32 else None)
    return {
        "name": fields[1] or fallback_name,
        "code": code,
        "price": price,
        "change": change,
        "change_percent": change_percent,
        "source": "腾讯指数",
    }


def fetch_tencent_quote(code: str) -> dict[str, Any]:
    symbol = ("sh" if code.startswith("6") else "sz") + code
    raw = http_get(f"https://qt.gtimg.cn/q={symbol}", encoding="gbk")
    data = raw.split('="', 1)[1].rsplit('"', 1)[0]
    fields = data.split("~")
    price = to_float(fields[3])
    previous_close = to_float(fields[4])
    change = price - previous_close if price is not None and previous_close else to_float(fields[31])
    change_percent = (change / previous_close * 100) if previous_close else to_float(fields[32])
    return {
        "name": fields[1],
        "code": fields[2] or code,
        "price": price,
        "previous_close": previous_close,
        "open": to_float(fields[5]),
        "high": to_float(fields[33]),
        "low": to_float(fields[34]),
        "amount_wan": to_float(fields[37]),
        "change": change,
        "change_percent": change_percent,
        "time": fields[30],
        "source": "腾讯行情",
    }


def fetch_sina_quote(code: str) -> dict[str, Any]:
    symbol = ("sh" if code.startswith("6") else "sz") + code
    raw = http_get(f"https://hq.sinajs.cn/list={symbol}", encoding="gbk")
    data = raw.split('="', 1)[1].rsplit('"', 1)[0]
    fields = data.split(",")
    price = to_float(fields[3])
    previous_close = to_float(fields[2])
    change = price - previous_close if price is not None and previous_close else None
    change_percent = (change / previous_close * 100) if previous_close else None
    return {
        "name": fields[0],
        "code": code,
        "price": price,
        "previous_close": previous_close,
        "open": to_float(fields[1]),
        "high": to_float(fields[4]),
        "low": to_float(fields[5]),
        "amount_wan": to_float(fields[9]) / 10000 if to_float(fields[9]) else None,
        "change": change,
        "change_percent": change_percent,
        "time": f"{fields[30]} {fields[31]}" if len(fields) > 31 else "--",
        "source": "新浪行情",
    }


def analyze_quote(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    if not quote:
        return None

    previous_close = quote.get("previous_close")
    price = quote.get("price")
    pct = quote.get("change_percent")
    if pct is None and previous_close and price:
        pct = (price - previous_close) / previous_close * 100
    pct = pct or 0
    high = quote.get("high")
    low = quote.get("low")
    amplitude = ((high - low) / previous_close * 100) if high and low and previous_close else None
    position = ((price - low) / (high - low) * 100) if price and high and low and high != low else None
    bias = "强势" if pct >= 5 else "偏强" if pct >= 1.5 else "弱势" if pct <= -5 else "偏弱" if pct <= -1.5 else "震荡"
    risk = "高" if abs(pct) >= 7 or (amplitude and amplitude >= 9) else "中" if abs(pct) >= 3 or (amplitude and amplitude >= 5) else "低到中"
    return {
        "pct": pct,
        "amplitude": amplitude,
        "position_in_range": position,
        "bias": bias,
        "risk_level": risk,
    }


def build_thinking_framework(
    question: str,
    intent: str,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle = classify_market_cycle(market_context, analysis)
    question_type = classify_question_type(question, intent)
    stock_tier = classify_stock_tier(quote, analysis, cycle)
    dimensions = analyze_dimensions(quote, analysis, market_context)
    conclusion = derive_framework_conclusion(cycle, stock_tier, dimensions, analysis)
    language = select_language_bias(conclusion, cycle)
    return {
        "rules": COGNITIVE_RULES,
        "cycle": cycle,
        "question_type": question_type,
        "stock_tier": stock_tier,
        "dimensions": dimensions,
        "conclusion": conclusion,
        "language": language,
    }


def classify_market_cycle(market_context: list[dict[str, Any]], analysis: dict[str, Any] | None) -> str:
    if not market_context:
        return "未知周期"
    avg_pct = sum(item.get("change_percent") or 0 for item in market_context) / len(market_context)
    stock_pct = analysis.get("pct") if analysis else 0
    if avg_pct <= -1 or (stock_pct is not None and stock_pct <= -7):
        return "退潮/冰点"
    if avg_pct >= 1 and stock_pct is not None and stock_pct >= 5:
        return "发酵/高潮"
    if avg_pct >= 0.2:
        return "回暖"
    if avg_pct <= -0.2:
        return "弱分歧"
    return "震荡混沌"


def classify_question_type(question: str, intent: str) -> str:
    if re.search(r"大盘|指数|市场|行情|进攻|防守", question):
        return "大盘/市场问题"
    if re.search(r"板块|题材|主线|方向", question):
        return "板块/题材问题"
    if re.search(r"买|卖|涨|跌|持有|减仓|止损|被套|成本|仓", question):
        return "个股/交易决策问题"
    if re.search(r"打板|低吸|半路|龙头|手法", question):
        return "交易手法问题"
    if re.search(r"慌|亏|心态|怎么办", question):
        return "心态/风控问题"
    return intent


def classify_stock_tier(quote: dict[str, Any] | None, analysis: dict[str, Any] | None, cycle: str) -> str:
    if not quote or not analysis:
        return "未定位"
    pct = analysis.get("pct") or 0
    amount = quote.get("amount_wan") or 0
    position = analysis.get("position_in_range")
    if pct >= 7 and amount >= 100000 and position is not None and position >= 70:
        return "疑似核心/前排"
    if pct >= 3 and amount >= 50000:
        return "板块前排观察"
    if pct <= -7:
        return "风险样本/掉队"
    if pct < 0 and position is not None and position <= 35:
        return "弱势跟风"
    return "普通观察标的"


def analyze_dimensions(quote: dict[str, Any] | None, analysis: dict[str, Any] | None, market_context: list[dict[str, Any]]) -> dict[str, str]:
    if not quote or not analysis:
        return {"fund": "数据不足", "emotion": "数据不足", "chip": "数据不足"}
    amount = quote.get("amount_wan") or 0
    pct = analysis.get("pct") or 0
    position = analysis.get("position_in_range")
    fund = "资金承接强" if amount >= 100000 and pct > 0 else "资金有流动性但偏防守" if amount >= 100000 else "资金关注度一般"
    emotion = "情绪强" if pct >= 5 else "情绪恶化" if pct <= -5 else "情绪一般"
    chip = "筹码承接好" if position is not None and position >= 70 else "筹码松动" if position is not None and position <= 30 else "筹码中性"
    return {"fund": fund, "emotion": emotion, "chip": chip}


def derive_framework_conclusion(cycle: str, stock_tier: str, dimensions: dict[str, str], analysis: dict[str, Any] | None) -> str:
    if "退潮" in cycle or "风险样本" in stock_tier or dimensions.get("emotion") == "情绪恶化":
        return "风险大于机会"
    if "前排" in stock_tier and dimensions.get("fund") == "资金承接强" and dimensions.get("chip") == "筹码承接好":
        return "有机会但只等分歧买点"
    if "回暖" in cycle and stock_tier != "弱势跟风":
        return "观察等待确认"
    return "管住手，等信号"


def select_language_bias(conclusion: str, cycle: str) -> str:
    if "风险" in conclusion:
        return "风险大于机会，小心吃面，别和退潮期硬刚。"
    if "机会" in conclusion:
        return "只看核心，只等分歧转一致，别追一致。"
    if "确认" in conclusion:
        return "先看，不急，信号出来再动手。"
    return "管住手，杂毛直接拉黑。"


def make_decision(
    question: str,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    user_state: dict[str, Any],
    role: str,
    trend: dict[str, Any] | None = None,
    sector_context: dict[str, Any] | None = None,
    market_sentiment: dict[str, Any] | None = None,
    experience_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not quote or not analysis:
        return None

    score = 50
    reasons: list[str] = []
    warnings: list[str] = []
    pct = analysis.get("pct") or 0
    amplitude = analysis.get("amplitude") or 0
    position = analysis.get("position_in_range")
    market_score = market_score_value(market_context)

    score += market_score
    reasons.append(f"指数环境评分 {market_score:+d}")

    if market_sentiment:
        sentiment_score = sentiment_score_value(market_sentiment)
        score += sentiment_score
        if sentiment_score >= 0:
            reasons.append(f"市场情绪{market_sentiment['sentiment_label']} {sentiment_score:+d}")
        else:
            warnings.append(f"市场情绪{market_sentiment['sentiment_label']} {sentiment_score:+d}")

    if sector_context:
        sector_score = sector_score_value(sector_context)
        score += sector_score
        if sector_score > 0:
            reasons.append(f"板块热度{sector_context['heat_label']} {sector_score:+d}")
        elif sector_score < 0:
            warnings.append(f"板块不在主线 {sector_score:+d}")

    role_bonus = role_score_bias(role, pct, position, market_score)
    score += role_bonus
    if role_bonus > 0:
        reasons.append(f"{ROLE_PROFILES[role]['name']}提高进攻权重 {role_bonus:+d}")
    elif role_bonus < 0:
        warnings.append(f"{ROLE_PROFILES[role]['name']}降低风险暴露 {role_bonus:+d}")

    if pct >= 5:
        score += 15
        reasons.append("个股强势上涨，加分")
    elif pct >= 1.5:
        score += 8
        reasons.append("个股偏强，加分")
    elif pct <= -7:
        score -= 35
        warnings.append("跌幅接近或达到极端区，禁止冲动抄底")
    elif pct <= -3:
        score -= 18
        warnings.append("个股明显走弱，先按风险处理")

    if amplitude >= 9:
        score -= 18
        warnings.append("振幅过大，说明分歧剧烈")
    elif amplitude >= 5:
        score -= 8
        reasons.append("振幅偏大，降低仓位上限")

    if position is not None:
        if position >= 70 and pct > 0:
            score += 10
            reasons.append("收在日内高位，承接较好")
        elif position <= 30 and pct < 0:
            score -= 15
            warnings.append("靠近日内低位，抛压未释放")

    amount_wan = quote.get("amount_wan") or 0
    if amount_wan >= 100000:
        score += 6
        reasons.append("成交额超过 10 亿，流动性较好")
    elif amount_wan and amount_wan < 20000:
        score -= 6
        warnings.append("成交额偏小，容易被情绪资金影响")

    if user_state.get("scenario") == "被套处理":
        score -= 8
        warnings.append("用户已处于亏损/被套场景，先控回撤而不是加仓摊薄")
    if user_state.get("position_level") and user_state["position_level"] >= 0.5:
        score -= 10
        warnings.append("持仓已不低，不适合继续提高风险暴露")

    if trend:
        score += trend_adjustment(trend, reasons, warnings)

    opportunity = detect_actionable_opportunity(pct, amplitude, position, amount_wan, market_score)
    if not opportunity and trend:
        opportunity = detect_trend_opportunity(trend, pct, market_score, amount_wan)
    if opportunity:
        score += opportunity["bonus"]
        reasons.append(opportunity["reason"])

    experience_adjustment = apply_experience_bias(experience_cases or [], reasons, warnings)
    score += experience_adjustment

    label, position_limit, action = classify_decision(score, analysis, user_state, opportunity)
    invalid = build_invalid_condition(quote, analysis)
    trigger = build_trigger_condition(quote, analysis, market_context)

    return {
        "score": max(0, min(100, round(score))),
        "label": label,
        "action": action,
        "position_limit": position_limit,
        "reasons": reasons,
        "warnings": warnings,
        "trigger_condition": trigger,
        "invalid_condition": invalid,
        "opportunity": opportunity,
        "experience_adjustment": experience_adjustment,
    }


def apply_experience_bias(experience_cases: list[dict[str, Any]], reasons: list[str], warnings: list[str]) -> int:
    total = 0
    for case in experience_cases[:3]:
        bias = int(case.get("decision_bias") or 0)
        total += bias
        line = f"经验案例《{case.get('title')}》：{case.get('lesson')} ({bias:+d})"
        if bias >= 0:
            reasons.append(line)
        else:
            warnings.append(line)
    return max(-28, min(20, total))


def role_score_bias(role: str, pct: float, position: float | None, market_score: int) -> int:
    if role == "defense":
        return -6
    if role == "leader" and pct >= 3 and market_score >= 0:
        return 8
    if role == "first_board" and 0 <= pct <= 5 and market_score >= 0:
        return 6
    if role == "cycle" and market_score > 0:
        return 4
    return 0


def detect_actionable_opportunity(pct: float, amplitude: float, position: float | None, amount_wan: float, market_score: int) -> dict[str, Any] | None:
    if market_score < 0:
        return None
    if pct >= 3 and pct <= 7 and position is not None and position >= 65 and amount_wan >= 50000:
        return {
            "type": "strong_front_row",
            "bonus": 12,
            "reason": "强势前排形态：涨幅适中、收在日内高位、成交额够用",
        }
    if 0 <= pct <= 3 and position is not None and 45 <= position <= 75 and amount_wan >= 30000 and amplitude <= 5:
        return {
            "type": "healthy_pullback",
            "bonus": 8,
            "reason": "健康承接形态：没加速、波动可控、筹码没有明显松动",
        }
    return None


def trend_adjustment(trend: dict[str, Any], reasons: list[str], warnings: list[str]) -> int:
    label = trend.get("trend_label")
    volume_label = trend.get("volume_label")
    if label == "多头趋势":
        reasons.append("K线处于多头趋势，均线结构加分")
        return 12
    if label == "转强观察":
        reasons.append("K线有转强迹象，加入观察加分")
        return 6
    if label == "空头趋势":
        warnings.append("K线处于空头趋势，不能逆势抄底")
        return -18
    if label == "弱势修复":
        warnings.append("K线只是弱修复，持续性不足")
        return -8
    if volume_label == "放量承接":
        reasons.append("量能出现承接，短线加分")
        return 4
    return 0


def detect_trend_opportunity(trend: dict[str, Any], pct: float, market_score: int, amount_wan: float) -> dict[str, Any] | None:
    if market_score < 0 or amount_wan < 30000:
        return None
    if trend.get("trend_label") in {"多头趋势", "转强观察"} and trend.get("volume_label") == "放量承接" and pct >= 0:
        return {
            "type": "trend_follow",
            "bonus": 10,
            "reason": "趋势跟随机会：均线转强且量能承接，允许小仓跟随",
        }
    return None


def market_score_value(market_context: list[dict[str, Any]]) -> int:
    if not market_context:
        return -5
    avg_pct = sum(item.get("change_percent") or 0 for item in market_context) / len(market_context)
    if avg_pct >= 1:
        return 12
    if avg_pct >= 0.2:
        return 6
    if avg_pct <= -1:
        return -15
    if avg_pct <= -0.2:
        return -8
    return 0


def sentiment_score_value(sentiment: dict[str, Any]) -> int:
    label = sentiment.get("sentiment_label")
    if label == "发酵":
        return 12
    if label == "回暖":
        return 6
    if label == "退潮":
        return -18
    return -3


def sector_score_value(sector_context: dict[str, Any]) -> int:
    heat = sector_context.get("heat_label")
    if heat == "主线/强势":
        return 14
    if heat == "支线活跃":
        return 6
    if heat == "非主线":
        return -8
    return -3


def build_decision_panel(decision: dict[str, Any] | None, market_context: list[dict[str, Any]], market_sentiment: dict[str, Any] | None, sector_context: dict[str, Any] | None, trend: dict[str, Any] | None, user_state: dict[str, Any]) -> dict[str, Any]:
    if not decision:
        return {"score": 0, "label": "数据不足", "items": []}
    items = []
    items.append({"name": "指数环境", "value": market_score_value(market_context)})
    if market_sentiment:
        items.append({"name": "情绪温度", "value": sentiment_score_value(market_sentiment), "detail": market_sentiment.get("sentiment_label")})
    if sector_context:
        items.append({"name": "板块主线", "value": sector_score_value(sector_context), "detail": sector_context.get("heat_label")})
    if trend:
        items.append({"name": "K线趋势", "value": trend.get("trend_score", 0), "detail": trend.get("trend_label")})
    if decision.get("experience_adjustment"):
        items.append({"name": "游资经验", "value": decision.get("experience_adjustment", 0), "detail": "相似案例偏置"})
    if user_state.get("position_level"):
        items.append({"name": "用户仓位", "value": -10 if user_state["position_level"] >= 0.5 else 0, "detail": str(user_state["position_level"])})
    return {"score": decision["score"], "label": decision["label"], "position_limit": decision["position_limit"], "items": items, "trigger": decision.get("trigger_condition"), "invalid": decision.get("invalid_condition")}


def classify_decision(score: int, analysis: dict[str, Any], user_state: dict[str, Any], opportunity: dict[str, Any] | None = None) -> tuple[str, str, str]:
    pct = analysis.get("pct") or 0
    risk = analysis.get("risk_level")
    scenario = user_state.get("scenario")
    if risk == "高" and pct < 0:
        return "明确回避", "0 成", "不抄底、不加仓；已有仓位优先等反抽减压或按纪律止损。"
    if opportunity and score >= 68:
        return "可小仓试错", "1-2 成", "符合强势/承接条件，可以按触发条件小仓试错，错了立刻撤。"
    if score >= 72:
        return "可小仓试错", "1 成", "评分达标，但仍必须等触发条件，不允许满仓追。"
    if score >= 58:
        return "纳入观察", "0-1 成", "等待触发条件出现再考虑试错。"
    if score >= 42:
        return "等待确认", "0 成", "现在不动，等指数、板块、承接三项确认。"
    if scenario == "被套处理":
        return "减压防守", "不加仓", "不做摊薄，反抽优先降风险。"
    return "明确回避", "0 成", "当前不符合高胜率短线框架。"


def build_trigger_condition(quote: dict[str, Any], analysis: dict[str, Any], market_context: list[dict[str, Any]]) -> str:
    return "指数不明显走弱，所属板块同步走强，个股放量站回日内均价/关键承接位，回落不破低点，尾盘不跳水。"


def build_invalid_condition(quote: dict[str, Any], analysis: dict[str, Any]) -> str:
    low = quote.get("low")
    price = quote.get("price")
    if low and price:
        return f"跌破或持续贴近日内低点 {fmt(low)}，且反抽无量；或继续冲高回落、板块不跟。"
    return "板块不跟、成交萎缩、放量回落、核心票掉队。"


def build_prompt_context(
    session_id: str,
    question: str,
    stock: dict[str, str] | None,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    market_sentiment: dict[str, Any] | None,
    sector_context: dict[str, Any] | None,
    user_state: dict[str, Any],
    decision: dict[str, Any] | None,
    related_knowledge: list[dict[str, str]],
    experience_cases: list[dict[str, Any]],
    thinking_framework: dict[str, Any],
    emotion: dict[str, str],
    role_profile: dict[str, str],
    slang_notes: list[str],
    agent_steps: list[dict[str, str]],
    rule_text: str,
) -> str:
    compact_knowledge = [
        {"title": item.get("title", ""), "content": item.get("content", "")[:120]}
        for item in related_knowledge[:2]
    ]
    compact_experience = [
        {"title": item.get("title", ""), "pattern": item.get("pattern", "")[:140], "lesson": item.get("lesson", ""), "bias": item.get("decision_bias", 0)}
        for item in experience_cases[:3]
    ]
    compact_steps = [f"{step['name']}:{step['detail']}" for step in agent_steps[-6:]]
    conversation_context = build_conversation_context(session_id)
    return json.dumps(
        {
            "question": question,
            "conversation_context": conversation_context,
            "role": role_profile.get("name"),
            "style": role_profile.get("style"),
            "stock": {"name": quote.get("name"), "code": quote.get("code")} if quote else stock,
            "quote": {
                "price": quote.get("price"),
                "change_percent": quote.get("change_percent"),
                "amount_wan": quote.get("amount_wan"),
            } if quote else None,
            "market": [f"{item['name']} {fmt(item.get('change_percent'))}%" for item in market_context],
            "market_sentiment": market_sentiment,
            "sector": sector_context,
            "user_state": user_state,
            "decision": decision,
            "trend": compact_trend(trend),
            "thinking": thinking_framework,
            "emotion": emotion,
            "knowledge": compact_knowledge,
            "experience_cases": compact_experience,
            "slang": slang_notes,
            "steps": compact_steps,
            "rule_answer": rule_text[:1200],
            "required_output": (
                "用中文，像真人交易教练一样自然回答。先直接回应用户这句话，不要假装没看见；"
                "如果是市场/股票问题，结论前置，再给依据、触发条件、失效条件和下一步追问；"
                "如果信息不足，先说明缺哪些关键变量，不要硬编；"
                "如果是系统能力或闲聊问题，简短自然说明，再引导用户给出具体交易场景。"
                "不要预测涨跌，不荐股，不输出确定收益。"
            ),
            "style_guardrails": [
                "不要机械重复规则答案，保留人的语气和承接。",
                "不要只说没识别到股票；先判断用户是不是在问市场方向、系统能力或需要澄清。",
                "不要编造行情数据；缺数据就明说。",
            ],
        },
        ensure_ascii=False,
    )


def call_llm(prompt_context: str) -> tuple[str | None, dict[str, str]]:
    return call_openai_compatible(prompt_context, SYSTEM_PROMPT)


def build_agent_reply(
    question: str,
    stock: dict[str, str] | None,
    quote: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    market_context: list[dict[str, Any]],
    user_state: dict[str, Any],
    decision: dict[str, Any] | None,
    related_knowledge: list[dict[str, str]],
    thinking_framework: dict[str, Any],
    emotion: dict[str, str],
    role_profile: dict[str, str],
    slang_notes: list[str],
    sector_context: dict[str, Any] | None = None,
    market_sentiment: dict[str, Any] | None = None,
    mainline_rank: list[dict[str, Any]] | None = None,
    experience_cases: list[dict[str, Any]] | None = None,
) -> str:
    market_tone = judge_market_tone(market_context)
    if quote and analysis and decision:
        position_text = fmt(analysis.get("position_in_range")) + "%" if analysis.get("position_in_range") is not None else "数据不足"
        reasons = "；".join(decision["reasons"][:4]) or "无明显加分项"
        warnings = "；".join(decision["warnings"][:4]) or "未触发重大风险项"
        lines = [
            build_fast_verdict(decision, thinking_framework, emotion),
            f"【当前角色】{role_profile['name']}，重点看：{role_profile['focus']}。用户情绪：{emotion['label']}，处理方式：{emotion['tone']}。",
            f"【思维复刻】周期={thinking_framework['cycle']}；问题={thinking_framework['question_type']}；层级={thinking_framework['stock_tier']}；三维={thinking_framework['dimensions']['fund']} / {thinking_framework['dimensions']['emotion']} / {thinking_framework['dimensions']['chip']}。",
            f"【规则结论】{decision['label']}。评分 {decision['score']}/100，仓位上限：{decision['position_limit']}。动作：{decision['action']}",
            f"【市场环境】{market_tone}",
            build_sentiment_line(market_sentiment),
            build_sector_line(sector_context),
            f"【标的】{quote['name']}（{quote['code']}），现价 {fmt(quote['price'])}，涨跌幅 {fmt(analysis['pct'])}%，成交额 {format_amount(quote.get('amount_wan'))}，数据源 {quote['source']}。",
            f"【结构】{analysis['bias']}；振幅 {fmt(analysis['amplitude'])}%；日内位置 {position_text}；风险 {analysis['risk_level']}。",
            build_trend_line(trend),
            f"【加分依据】{reasons}。",
            f"【扣分/风险】{warnings}。",
            f"【触发条件】{decision['trigger_condition']}",
            f"【失效条件】{decision['invalid_condition']}",
            build_scenario_line(trend),
            f"【游资口径】{thinking_framework['language']}",
            build_slang_line(slang_notes),
            build_knowledge_line(related_knowledge),
            build_experience_line(experience_cases or []),
            build_retail_solution(question, quote, analysis, user_state, decision),
            build_execution_matrix(question, quote, analysis, trend, user_state, decision),
            build_follow_up_question(user_state, decision),
        ]
        if re.search(r"涨|买|卖|可以买|买入", question):
            lines.append(f"【直接回答】按当前规则，不是问‘会不会涨’，而是执行“{decision['label']}”。未触发条件前，不按买入处理；触发后也只能按仓位上限执行。")
        return "\n".join(lines)

    if is_market_opportunity_question(question):
        return build_market_opportunity_reply(question, market_context, market_sentiment, mainline_rank or [], role_profile, emotion, experience_cases or [])

    if experience_cases:
        return build_experience_framework_reply(question, market_context, market_sentiment, role_profile, emotion, experience_cases)

    if is_agent_meta_question(question) or is_casual_conversation(question):
        return build_conversational_reply(question, role_profile, emotion)

    if is_ambiguous_followup(question):
        return build_clarifying_reply(question)

    return (
        "你这个问题我接住了，但现在缺少关键对象。"
        f"\n【市场环境】{market_tone}"
        "\n【我需要你补充】你是在问市场方向、具体股票、持仓处理，还是复盘错因？如果是个股，请给股票名/代码；如果是持仓，请给成本和仓位。"
    )


def is_agent_meta_question(question: str) -> bool:
    return bool(re.search(r"你.*(智能|模型|大模型|接口|能力|会不会|能不能|为什么)|怎么用|你能做什么|正常沟通|像人", question))


def is_casual_conversation(question: str) -> bool:
    return bool(re.search(r"你好|在吗|谢谢|辛苦|厉害|不错|哈哈|聊聊", question))


def is_ambiguous_followup(question: str) -> bool:
    return bool(re.search(r"那|这个|它|现在|今天|怎么办|怎么搞|咋办", question)) and not re.search(r"买什么|方向|市场|大盘|股票|\d{6}", question)


def build_conversational_reply(question: str, role_profile: dict[str, str], emotion: dict[str, str]) -> str:
    if re.search(r"模型|大模型|接口|智能|正常沟通|像人", question):
        return "\n".join(
            [
                "可以更像人。问题不完全是模型低级，更多是 Agent 以前太像规则机：只会等股票名，再套模板。",
                f"【我现在的处理方式】当前角色是{role_profile['name']}，我会先判断你是在问市场、个股、持仓、复盘，还是系统能力；能直接答就直接答，缺信息才追问。",
                "【如果你问交易】我不会直接喊买卖，会先过市场温度、主线、个股地位、仓位和失效条件。",
                "【你可以这样问】今天市场能买什么？我三成仓拿着某某，成本多少，怎么办？某某是不是主线前排？",
            ]
        )
    if re.search(r"谢谢|辛苦|厉害|不错", question):
        return "不客气。你后面直接按真实交易场景问我就行：市场方向、具体标的、成本仓位、复盘错因，我会按不同链路拆。"
    return f"我在。你可以直接问市场方向、个股处理、仓位风控或复盘错因。当前我会按{role_profile['name']}风格回答，用户情绪识别为{emotion['label']}。"


def build_clarifying_reply(question: str) -> str:
    return "\n".join(
        [
            "你这句是追问，但上下文里的关键信息不够，我不能硬猜。",
            "你补一个方向我就能继续：",
            "1. 问市场：比如“今天市场能买什么方向？”",
            "2. 问个股：给股票名或 6 位代码。",
            "3. 问持仓：给成本、仓位、你是想买/卖/拿。",
            "这样我才能像交易教练一样给你具体剧本，而不是泛泛而谈。",
        ]
    )


def is_market_opportunity_question(question: str) -> bool:
    return bool(re.search(r"买什么|买哪些|能买什么|做什么|看什么方向|机会在哪|方向在哪|市场.*买|行情.*买|今天.*买", question))


def build_market_opportunity_reply(
    question: str,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    mainline_rank: list[dict[str, Any]],
    role_profile: dict[str, str],
    emotion: dict[str, str],
    experience_cases: list[dict[str, Any]],
) -> str:
    temperature = compute_market_temperature(market_context, sentiment, mainline_rank)
    if temperature >= 80:
        stance = "可以进攻，但只做主线核心的确认点。"
        position = "单票最多 2-3 成，不能因为情绪好就满仓。"
    elif temperature >= 60:
        stance = "可以小仓试错，不适合到处乱买。"
        position = "单票 1-2 成，只做前排、容量核心或分歧回流。"
    elif temperature >= 40:
        stance = "只能观察和轻仓验证，买点要很苛刻。"
        position = "没有确定主线时，宁可错过，不做后排脉冲。"
    else:
        stance = "今天优先防守，不是找买什么，而是找什么不能碰。"
        position = "默认空仓/低仓，持仓反抽先处理风险。"

    mainlines = mainline_rank[:5]
    if mainlines:
        direction_lines = []
        for index, item in enumerate(mainlines, 1):
            direction_lines.append(
                f"{index}. {item.get('name', '--')}：强度 {fmt(item.get('strength_score'))}，涨幅 {fmt(item.get('pct'))}%，涨停 {item.get('limit_up_count', '--')}，持续 {item.get('active_days', 0)} 天。"
            )
        direction_text = "\n".join(direction_lines)
    else:
        direction_text = "主线强度榜暂不可用，不能硬编方向；先看涨幅、成交额、涨停数量和持续性是否共振。"

    sentiment_line = build_sentiment_line(sentiment)
    market_line = judge_market_tone(market_context)
    forbidden = build_market_forbidden_line(temperature, sentiment)
    checklist = build_market_buy_checklist(temperature)
    return "\n".join(
        [
            f"【直接回答】你问的是“市场能买什么”，成熟做法不是直接报票，而是先过市场总开关。当前温度 {temperature}/100：{stance}",
            f"【当前角色】{role_profile['name']}，重点看：{role_profile['focus']}。用户情绪：{emotion['label']}，处理方式：{emotion['tone']}。",
            f"【市场环境】{market_line}",
            sentiment_line,
            f"【今天只看这些方向】\n{direction_text}",
            f"【什么能买】{checklist}",
            f"【什么不能买】{forbidden}",
            build_experience_line(experience_cases),
            f"【仓位剧本】{position}",
            "【执行口径】如果你要我进一步落到个股，请从上面主线里给一个具体股票名/代码，我会继续按行情、K线、板块地位、触发/失效条件重算。",
            "【合规提示】这里给的是市场研究和条件化筛选框架，不是确定性荐股或买卖指令。",
        ]
    )


def build_market_buy_checklist(temperature: int) -> str:
    if temperature >= 60:
        return "只看主线前排、容量核心、放量承接、分歧后回流；触发条件是板块同步走强、个股站回关键位、成交额放大且不冲高回落。"
    if temperature >= 40:
        return "只允许观察或极小仓验证，必须同时满足指数不拖累、板块排名靠前、个股不是后排、回落有承接。"
    return "严格说今天不主动找买点；只有冰点修复、指数止跌、主线核心率先回流，才允许重新评估。"


def build_experience_framework_reply(
    question: str,
    market_context: list[dict[str, Any]],
    sentiment: dict[str, Any] | None,
    role_profile: dict[str, str],
    emotion: dict[str, str],
    experience_cases: list[dict[str, Any]],
) -> str:
    primary = experience_cases[0]
    case_lines = []
    for item in experience_cases[:3]:
        case_lines.append(
            f"《{item.get('title')}》场景：{item.get('pattern')} 经验：{item.get('lesson')}"
        )
    return "\n".join(
        [
            "【直接回答】这个问题可以用历史短线经验拆，不需要先给具体股票。",
            f"【核心判断】按《{primary.get('title')}》这类经验，重点不是冲不冲，而是先看市场阶段和盈亏比。",
            f"【当前角色】{role_profile['name']}，重点看：{role_profile['focus']}。用户情绪：{emotion['label']}。",
            f"【市场环境】{judge_market_tone(market_context)}",
            build_sentiment_line(sentiment),
            "【相似经验】" + "\n".join(case_lines),
            f"【正向信号】{primary.get('positive_signal')}",
            f"【负向信号】{primary.get('negative_signal')}",
            "【执行口径】没有正向信号就不执行进攻；如果你给具体股票，我再按个股地位、板块、K线和仓位重算。",
            "【边界】这是公开经验抽象，不是复刻任何真实游资本人，也不是确定性荐股。",
        ]
    )


def build_market_forbidden_line(temperature: int, sentiment: dict[str, Any] | None) -> str:
    base = ["非主线后排", "一日游题材", "缩量冲高", "高位一致加速", "亏损后补仓摊薄"]
    if temperature < 50:
        base.extend(["退潮期追高", "弱势票抄底", "满仓试错"])
    if sentiment and sentiment.get("sentiment_label") == "退潮":
        base.append("情绪退潮时打板/半路")
    return "、".join(dict.fromkeys(base)) + "。"


def build_knowledge_line(related_knowledge: list[dict[str, str]]) -> str:
    if not related_knowledge:
        return "【知识依据】未匹配到专门知识条目，按通用短线规则处理。"
    snippets = "；".join(f"{item['title']}：{item['content']}" for item in related_knowledge[:2])
    return f"【知识依据】{snippets}"


def build_experience_line(experience_cases: list[dict[str, Any]]) -> str:
    if not experience_cases:
        return "【经验案例】未匹配到高相似交易经验，只按当前数据和规则处理。"
    lines = []
    for item in experience_cases[:3]:
        bias = item.get("decision_bias", 0)
        lines.append(f"《{item.get('title')}》{item.get('lesson')}（经验偏置 {bias:+d}）")
    return "【经验案例】" + "；".join(lines)


def compact_trend(trend: dict[str, Any] | None) -> dict[str, Any] | None:
    if not trend:
        return None
    return {
        "trend_label": trend.get("trend_label"),
        "volume_label": trend.get("volume_label"),
        "support": trend.get("support"),
        "resistance": trend.get("resistance"),
        "confidence": trend.get("confidence"),
    }


def build_trend_line(trend: dict[str, Any] | None) -> str:
    if not trend:
        return "【趋势】历史K线暂不可用，只按实时行情判断。"
    return (
        f"【趋势】{trend['trend_label']}；量能={trend['volume_label']}；"
        f"MA5={fmt(trend.get('ma5'))}，MA10={fmt(trend.get('ma10'))}，MA20={fmt(trend.get('ma20'))}；"
        f"支撑={fmt(trend.get('support'))}，压力={fmt(trend.get('resistance'))}，置信度={trend.get('confidence')}%。"
    )


def build_sentiment_line(sentiment: dict[str, Any] | None) -> str:
    if not sentiment:
        return "【情绪】涨跌停数据暂不可用。"
    coverage = sentiment.get("coverage")
    coverage_text = f"，覆盖率{coverage * 100:.1f}%" if isinstance(coverage, (int, float)) else ""
    return f"【情绪】{sentiment['sentiment_label']}；上涨{sentiment['up_count']}，下跌{sentiment['down_count']}，平盘{sentiment.get('flat_count', '--')}，涨停{sentiment['limit_up_count']}，跌停{sentiment['limit_down_count']}；样本{sentiment.get('sample_count', '--')}/{sentiment.get('total_count', '--')}{coverage_text}，来源：{sentiment.get('source', '公开行情')}。"


def build_sector_line(sector: dict[str, Any] | None) -> str:
    if not sector:
        return "【板块】板块数据暂不可用。"
    rank = sector.get("rank") or "--"
    pct = fmt(sector.get("pct")) if sector.get("pct") is not None else "--"
    return f"【板块】所属{sector['sector']}，热度={sector['heat_label']}，排名={rank}，涨幅={pct}%，当前榜首={sector.get('top_sector', '--')}。"


def build_scenario_line(trend: dict[str, Any] | None) -> str:
    if not trend:
        return "【走势推演】缺少历史K线，不能做分支推演。"
    scenario = trend.get("scenario", {})
    return f"【走势推演】向上：{scenario.get('bull')} 向下：{scenario.get('bear')} 中性：{scenario.get('base')}"


def build_fast_verdict(decision: dict[str, Any], thinking_framework: dict[str, Any], emotion: dict[str, str]) -> str:
    label = decision["label"]
    if emotion.get("label") == "纠错":
        prefix = "是，我可能看错了，按新数据重算。"
    elif label in {"明确回避", "减压防守"}:
        prefix = "结论：别碰，先防守。"
    elif label == "等待确认":
        prefix = "结论：先别动，等确认。"
    elif label == "纳入观察":
        prefix = "结论：能看，不能急。"
    else:
        prefix = "结论：只允许小仓试错。"
    reason = f"理由：{thinking_framework['cycle']}，{thinking_framework['stock_tier']}，{thinking_framework['conclusion']}。"
    action = f"动作：{decision['action']}"
    return f"{prefix}\n{reason}\n{action}"


def build_slang_line(slang_notes: list[str]) -> str:
    if not slang_notes:
        return "【黑话识别】未触发特殊黑话，按正常短线框架处理。"
    return "【黑话识别】" + "；".join(slang_notes)


def build_follow_up_question(user_state: dict[str, Any], decision: dict[str, Any]) -> str:
    if "cost" not in user_state and user_state.get("scenario") in {"被套处理", "卖出风控"}:
        return "【我需要你补充】你的成本价和仓位是多少？给出后我可以按浮亏/浮盈重新算处理方案。"
    if "position_level" not in user_state and user_state.get("scenario") in {"买入决策", "追高决策"}:
        return "【我需要你补充】你现在是空仓、已有底仓，还是准备追进去？不同仓位，动作完全不同。"
    if decision["label"] in {"等待确认", "纳入观察"}:
        return "【下一步】等触发条件出现后再问我一次，我会沿用这个标的继续判断，不用重复输入股票名。"
    return "【下一步】如果盘中出现反抽、跌破、回封、板块联动这些变化，继续追问，我会沿用当前标的和你的状态。"


def build_retail_solution(
    question: str,
    quote: dict[str, Any],
    analysis: dict[str, Any],
    user_state: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    price = quote.get("price")
    cost = user_state.get("cost")
    if cost and price:
        pnl = (price - cost) / cost * 100
        if pnl < -8:
            return f"【散户处理】你成本 {fmt(cost)}，浮亏约 {fmt(pnl)}%。不要补仓摊薄；反抽不放量先减压，只有重新站回成本附近且板块回流，才谈修复。"
        if pnl < 0:
            return f"【散户处理】你成本 {fmt(cost)}，浮亏约 {fmt(pnl)}%。先看能否收复关键承接位；不能收复就别加仓，反抽优先降风险。"
        return f"【散户处理】你成本 {fmt(cost)}，浮盈约 {fmt(pnl)}%。如果放量滞涨或尾盘回落，先锁一部分利润；强势延续再留观察仓。"

    if user_state.get("scenario") == "被套处理":
        return "【散户处理】被套时第一原则是不加错。先判断逻辑是否失效，失效就等反抽减压；没有反抽也不能无限扛。"
    if user_state.get("scenario") == "买入决策":
        return f"【散户处理】当前动作按“{decision['label']}”执行。没有触发条件，不开新仓；即使触发，也只按仓位上限，不追满仓。"
    return "【散户处理】普通散户最容易错在没有失效条件。先按系统结论定动作，再按触发/失效条件执行，不临盘改计划。"


def build_execution_matrix(
    question: str,
    quote: dict[str, Any],
    analysis: dict[str, Any],
    trend: dict[str, Any] | None,
    user_state: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    price = quote.get("price")
    low = quote.get("low")
    high = quote.get("high")
    support = trend.get("support") if trend else low
    resistance = trend.get("resistance") if trend else high
    trigger = decision.get("trigger_condition") or "放量转强、板块同步回流。"
    invalid = decision.get("invalid_condition") or "跌破关键位、板块不跟、放量回落。"
    label = decision.get("label")
    position_limit = decision.get("position_limit")
    cost = user_state.get("cost")
    scenario = user_state.get("scenario")

    if label in {"风控否决", "明确回避"}:
        empty_action = "空仓：不买，不做左侧证明。"
        hold_action = "持仓：不加仓；有反抽先减压，跌破失效线按纪律处理。"
        heavy_action = "重仓：先把风险降下来，不等行情证明自己；反抽无量优先减压。"
        trapped_action = "被套：禁止补仓摊薄，先按失效线处理，只有重新站回关键位才谈修复。"
    elif label in {"等待确认", "纳入观察"}:
        empty_action = f"空仓：先不买；只有满足触发条件后，才允许重新评估，仓位上限 {position_limit}。"
        hold_action = "持仓：不加仓；能站回关键位再看修复，不能站回就降低风险。"
        heavy_action = "重仓：不加，等反抽或确认失败做降仓；仓位比观点更重要。"
        trapped_action = "被套：不补仓等回本，先看是否收复承接位；收不回就按反抽减压。"
    else:
        empty_action = f"空仓：只允许触发后小仓试错，仓位上限 {position_limit}；不追高、不满仓。"
        hold_action = "持仓：强势延续才留观察仓；放量滞涨或回落先锁利润/降风险。"
        heavy_action = "重仓：即使结论偏强也不继续加，冲高分批降到可承受仓位。"
        trapped_action = "被套：若触发转强可观察修复，但不靠补仓摊薄；失效先处理。"

    if cost and price:
        pnl = (price - cost) / cost * 100
        if pnl < 0:
            hold_action = f"持仓：你成本 {fmt(cost)}，浮亏约 {fmt(pnl)}%；不补仓摊薄，反抽无量先减压。"
            trapped_action = f"被套：你成本 {fmt(cost)}，浮亏约 {fmt(pnl)}%；先等有效反抽减压，跌破失效条件不扛。"
        else:
            hold_action = f"持仓：你成本 {fmt(cost)}，浮盈约 {fmt(pnl)}%；跌破强弱线先保护利润。"
            trapped_action = "被套：当前不是被套剧本，若跌回成本下方再切换风控处理。"
    elif scenario == "卖出风控":
        hold_action = "持仓：先按失效线处理，不要等亏损扩大后再找理由。"
    if user_state.get("position_level") and user_state["position_level"] >= 0.7:
        heavy_action = "重仓：你当前仓位偏重，第一任务是恢复机动性；没有强触发不加仓，有反抽先降风险。"

    return "\n".join(
        [
            "【交易剧本】不是替你下单，是把不同仓位下的动作写清楚。",
            f"当前价：{fmt(price)}；参考支撑：{fmt(support)}；参考压力：{fmt(resistance)}。",
            empty_action,
            hold_action,
            heavy_action,
            trapped_action,
            f"触发再评估：{trigger}",
            f"失效先防守：{invalid}",
        ]
    )


def judge_market_tone(market_context: list[dict[str, Any]]) -> str:
    if not market_context:
        return "指数数据暂缺，Agent 会自动降低判断强度。"
    avg_pct = sum(item.get("change_percent") or 0 for item in market_context) / len(market_context)
    detail = "，".join(f"{item['name']} {fmt(item.get('change_percent'))}%" for item in market_context)
    if avg_pct >= 1:
        tone = "指数偏进攻，短线可提高关注度，但仍要看主线和承接。"
    elif avg_pct <= -1:
        tone = "指数偏防守，短线先控制频率，追高胜率会下降。"
    else:
        tone = "指数震荡，机会更依赖题材主线和个股辨识度。"
    return f"{tone}（{detail}）"


def build_flexible_view(question: str, analysis: dict[str, Any], market_tone: str) -> str:
    pct = analysis.get("pct") or 0
    position = analysis.get("position_in_range")
    risk = analysis.get("risk_level")
    if risk == "高" and pct < 0:
        return "【游资集合体会怎么处理】这种高风险下跌结构，第一反应不是抄底，而是看有没有止跌、回流和板块修复；没有这些，先防守。"
    if risk == "高" and pct > 0:
        return "【游资集合体会怎么处理】高波动上涨更看分歧承接；没有先手不盲追，等分歧转一致或次日弱转强确认。"
    if position is not None and position >= 70 and pct > 0:
        return "【游资集合体会怎么处理】收在日内高位，说明承接不差；下一步看是否带动板块，能带板块才有更高地位。"
    if position is not None and position <= 35 and pct < 0:
        return "【游资集合体会怎么处理】靠近日内低位，说明抛压还在；先等止跌结构，不用急着证明自己比市场聪明。"
    return "【游资集合体会怎么处理】目前更像观察位：等指数、板块、量能、位置四件事共振，再谈交易计划。"


def build_rule_reply(question: str, stock: dict[str, str] | None, quote: dict[str, Any] | None, analysis: dict[str, Any] | None) -> str:
    if quote and analysis:
        lines = [
            "我用游资集合体框架拆，不代表任何真实人物本人，也不给确定性买卖指令。",
            f"【标的】{quote['name']}（{quote['code']}），现价 {fmt(quote['price'])}，涨跌幅 {fmt(analysis['pct'])}%，成交额 {format_amount(quote.get('amount_wan'))}，数据源 {quote['source']}。",
            f"【结构】当前属于{analysis['bias']}，振幅 {fmt(analysis['amplitude'])}%，风险等级 {analysis['risk_level']}。",
            "【游资集合体会怎么处理】先看它是不是节后主线核心，再看板块有没有联动，最后看分歧时有没有承接；如果只是单票脉冲，不会因为一个问题就冲进去。",
            "【进攻条件】指数不拖累、所属题材涨幅靠前、成交额放大、分时回落不破承接区、尾盘不明显走弱。",
            "【防守条件】板块不跟、冲高回落、放量长上影、核心票掉队，优先防守而不是幻想明天反包。",
            "【风控】能做的只能是条件化观察或小仓试错预案，不能把短线试错做成无纪律持有。",
        ]
        if re.search(r"涨|买|卖|可以买|买入", question):
            lines.append("对‘会不会涨、能不能买’的回答：不预测确定涨跌；条件满足才有研究价值，条件不满足就不符合高胜率短线框架。")
        return "\n".join(lines)

    return "我用游资集合体框架拆：先看指数环境，再看主线题材，再看个股地位，最后看买点和风控。没有行情或标的信息时，不做强结论；短线最怕信息不完整还强行交易。"


def select_action(text: str, analysis: dict[str, Any] | None) -> dict[str, str]:
    if analysis and analysis.get("risk_level") == "高" or re.search(r"危险|防守|风险|止损|回落", text):
        return {"mood": "alert", "gesture": "拍桌警示", "motion": "warn"}
    if re.search(r"观察|等待|确认|承接|复盘", text):
        return {"mood": "thinking", "gesture": "抱臂推演", "motion": "think"}
    return {"mood": "calm", "gesture": "指屏强调", "motion": "point"}


def detect_intent(question: str) -> str:
    if re.search(r"模型|大模型|接口|智能|正常沟通|像人|你能做什么|怎么用", question):
        return "系统能力沟通"
    if re.search(r"你好|在吗|谢谢|辛苦|哈哈|聊聊", question):
        return "自然对话"
    if re.search(r"买什么|买哪些|能买什么|做什么|看什么方向|机会在哪|方向在哪|市场.*买|行情.*买", question):
        return "市场机会扫描"
    if re.search(r"观察池|自选股", question):
        return "观察池"
    if re.search(r"复盘|错在哪|错哪里|为什么亏|亏在哪|被套原因|追高了|吃面|计划", question):
        return "复盘方法"
    if re.search(r"买|卖|涨|跌|持有|减仓|加仓|止损|被套|成本|追高|打板|半路|走势|怎么看|怎么办|后面|如何|分析|看一下|看看", question):
        return "个股/交易决策问题"
    if re.search(r"情绪|周期|龙头|主线", question):
        return "情绪周期"
    if re.search(r"仓位|止损|风险", question):
        return "仓位纪律"
    return "综合判断"


def http_get(url: str, encoding: str = "utf-8") -> str:
    source = source_name_from_url(url)
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            text = response.read().decode(encoding, errors="ignore")
        record_source_health(source, True, int((time.perf_counter() - started) * 1000))
        return text
    except Exception as error:
        record_source_health(source, False, int((time.perf_counter() - started) * 1000), str(error))
        raise


def to_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def fmt(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"


def format_amount(wan: float | None) -> str:
    if not wan:
        return "--"
    return f"{wan / 10000:.2f}亿" if wan >= 10000 else f"{wan:.0f}万"
