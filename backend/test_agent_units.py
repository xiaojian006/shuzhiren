from backend.main import build_tool_plan, evaluate_risk_veto, parse_user_state, resolve_stock


def test_parse_user_state_cost_and_position() -> None:
    state = parse_user_state("中际旭创成本 120，三成仓，怎么办？")
    assert state["cost"] == 120.0
    assert state["position_level"] == 0.3


def test_resolve_local_stock() -> None:
    stock = resolve_stock("帮我看一下豫能控股")
    assert stock and stock["code"] == "001896"


def test_tool_plan_market_question_skips_stock() -> None:
    plan = build_tool_plan("现在市场适合进攻还是防守？", "综合判断", None, {})
    assert plan["market"] is True
    assert plan["quote"] is False


def test_risk_veto_full_position() -> None:
    risk = evaluate_risk_veto("工业富联满仓了还能加吗？", None, None, None, [], None, None, {"position_level": 1.0})
    assert risk["blocked"] is True
    assert any("满仓" in item or "仓位" in item for item in risk["reasons"])


if __name__ == "__main__":
    test_parse_user_state_cost_and_position()
    test_resolve_local_stock()
    test_tool_plan_market_question_skips_stock()
    test_risk_veto_full_position()
    print("Unit tests passed: 4 cases")
