from loopguard.pricing import cost_for


def test_known_model_cost_is_token_weighted():
    # gpt-oss-120b = ($0.25/M in, $0.69/M out)
    cost = cost_for("cerebras/gpt-oss-120b", 1_000_000, 1_000_000)
    assert round(cost, 4) == round(0.25 + 0.69, 4)


def test_bare_model_name_resolves_same_as_prefixed():
    assert cost_for("gpt-oss-120b", 2000, 1000) == cost_for("cerebras/gpt-oss-120b", 2000, 1000)


def test_unknown_model_returns_zero():
    assert cost_for("totally-made-up-model", 5000, 5000) == 0.0
