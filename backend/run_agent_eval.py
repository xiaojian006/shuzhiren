import json
from pathlib import Path

import re

from backend.main import LOCAL_STOCK_ALIASES, build_tool_plan, detect_intent, evaluate_risk_veto, parse_user_state


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise AssertionError(message)


def resolve_stock_local(question: str) -> dict[str, str] | None:
    code_match = re.search(r"\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b", question)
    if code_match:
        return {"code": code_match.group(1), "name": "", "source": "直接代码"}
    for name, code in LOCAL_STOCK_ALIASES.items():
        if name in question:
            return {"code": code, "name": name, "source": "本地名称表"}
    return None


def main() -> None:
    cases = json.loads((ROOT / "backend" / "agent_eval_cases.json").read_text(encoding="utf-8"))
    failures = []
    for index, case in enumerate(cases, 1):
        question = case["question"]
        try:
            intent = detect_intent(question)
            if case.get("intent_contains") and case["intent_contains"] not in intent:
                fail(f"intent expected {case['intent_contains']}, got {intent}")

            stock = resolve_stock_local(question)
            if case.get("stock_code") and (not stock or stock.get("code") != case["stock_code"]):
                fail(f"stock expected {case['stock_code']}, got {stock}")

            user_state = parse_user_state(question)
            for key, value in (case.get("user_state") or {}).items():
                if user_state.get(key) != value:
                    fail(f"user_state.{key} expected {value}, got {user_state.get(key)}")

            tool_plan = build_tool_plan(question, intent, stock, user_state)
            for key, value in (case.get("tools") or {}).items():
                if tool_plan.get(key) is not value:
                    fail(f"tool {key} expected {value}, got {tool_plan.get(key)}")

            risk = evaluate_risk_veto(question, None, None, None, [], None, None, user_state)
            for word in case.get("risk_words") or []:
                if word not in "；".join(risk.get("reasons", [])):
                    fail(f"risk word {word} missing in {risk.get('reasons', [])}")
        except Exception as error:
            failures.append(f"{index}. {question}: {error}")

    if failures:
        print("Agent eval failed:")
        print("\n".join(failures))
        raise SystemExit(1)

    print(f"Agent eval passed: {len(cases)} cases")


if __name__ == "__main__":
    main()
