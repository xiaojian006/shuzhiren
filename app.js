const chatLog = document.querySelector("#chatLog");
const chatForm = document.querySelector("#chatForm");
const questionInput = document.querySelector("#questionInput");
const avatar = document.querySelector("#avatar");
const gesture = document.querySelector("#gesture");
const moodText = document.querySelector("#moodText");
const intentText = document.querySelector("#intentText");
const voiceText = document.querySelector("#voiceText");
const muteBtn = document.querySelector("#muteBtn");
const marketSnapshot = document.querySelector("#marketSnapshot");
const roleSelect = document.querySelector("#roleSelect");
const decisionPanel = document.querySelector("#decisionPanel");
const watchlistPanel = document.querySelector("#watchlistPanel");
const emotionDashboard = document.querySelector("#emotionDashboard");
const mainlineRank = document.querySelector("#mainlineRank");
const confidencePanel = document.querySelector("#confidencePanel");
const riskVetoPanel = document.querySelector("#riskVetoPanel");
const lineagePanel = document.querySelector("#lineagePanel");
const auditPanel = document.querySelector("#auditPanel");
const auditReplayPanel = document.querySelector("#auditReplayPanel");
const sourceHealthPanel = document.querySelector("#sourceHealthPanel");
const watchAlertsPanel = document.querySelector("#watchAlertsPanel");
const premarketPlanPanel = document.querySelector("#premarketPlanPanel");
const userProfilePanel = document.querySelector("#userProfilePanel");
const auditExplainBtn = document.querySelector("#auditExplainBtn");
const auditExplainPanel = document.querySelector("#auditExplainPanel");

let muted = false;
let quoteRequestId = 0;
let speechQueue = [];
let speechInterrupted = false;
let thinkingTimer = null;
let idleMotionTimer = null;
let watchAlertTimer = null;
let lastBackendReply = null;
const sessionId = localStorage.getItem("digitalHumanSessionId") || crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
localStorage.setItem("digitalHumanSessionId", sessionId);

const localStockAliases = {
  豫能控股: "001896",
  贵州茅台: "600519",
  平安银行: "000001",
  宁德时代: "300750",
  比亚迪: "002594",
  东方财富: "300059",
  中信证券: "600030",
  浪潮信息: "000977",
  中际旭创: "300308",
  工业富联: "601138",
};

const personaPrefix =
  "我用公开市场里多类游资短线框架的集合体来拆，不代表任何真实人物本人或私人判断。";
const riskSuffix =
  "\n\n结论只能作为研究参考，不构成投资建议；真实交易要结合你的资金、期限和风险承受能力。";

const knowledgeBase = [
  {
    intent: "情绪周期",
    mood: "thinking",
    gesture: "推演周期",
    keywords: ["情绪", "周期", "龙头", "连板", "高度", "主线"],
    answer:
      "短线的核心不是预测，而是确认资金是否还愿意给溢价。看情绪周期要盯四个点：高度板有没有继续打开空间，断板票是否出现 A 杀，主线分歧后有没有资金回流，容量核心有没有持续成交承接。启动和发酵期可以主动找核心，分歧期只做强中强或等回流，退潮期最好的动作通常是降低频率。",
  },
  {
    intent: "仓位纪律",
    mood: "calm",
    gesture: "压住仓位",
    keywords: ["仓位", "满仓", "控制", "风险", "纪律", "止损"],
    answer:
      "仓位不是信心的表达，是容错率的设计。看不懂市场时，仓位应该主动降下来；只有指数环境、题材主线、个股地位和买点结构同时匹配，才有提高仓位的理由。短线错了要快，不能把一笔试错交易拖成被动长线。",
  },
  {
    intent: "复盘方法",
    mood: "thinking",
    gesture: "记录复盘",
    keywords: ["复盘", "总结", "盘后", "明天", "计划", "怎么看"],
    answer:
      "有效复盘只回答三个问题：今天钱在什么方向赚钱，亏钱效应从哪里扩散，明天什么条件证明判断正确或错误。不要只看涨幅榜，要看强度、容量、持续性和失败样本。第二天交易前先写预案，盘中只执行条件，不临时被分时牵着走。",
  },
  {
    intent: "被套处理",
    mood: "alert",
    gesture: "先控回撤",
    keywords: ["被套", "亏", "割肉", "回本", "追高", "怎么办"],
    answer:
      "被套后第一步不是找利好，而是判断买入逻辑是否被证伪。如果题材退潮、核心走弱、量能萎缩，短线逻辑就已经变形；这时要先控制回撤，再考虑反抽减压。最危险的是亏损后不断降低标准，把纪律改成幻想。",
  },
  {
    intent: "攻防判断",
    mood: "thinking",
    gesture: "切换攻防",
    keywords: ["进攻", "防守", "现在市场", "行情", "大盘", "指数", "适合"],
    answer:
      "攻防取决于指数环境和赚钱效应是否共振。指数稳、成交放大、主线清楚、核心股抗分歧，这是进攻环境；指数弱、成交缩、热点一日游、跌停和大面扩散，就是防守环境。真正的短线高手不是天天进攻，而是在市场给赔率的时候进攻，在赔率消失的时候收手。",
  },
];

function detectStockCode(text) {
  const match = text.match(/\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b/);
  return match ? match[1] : null;
}

async function resolveStock(question) {
  const code = detectStockCode(question);
  if (code) return { code, name: null, source: "直接代码" };

  const localMatch = resolveLocalStockName(question);
  if (localMatch) return { ...localMatch, source: "本地名称表" };

  const keyword = extractStockKeyword(question);
  if (!keyword) return null;

  return await searchSinaStock(keyword);
}

function resolveLocalStockName(question) {
  for (const [name, code] of Object.entries(localStockAliases)) {
    if (question.includes(name)) return { name, code };
  }

  return null;
}

function extractStockKeyword(question) {
  const cleaned = question
    .replace(/[，。！？,.?!；;：:\s]/g, " ")
    .replace(/五一|节后|过后|之后|以前|现在|今天|明天|后面|当前|这个|股票|个股|会涨吗|会跌吗|可以买入吗|可以买|买入|卖出|分析|看看|如何|怎么样|怎么办|能不能/g, " ");
  const candidates = cleaned.match(/[\u4e00-\u9fa5A-Za-z]{2,12}/g) || [];

  return candidates.sort((a, b) => b.length - a.length)[0] || null;
}

function searchSinaStock(keyword) {
  return new Promise((resolve, reject) => {
    const variableName = `suggestdata_${Date.now()}_${quoteRequestId++}`;
    const script = document.createElement("script");
    script.charset = "GBK";
    const timer = window.setTimeout(() => cleanup(new Error("股票名称搜索超时")), 8000);
    let done = false;

    window[variableName] = "";

    function cleanup(error, data) {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      script.remove();
      delete window[variableName];
      if (error) reject(error);
      else resolve(data);
    }

    script.onload = () => {
      try {
        cleanup(null, parseSinaSuggestion(window[variableName], keyword));
      } catch (error) {
        cleanup(error);
      }
    };

    script.onerror = () => cleanup(new Error("股票名称搜索接口加载失败"));
    script.src = `https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key=${encodeURIComponent(keyword)}&name=${variableName}`;
    document.body.appendChild(script);
  });
}

function parseSinaSuggestion(raw, keyword) {
  if (!raw) throw new Error(`没有搜索到“${keyword}”对应的 A 股代码`);

  const items = raw.split(";").map((item) => item.split(",")).filter((fields) => fields.length >= 4);
  const match = items.find((fields) => /^(00|30|60|68)\d{4}$/.test(fields[2] || fields[3] || ""));

  if (!match) throw new Error(`没有搜索到“${keyword}”对应的 A 股代码`);

  const code = /^(00|30|60|68)\d{4}$/.test(match[2]) ? match[2] : match[3];

  return {
    name: match[0] || keyword,
    code,
    source: "新浪名称搜索",
  };
}

function toTencentSymbol(code) {
  return code.startsWith("6") ? `sh${code}` : `sz${code}`;
}

function toSinaSymbol(code) {
  return code.startsWith("6") ? `sh${code}` : `sz${code}`;
}

async function fetchQuote(code) {
  const errors = [];

  for (const source of [fetchTencentQuote, fetchSinaQuote]) {
    try {
      return await source(code);
    } catch (error) {
      errors.push(error.message);
    }
  }

  throw new Error(`多个公开行情源均失败：${errors.join("；")}`);
}

function fetchTencentQuote(code) {
  return new Promise((resolve, reject) => {
    const symbol = toTencentSymbol(code);
    const variableName = `v_${symbol}`;
    const script = document.createElement("script");
    script.charset = "GBK";
    const requestMark = `${Date.now()}_${quoteRequestId++}`;
    const timer = window.setTimeout(() => cleanup(new Error("腾讯行情接口响应超时")), 8000);
    let done = false;

    delete window[variableName];

    function cleanup(error, data) {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      script.remove();
      if (error) reject(error);
      else resolve(data);
    }

    script.onload = () => {
      try {
        cleanup(null, parseTencentQuote(window[variableName], code));
      } catch (error) {
        cleanup(error);
      }
    };

    script.onerror = () => cleanup(new Error("腾讯行情接口加载失败"));
    script.src = `https://qt.gtimg.cn/q=${symbol}&_=${requestMark}`;
    document.body.appendChild(script);
  });
}

function fetchSinaQuote(code) {
  return new Promise((resolve, reject) => {
    const symbol = toSinaSymbol(code);
    const variableName = `hq_str_${symbol}`;
    const script = document.createElement("script");
    script.charset = "GBK";
    const requestMark = `${Date.now()}_${quoteRequestId++}`;
    const timer = window.setTimeout(() => cleanup(new Error("新浪行情接口响应超时")), 8000);
    let done = false;

    delete window[variableName];

    function cleanup(error, data) {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      script.remove();
      if (error) reject(error);
      else resolve(data);
    }

    script.onload = () => {
      try {
        cleanup(null, parseSinaQuote(window[variableName], code));
      } catch (error) {
        cleanup(error);
      }
    };

    script.onerror = () => cleanup(new Error("新浪行情接口加载失败"));
    script.src = `https://hq.sinajs.cn/list=${symbol}&_=${requestMark}`;
    document.body.appendChild(script);
  });
}

function parseTencentQuote(raw, requestedCode) {
  if (!raw || !raw.includes("~")) throw new Error("没有获取到该代码的行情");

  const fields = raw.split("~");
  const price = numberOrNull(fields[3]);
  const previousClose = numberOrNull(fields[4]);
  const open = numberOrNull(fields[5]);
  const volumeHands = numberOrNull(fields[6]);
  const high = numberOrNull(fields[33]);
  const low = numberOrNull(fields[34]);
  const amountWan = numberOrNull(fields[37]);
  const change = price !== null && previousClose ? price - previousClose : numberOrNull(fields[31]);
  const changePercent = price !== null && previousClose ? (change / previousClose) * 100 : numberOrNull(fields[32]);

  return {
    name: fields[1] || "未知证券",
    code: fields[2] || requestedCode,
    price,
    previousClose,
    open,
    high,
    low,
    volumeHands,
    amountWan,
    change,
    changePercent,
    time: fields[30] || "--",
    source: "腾讯行情",
  };
}

function parseSinaQuote(raw, requestedCode) {
  if (!raw || !raw.includes(",")) throw new Error("新浪行情没有返回有效数据");

  const fields = raw.split(",");
  const price = numberOrNull(fields[3]);
  const previousClose = numberOrNull(fields[2]);
  const open = numberOrNull(fields[1]);
  const high = numberOrNull(fields[4]);
  const low = numberOrNull(fields[5]);
  const amountYuan = numberOrNull(fields[9]);
  const change = price !== null && previousClose ? price - previousClose : null;
  const changePercent = price !== null && previousClose ? (change / previousClose) * 100 : null;

  return {
    name: fields[0] || "未知证券",
    code: requestedCode,
    price,
    previousClose,
    open,
    high,
    low,
    volumeHands: numberOrNull(fields[8]) ? numberOrNull(fields[8]) / 100 : null,
    amountWan: amountYuan ? amountYuan / 10000 : null,
    change,
    changePercent,
    time: fields[30] && fields[31] ? `${fields[30]} ${fields[31]}` : "--",
    source: "新浪行情",
  };
}

function numberOrNull(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, digits = 2) {
  return value === null || value === undefined ? "--" : value.toFixed(digits);
}

function formatAmount(wan) {
  if (!wan) return "--";
  return wan >= 10000 ? `${(wan / 10000).toFixed(2)} 亿` : `${wan.toFixed(0)} 万`;
}

function analyzeQuote(quote) {
  const pct = quote.changePercent ?? 0;
  const amplitude = quote.high && quote.low && quote.previousClose
    ? ((quote.high - quote.low) / quote.previousClose) * 100
    : null;
  const positionInRange = quote.high && quote.low && quote.price && quote.high !== quote.low
    ? ((quote.price - quote.low) / (quote.high - quote.low)) * 100
    : null;

  const bias = pct >= 5 ? "强势" : pct >= 1.5 ? "偏强" : pct <= -5 ? "弱势" : pct <= -1.5 ? "偏弱" : "震荡";
  const riskLevel = Math.abs(pct) >= 7 || (amplitude !== null && amplitude >= 9)
    ? "高"
    : Math.abs(pct) >= 3 || (amplitude !== null && amplitude >= 5)
      ? "中"
      : "低到中";
  const decision = buildResearchDecision({ pct, amplitude, positionInRange, bias, quote });
  const tactic = buildTacticalPlan({ pct, amplitude, positionInRange, bias, quote, riskLevel });

  return { pct, amplitude, positionInRange, bias, riskLevel, decision, tactic };
}

function buildResearchDecision({ pct, amplitude, positionInRange, bias, quote }) {
  if (pct >= 7) {
    return "短线已经明显加速，重点不是追不追，而是有没有板块共振和明天溢价。没有先手时，更适合等分歧后的承接确认。";
  }

  if (pct <= -7) {
    return "当日杀跌较重，先把它当风险样本处理。除非出现放量止跌、核心地位仍在、板块同步修复，否则不急着做左侧判断。";
  }

  if (positionInRange !== null && positionInRange > 70 && pct > 0) {
    return "价格收在日内偏高区，说明承接尚可。下一步看能否继续放量并带动板块，如果只是单票脉冲，追高性价比不高。";
  }

  if (positionInRange !== null && positionInRange < 30 && pct < 0) {
    return "价格靠近日内低位，说明抛压没有完全释放。更合理的动作是等止跌和回流信号，而不是因为跌了就觉得便宜。";
  }

  if (quote.price && quote.open && quote.price > quote.open && pct > 0) {
    return "日内能站在开盘价上方，结构不差，但还需要结合成交额和板块地位判断持续性。短线只看分时是不够的。";
  }

  return `${bias}结构，暂时没有给出足够强的进攻信号。更适合列入观察，等待主线、量能和位置三者共振。`;
}

function buildTacticalPlan({ pct, amplitude, positionInRange, bias, quote, riskLevel }) {
  const plans = [];

  if (riskLevel === "高") {
    plans.push("偏事件驱动或高波动状态下，集合游资框架通常不会无脑追确认后的高位，而是先看分歧承接；没有先手时，先把它放进观察池。");
  } else if (pct > 0 && positionInRange !== null && positionInRange >= 65) {
    plans.push("如果它属于节后主线核心，并且放量站在日内高位，激进资金会看次日是否弱转强；但临盘追高要等板块共振，不是只看单票红盘。");
  } else if (pct < 0 && positionInRange !== null && positionInRange <= 35) {
    plans.push("如果节后资金没有回流，集合游资框架会先防守，不会因为名字熟悉就接下跌趋势；更看重止跌、放量回拉和板块修复。");
  } else {
    plans.push("当前更像观察位，集合游资框架会等市场给方向：要么放量突破确认强度，要么缩量回踩后出现承接，再决定是否纳入交易计划。");
  }

  plans.push("触发条件：指数不拖累、所属板块进入涨幅前列、同题材出现联动、该股成交额放大且不冲高回落。满足越多，进攻价值越高。缺两项以上，就偏防守。");
  plans.push("风控条件：跌破当日关键承接区、板块核心掉队、放量长上影或尾盘明显回落时，要先降低预期，不能把短线试错变成被动持有。");

  return plans.join("\n");
}

function buildQuoteReply(question, quote) {
  const analysis = analyzeQuote(quote);
  const lines = [
    personaPrefix,
    `\n【行情】${quote.name}（${quote.code}）`,
    `现价：${formatNumber(quote.price)}，涨跌幅：${formatNumber(analysis.pct)}%，涨跌额：${formatNumber(quote.change)}`,
    `今开：${formatNumber(quote.open)}，最高：${formatNumber(quote.high)}，最低：${formatNumber(quote.low)}，成交额：${formatAmount(quote.amountWan)}`,
    `更新时间：${quote.time}，数据源：${quote.source || "公开行情"}`,
    "\n【结构判断】",
    `状态：${analysis.bias}；日内振幅：${formatNumber(analysis.amplitude)}%；风险级别：${analysis.riskLevel}`,
    analysis.positionInRange === null
      ? "日内位置：数据不足"
      : `日内位置：约处在全天波动区间的 ${formatNumber(analysis.positionInRange)}% 位置`,
    "\n【短线研究结论】",
    analysis.decision,
    "\n【换成游资集合体的操作框架】",
    analysis.tactic,
    "\n【决策清单】",
    "1. 先确认它是不是节后市场主线，不是主线就降低预期。",
    "2. 再确认它在板块里是不是前排辨识度，不是前排就不追情绪。",
    "3. 最后看买点是否有性价比：强势看分歧承接，弱势看止跌回流，不做没有风控位的冲动单。",
  ];

  if (/涨|买|卖|荐股|能不能进|可以进|满仓|梭哈/.test(question)) {
    lines.push("\n【对“会不会涨、能不能买”的回答】不能给确定涨跌预测，也不能替你下买卖决定。更接近实战的答案是：如果节后板块回流、该股放量站强、日内承接不破，可以进入观察或试错预案；如果只是单票脉冲、板块不跟、冲高回落，就不符合游资框架里的高胜率进攻条件。");
  }

  return {
    intent: "个股行情研究",
    mood: analysis.riskLevel === "高" ? "alert" : "thinking",
    gesture: analysis.riskLevel === "高" ? "警惕波动" : "研判承接",
    text: `${lines.join("\n")}${riskSuffix}`,
    quote,
    analysis,
  };
}

function analyzeQuestion(question) {
  const normalized = question.trim().toLowerCase();
  let best = null;
  let score = 0;

  for (const item of knowledgeBase) {
    const itemScore = item.keywords.reduce((total, keyword) => {
      return normalized.includes(keyword.toLowerCase()) ? total + 1 : total;
    }, 0);

    if (itemScore > score) {
      best = item;
      score = itemScore;
    }
  }

  if (best) return best;

  return {
    intent: "综合判断",
    mood: "calm",
    gesture: "拆解问题",
    answer:
      "先把问题拆成市场环境、题材方向、个股地位、买点位置和风控条件。短线不是追求每次判断都对，而是让对的时候赚得合理，错的时候亏得可控。条件不清晰时，等待本身就是策略。",
  };
}

function buildReply(question) {
  const result = analyzeQuestion(question);
  return {
    ...result,
    text: `${personaPrefix}\n\n${result.answer}${riskSuffix}`,
  };
}

function addMessage(role, text, question = "") {
  const message = document.createElement("article");
  message.className = `message ${role}`;
  message.innerHTML = `<div class="speaker">${role === "user" ? "用户" : "数字人"}</div><p></p>`;
  message.querySelector("p").textContent = text;
  if (role === "bot") {
    const actions = document.createElement("div");
    actions.className = "feedback-row";
    actions.innerHTML = `<button type="button" data-useful="true">有用</button><button type="button" data-useful="false">没用</button>`;
    actions.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      sendFeedback(question, text, button.dataset.useful === "true");
      actions.textContent = "已记录反馈";
    });
    message.appendChild(actions);
  }
  chatLog.appendChild(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addAgentTrace(steps) {
  if (!Array.isArray(steps) || steps.length === 0) return;

  const trace = steps.map((step, index) => `${index + 1}. ${step.name}：${step.detail}`).join("\n");
  const message = document.createElement("article");
  message.className = "message bot agent-trace";
  message.innerHTML = `<div class="speaker">Agent 执行轨迹</div><p></p>`;
  message.querySelector("p").textContent = trace;
  chatLog.appendChild(message);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setAvatarState(state, intent, action) {
  avatar.className = `avatar ${state}`;
  moodText.textContent = state === "alert" ? "谨慎提醒" : state === "thinking" ? "思考推演" : "冷静观察";
  intentText.textContent = intent;
  gesture.textContent = action;
}

function startThinking(question) {
  clearTimeout(thinkingTimer);
  const delay = Math.min(3200, Math.max(900, question.length * 55 + 700));
  setAvatarState("thinking", "推演中", "抱臂思考");
  voiceText.textContent = "思考中...";
  return new Promise((resolve) => {
    thinkingTimer = window.setTimeout(resolve, delay);
  });
}

function interruptDigitalHuman() {
  speechInterrupted = true;
  speechQueue = [];
  clearTimeout(thinkingTimer);
  window.speechSynthesis?.cancel();
  stopSpeakingAnimation();
}

function setBackendAvatarState(reply) {
  const state = detectAgentState(reply);
  avatar.className = `avatar ${state.mood} ${state.motion}`.trim();
  moodText.textContent = state.label;
  intentText.textContent = reply.intent || "综合判断";
  gesture.textContent = state.gesture || reply.gesture || "观察盘面";
}

function detectAgentState(reply) {
  if (reply?.risk_veto?.blocked) return { mood: "alert", motion: "warn", label: "风控否决", gesture: "压住风险" };
  if ((reply?.answer_confidence?.score ?? 100) < 55) return { mood: "thinking", motion: "think", label: "低置信推演", gesture: "标注数据缺口" };
  if (reply?.intent?.includes("市场机会")) return { mood: "thinking", motion: "point", label: "机会扫描", gesture: "筛选主线" };
  if (reply?.agent_critic && reply.agent_critic.passed === false) return { mood: "alert", motion: "knock", label: "自检修正", gesture: "校验回答" };
  return { mood: reply?.mood || "calm", motion: reply?.motion || detectMotionFromText(reply?.text || ""), label: reply?.mood === "alert" ? "谨慎提醒" : reply?.mood === "thinking" ? "思考推演" : "冷静观察", gesture: reply?.gesture || "观察盘面" };
}

function detectMotionFromText(text = "") {
  if (/核按钮|吃面|风险|危险|回避|止损|退潮|别碰/.test(text)) return "warn";
  if (/机会|龙头|吃肉|核心|弱转强|试错/.test(text)) return "point";
  if (/记住|必须|我再说一遍|纪律/.test(text)) return "knock";
  if (/思考|观察|等待|确认|你觉得/.test(text)) return "think";
  return "idle-live";
}

function updateMarketSnapshot(reply) {
  const llmLine = reply?.llm_status
    ? `\n模型：${reply.llm_status.provider} / ${reply.llm_status.status}`
    : "";
  if (!reply?.quote) {
    if (llmLine) marketSnapshot.textContent = llmLine.trim();
    return;
  }

  const { quote, analysis } = reply;
  const pct = analysis?.pct ?? quote.change_percent ?? quote.changePercent ?? 0;
  const riskLevel = analysis?.risk_level ?? analysis?.riskLevel ?? "--";
  const bias = analysis?.bias ?? "--";
  const className = pct >= 0 ? "quote-up" : "quote-down";
  marketSnapshot.innerHTML = `
    <span class="${className}">${quote.name} ${formatNumber(quote.price)} ${formatNumber(pct)}%</span>\n
    风险：${riskLevel}；结构：${bias}\n
    数据源：${quote.source || "公开行情"}\n
    更新时间：${quote.time}${llmLine}
  `.trim();
}

function updateDecisionPanel(reply) {
  if (!decisionPanel || !reply?.decision_panel) return;
  const panel = reply.decision_panel;
  const items = (panel.items || [])
    .map((item) => `<div><span>${item.name}</span><strong>${item.value > 0 ? "+" : ""}${item.value}</strong><em>${item.detail || ""}</em></div>`)
    .join("");
  decisionPanel.innerHTML = `
    <div class="decision-head"><b>${panel.label}</b><span>${panel.score}/100</span></div>
    <div class="decision-limit">仓位上限：${panel.position_limit || "--"}</div>
    <div class="decision-items">${items}</div>
  `;
}

function updateEmotionDashboard(dashboard) {
  if (!emotionDashboard || !dashboard) return;
  const friedRate = dashboard.fried_board_rate == null ? "--" : `${Math.round(dashboard.fried_board_rate * 100)}%`;
  const y = dashboard.yesterday_limit_performance || {};
  emotionDashboard.innerHTML = `
    <div class="metric-grid">
      <div><b>${dashboard.label || "--"}</b><span>情绪阶段</span></div>
      <div><b>${dashboard.up_count || 0}/${dashboard.down_count || 0}</b><span>上涨/下跌</span></div>
      <div><b>${dashboard.limit_up_count || 0}/${dashboard.limit_down_count || 0}</b><span>涨停/跌停</span></div>
      <div><b>${friedRate}</b><span>炸板率</span></div>
      <div><b>${dashboard.limit_height || 0}板</b><span>连板高度</span></div>
      <div><b>${formatNumber(y.avg_pct)}%</b><span>昨日涨停均幅</span></div>
    </div>
    <p>样本：${dashboard.sample_count || "--"}/${dashboard.total_count || "--"}，覆盖率：${dashboard.coverage == null ? "--" : Math.round(dashboard.coverage * 100) + "%"}</p>
  `;
}

function updateMainlineRank(items) {
  if (!mainlineRank || !Array.isArray(items)) return;
  if (items.length === 0) {
    mainlineRank.textContent = "暂无主线强度数据。";
    return;
  }
  mainlineRank.innerHTML = items.slice(0, 6).map((item, index) => `
    <div class="rank-item">
      <b>${index + 1}. ${item.name || item.code}</b>
      <span>强度 ${formatNumber(item.strength_score)}｜涨幅 ${formatNumber(item.pct)}%｜涨停 ${item.limit_up_count ?? "--"}｜持续 ${item.active_days || 0}天</span>
    </div>
  `).join("");
}

function updateConfidencePanel(confidence) {
  if (!confidencePanel || !confidence) return;
  confidencePanel.innerHTML = `
    <div class="decision-head"><b>${confidence.label}</b><span>${confidence.score}/100</span></div>
    <p>${(confidence.items || []).join("；")}</p>
    <p>盘中：${confidence.is_trading_time ? "是" : "否"}；来源：${confidence.data_source || "公开数据"}</p>
  `;
}

function updateRiskVetoPanel(veto) {
  if (!riskVetoPanel || !veto) return;
  const reasons = (veto.reasons || []).map((item) => `<p>${item}</p>`).join("");
  riskVetoPanel.innerHTML = `
    <div class="decision-head"><b>${veto.blocked ? "已否决" : "未否决"}</b><span>${veto.level || "none"}</span></div>
    <p>${veto.action || "未触发风控否决"}</p>
    ${reasons}
  `;
}

function updateLineagePanel(items) {
  if (!lineagePanel || !Array.isArray(items)) return;
  lineagePanel.innerHTML = items.map((item) => `
    <div class="lineage-item">
      <b>${item.name}</b>
      <span>${item.source_type}｜${item.source}｜${item.available ? "可用" : "不可用"}</span>
      <em>${item.method}${item.coverage == null ? "" : `｜覆盖：${typeof item.coverage === "number" && item.coverage <= 1 ? Math.round(item.coverage * 100) + "%" : item.coverage}`}</em>
    </div>
  `).join("");
}

function updateAuditPanel(audit) {
  if (!auditPanel || !audit) return;
  auditPanel.innerHTML = `
    <div class="decision-head"><b>${audit.final_label || "--"}</b><span>${audit.final_score ?? "--"}/100</span></div>
    <p>审计ID：${audit.id}</p>
    <p>原始结论：${audit.original_label || "--"}；最终结论：${audit.final_label || "--"}；仓位：${audit.position_limit || "--"}</p>
    <p>证据：${(audit.evidence || []).slice(0, 3).join("；") || "暂无"}</p>
  `;
}

function updateUserProfilePanel(memory) {
  if (!userProfilePanel || !memory) return;
  const strategy = memory.strategy_memory || memory;
  const mistake = memory.mistake_profile || {};
  const scenarios = (strategy.frequent_scenarios || []).map(([name, count]) => `${name} ${count}次`).join("；") || "暂无高频场景";
  const risks = (strategy.recent_risks || []).join("；") || "暂无近期重复风险";
  userProfilePanel.innerHTML = `
    <p>高频场景：${scenarios}</p>
    <p>近期风险：${risks}</p>
    <p>错因画像：${(mistake.active || []).join("、") || mistake.primary || "暂无稳定错因"}</p>
    <p>负反馈：${strategy.recent_negative_feedback_count || memory.not_useful_count || 0} 次</p>
    <p>观察池：${memory.watchlist_count ?? "--"} 个；历史轮次：${memory.turn_count ?? "--"}</p>
  `;
}

function explainLastAudit() {
  if (!auditExplainPanel) return;
  const reply = lastBackendReply;
  if (!reply?.decision_audit) {
    auditExplainPanel.textContent = "暂无可复盘回答。";
    return;
  }
  const audit = reply.decision_audit;
  const lineage = (reply.data_lineage || []).filter((item) => item.available).map((item) => item.name).join("、") || "无实时数据";
  const risk = (reply.risk_veto?.reasons || []).join("；") || "未触发风控否决";
  auditExplainPanel.innerHTML = `
    <p>为什么是这个结论：${(audit.evidence || []).slice(0, 5).join("；") || "证据不足"}</p>
    <p>使用数据：${lineage}</p>
    <p>风控检查：${risk}</p>
    <p>仓位上限：${audit.position_limit || "--"}</p>
  `;
}

function updateAuditReplayPanel(items) {
  if (!auditReplayPanel || !Array.isArray(items)) return;
  if (items.length === 0) {
    auditReplayPanel.textContent = "暂无历史审计。";
    return;
  }
  auditReplayPanel.innerHTML = items.map((item) => `
    <div class="replay-item">
      <b>${item.final_label || "--"} ${item.final_score ?? "--"}/100</b>
      <span>${item.question || "--"}</span>
      <em>${item.id || "--"}｜原始：${item.original_label || "--"}｜仓位：${item.position_limit || "--"}</em>
    </div>
  `).join("");
}

function updateSourceHealthPanel(items) {
  if (!sourceHealthPanel || !Array.isArray(items)) return;
  if (items.length === 0) {
    sourceHealthPanel.textContent = "暂无接口调用记录。";
    return;
  }
  sourceHealthPanel.innerHTML = items.map((item) => `
    <div class="health-item ${item.status === "健康" ? "ok" : "warn"}">
      <b>${item.source}</b>
      <span>${item.status}｜成功率 ${item.success_rate == null ? "--" : Math.round(item.success_rate * 100) + "%"}｜均耗时 ${item.avg_elapsed_ms ?? "--"}ms</span>
      <em>${item.last_error ? `最近错误：${item.last_error}` : "最近错误：无"}</em>
    </div>
  `).join("");
}

function updateWatchAlertsPanel(alerts, isTradingTime) {
  if (!watchAlertsPanel || !Array.isArray(alerts)) return;
  if (alerts.length === 0) {
    watchAlertsPanel.textContent = `${isTradingTime ? "盘中" : "非盘中"}：暂无触发/失效提醒。`;
    return;
  }
  watchAlertsPanel.innerHTML = alerts.map((item) => `
    <div class="alert-item ${item.level}">
      <b>${item.name || item.code}｜${item.status}</b>
      <span>${formatNumber(item.price)} / ${formatNumber(item.change_percent)}%</span>
      <p>${item.message}</p>
    </div>
  `).join("");
}

function updateWatchlistPanel(items) {
  if (!watchlistPanel || !Array.isArray(items)) return;
  if (items.length === 0) {
    watchlistPanel.textContent = "暂无观察标的。可说“把豫能控股加入观察池”。";
    return;
  }

  watchlistPanel.innerHTML = items
    .map((item) => {
      const pct = item.change_percent ?? item.changePercent;
      const className = pct >= 0 ? "quote-up" : "quote-down";
      return `
        <div class="watch-item">
          <div><b>${item.name || item.code}</b><span class="${className}">${formatNumber(item.price)} / ${formatNumber(pct)}%</span></div>
          <p>触发：${item.trigger_condition || "放量转强再看"}</p>
          <p>失效：${item.invalid_condition || "跌破关键位移出"}</p>
          <p>复盘：${item.auto_review?.status || "待复盘"} ${item.auto_review?.detail || ""}</p>
        </div>
      `;
    })
    .join("");
}

function updatePremarketPlanPanel(plan) {
  if (!premarketPlanPanel || !plan) return;
  const mainlines = (plan.mainlines || []).length ? plan.mainlines.join("、") : "暂无明确主线";
  const blockers = (plan.blockers || []).map((item) => `<p>${item}</p>`).join("");
  const watchActions = (plan.watch_actions || [])
    .map((item) => `<p><b>${item.name || item.code}</b>：${item.plan}</p>`)
    .join("");
  premarketPlanPanel.innerHTML = `
    <div class="decision-head"><b>${plan.stance}</b><span>${plan.temperature}/100</span></div>
    <p>${plan.summary}</p>
    <p>主线观察：${mainlines}</p>
    <p>角色：${plan.role}；${plan.position_rule}</p>
    <div class="profile-blockers">${blockers}</div>
    ${watchActions ? `<div class="watch-plan"><strong>观察池计划</strong>${watchActions}</div>` : ""}
  `;
}

async function callBackendChat(question) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId, role: roleSelect?.value || "cycle" }),
  });

  if (!response.ok) throw new Error("后端接口不可用");
  return await response.json();
}

function startAgentProgress(question) {
  if (!("EventSource" in window)) return null;
  const url = `/api/chat/progress?question=${encodeURIComponent(question)}&session_id=${encodeURIComponent(sessionId)}&role=${encodeURIComponent(roleSelect?.value || "cycle")}`;
  const source = new EventSource(url);
  source.onmessage = (event) => {
    try {
      const progress = JSON.parse(event.data);
      setAvatarState(progress.state || "thinking", progress.intent || "Agent 执行中", progress.gesture || "调度工具");
      voiceText.textContent = progress.detail || "Agent 执行中...";
    } catch (error) {
      voiceText.textContent = "Agent 执行中...";
    }
  };
  source.addEventListener("done", () => source.close());
  source.onerror = () => source.close();
  return source;
}

function sendFeedback(question, answer, useful) {
  fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, question, answer, useful }),
  }).catch(() => {});
}

async function loadWatchlist() {
  try {
    const response = await fetch(`/api/watchlist?session_id=${encodeURIComponent(sessionId)}`);
    if (!response.ok) return;
    const data = await response.json();
    updateWatchlistPanel(data.items || []);
  } catch (error) {
    // 静态模式下没有后端，保持默认提示即可。
  }
}

async function loadUserProfile() {
  try {
    const response = await fetch(`/api/profile/summary?session_id=${encodeURIComponent(sessionId)}`);
    if (!response.ok) return;
    const data = await response.json();
    updateUserProfilePanel(data);
  } catch (error) {
    // 后端未启动时保持默认占位。
  }
}

async function loadMarketDashboard() {
  try {
    const response = await fetch("/api/market-dashboard");
    if (!response.ok) return;
    const data = await response.json();
    updateEmotionDashboard(data.dashboard);
    updateMainlineRank(data.mainline_rank || []);
    updateSourceHealthPanel(data.source_health || []);
  } catch (error) {
    // 后端未启动时保持默认占位。
  }
}

async function loadPremarketPlan() {
  try {
    const response = await fetch(`/api/premarket-plan?session_id=${encodeURIComponent(sessionId)}&role=${encodeURIComponent(roleSelect?.value || "cycle")}`);
    if (!response.ok) return;
    const data = await response.json();
    updatePremarketPlanPanel(data.plan);
    updateMainlineRank(data.mainline_rank || []);
    updateSourceHealthPanel(data.source_health || []);
  } catch (error) {
    if (premarketPlanPanel) premarketPlanPanel.textContent = "后端未启动，暂无法生成盘前计划。";
  }
}

async function loadAuditReplay() {
  try {
    const response = await fetch(`/api/audit/latest?session_id=${encodeURIComponent(sessionId)}&limit=5`);
    if (!response.ok) return;
    const data = await response.json();
    updateAuditReplayPanel(data.items || []);
  } catch (error) {
    // 后端未启动时保持默认占位。
  }
}

async function loadWatchAlerts() {
  try {
    const response = await fetch(`/api/watchlist/alerts?session_id=${encodeURIComponent(sessionId)}&role=${encodeURIComponent(roleSelect?.value || "cycle")}`);
    if (!response.ok) return;
    const data = await response.json();
    updateWatchlistPanel(data.items || []);
    updateWatchAlertsPanel(data.alerts || [], data.is_trading_time);
  } catch (error) {
    // 后端未启动时保持默认占位。
  }
}

async function loadSourceHealth() {
  try {
    const response = await fetch("/api/source-health");
    if (!response.ok) return;
    const data = await response.json();
    updateSourceHealthPanel(data.items || []);
  } catch (error) {
    // 后端未启动时保持默认占位。
  }
}

function splitSentences(text) {
  return text
    .replace(/【[^】]+】/g, "")
    .split(/(?<=[。！？；\n])/)
    .map((sentence) => sentence.trim())
    .filter(Boolean)
    .slice(0, 10);
}

function speak(text) {
  window.speechSynthesis?.cancel();
  speechInterrupted = false;
  speechQueue = splitSentences(text);

  if (muted || !("speechSynthesis" in window)) {
    voiceText.textContent = muted ? "语音关闭" : "浏览器不支持";
    return;
  }

  speakNextSentence();
}

function speakNextSentence() {
  if (speechInterrupted || speechQueue.length === 0) {
    stopSpeakingAnimation();
    return;
  }

  const sentence = speechQueue.shift();
  const utterance = new SpeechSynthesisUtterance(sentence);
  utterance.lang = "zh-CN";
  utterance.rate = /别|不|风险|回避|止损/.test(sentence) ? 0.92 : 1.03;
  utterance.pitch = /机会|龙头|吃肉/.test(sentence) ? 0.96 : 0.86;
  utterance.volume = 1;

  utterance.onstart = () => {
    avatar.classList.add("speaking");
    voiceText.textContent = "正在播报";
  };

  utterance.onend = () => {
    window.setTimeout(speakNextSentence, 260 + Math.random() * 260);
  };
  utterance.onerror = () => stopSpeakingAnimation();

  window.speechSynthesis.speak(utterance);
}

function stopSpeakingAnimation() {
  avatar.classList.remove("speaking");
  voiceText.textContent = muted ? "语音关闭" : "播报完成";
}

async function answerQuestion(question) {
  interruptDigitalHuman();
  addMessage("user", question);
  setAvatarState("thinking", "理解中", "分析语义");
  const progressSource = startAgentProgress(question);

  try {
    await startThinking(question);
    try {
      const backendReply = await callBackendChat(question);
      lastBackendReply = backendReply;
      progressSource?.close();
      setBackendAvatarState(backendReply);
      addAgentTrace(backendReply.agent_steps);
      addMessage("bot", backendReply.text, question);
      updateMarketSnapshot(backendReply);
      updateDecisionPanel(backendReply);
      if (backendReply.watchlist) updateWatchlistPanel(backendReply.watchlist);
      updateEmotionDashboard(backendReply.market_dashboard);
      updateMainlineRank(backendReply.mainline_rank || []);
      updateConfidencePanel(backendReply.answer_confidence);
      updateRiskVetoPanel(backendReply.risk_veto);
      updateLineagePanel(backendReply.data_lineage || []);
      updateAuditPanel(backendReply.decision_audit);
      updateUserProfilePanel(backendReply.strategy_memory);
      updateSourceHealthPanel(backendReply.source_health || []);
      loadAuditReplay();
      loadWatchAlerts();
      loadUserProfile();
      speak(backendReply.text);
      return;
    } catch (error) {
      progressSource?.close();
      marketSnapshot.textContent = "后端未启动，已切换浏览器本地分析模式。";
    }

    const stock = await resolveStock(question).catch((error) => {
      if (/涨|买|卖|分析|股票|控股|股份|科技|能源|证券|银行|药业/.test(question)) {
        throw error;
      }

      return null;
    });
    let reply;
    if (stock?.code) {
      marketSnapshot.textContent = `已识别 ${stock.name || stock.code}（${stock.code}），正在查询行情...`;
      const quote = await fetchQuote(stock.code);
      if (stock.name && quote.name === "未知证券") quote.name = stock.name;
      reply = buildQuoteReply(question, quote);
      updateMarketSnapshot(reply);
    } else {
      await new Promise((resolve) => window.setTimeout(resolve, 360));
      reply = buildReply(question);
    }

    setAvatarState(reply.mood, reply.intent, reply.gesture);
    addMessage("bot", reply.text, question);
    speak(reply.text);
  } catch (error) {
    progressSource?.close();
    const text = `${personaPrefix}\n\n行情数据暂时没有取到：${error.message}。我先按通用短线框架说：不要在信息不完整时强行决策，先确认指数环境、板块主线、个股地位和量价承接，再考虑是否进入交易计划。${riskSuffix}`;
    marketSnapshot.textContent = "行情查询失败，可稍后重试或检查网络。";
    setAvatarState("alert", "行情异常", "等待数据");
    addMessage("bot", text, question);
    speak(text);
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();

  if (!question) return;

  questionInput.value = "";
  answerQuestion(question);
});

questionInput.addEventListener("input", () => {
  if (questionInput.value.trim()) interruptDigitalHuman();
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    answerQuestion(button.dataset.question);
  });
});

muteBtn.addEventListener("click", () => {
  muted = !muted;
  muteBtn.textContent = muted ? "开启语音" : "关闭语音";
  window.speechSynthesis?.cancel();
  stopSpeakingAnimation();
});

roleSelect?.addEventListener("change", () => {
  loadPremarketPlan();
  loadWatchAlerts();
});

auditExplainBtn?.addEventListener("click", explainLastAudit);

function startIdleMotion() {
  clearInterval(idleMotionTimer);
  idleMotionTimer = window.setInterval(() => {
    if (avatar.classList.contains("speaking") || avatar.classList.contains("thinking")) return;
    avatar.classList.add("idle-live");
    window.setTimeout(() => avatar.classList.remove("idle-live"), 1800);
  }, 3600 + Math.random() * 1600);
}

function startWatchAlertPolling() {
  clearInterval(watchAlertTimer);
  watchAlertTimer = window.setInterval(() => {
    loadWatchAlerts();
    loadSourceHealth();
  }, 90000);
}

startIdleMotion();
startWatchAlertPolling();
loadPremarketPlan();
loadWatchlist();
loadUserProfile();
loadMarketDashboard();
loadAuditReplay();
loadWatchAlerts();

window.addEventListener("beforeunload", () => {
  window.speechSynthesis?.cancel();
});
