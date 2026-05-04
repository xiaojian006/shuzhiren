import json
import re
from pathlib import Path

from backend.agent_critic import critique_answer
from backend.llm_output import parse_structured_llm


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cases = json.loads((ROOT / "backend" / "response_quality_cases.json").read_text(encoding="utf-8"))
    failures = []
    for case in cases:
        answer = case["answer"]
        for text in case.get("must_contain", []):
            if text not in answer:
                failures.append(f"{case['name']}: missing {text}")
        pattern = case.get("must_not_match")
        if pattern and re.search(pattern, answer):
            failures.append(f"{case['name']}: forbidden pattern {pattern}")
        critic = critique_answer(answer, case.get("decision"), case.get("risk_veto", {"blocked": False}), case.get("confidence", {"score": 80}))
        if case.get("critic_should_pass", True) and not critic["passed"]:
            failures.append(f"{case['name']}: critic failed {critic['issues']}")

    valid_llm = parse_structured_llm(
        '{"verdict":"等待确认","reasoning_summary":"数据完整但条件未触发","trigger":"放量站回关键位","invalid":"跌破低点","position_limit":"0 成","risk_flags":[],"follow_up":"补成本仓位"}',
        {"position_limit": "0 成"},
    )
    if not valid_llm:
        failures.append("structured_llm: valid json rejected")
    invalid_llm = parse_structured_llm(
        '{"verdict":"一定涨，确定买","reasoning_summary":"强","trigger":"现在","invalid":"无","position_limit":"满仓","risk_flags":[]}',
        {"position_limit": "0 成"},
    )
    if invalid_llm:
        failures.append("structured_llm: forbidden json accepted")
    if failures:
        print("Response quality eval failed:")
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"Response quality eval passed: {len(cases)} cases")


if __name__ == "__main__":
    main()
