from aegis.config.harnesses import merge_harnesses, HarnessRegistration
from aegis.tui.config_panel import _harness_options


def test_harness_options_include_registered():
    opts = _harness_options(merge_harnesses({}))
    labels = [label for label, _value in opts]
    assert any("opencode" in lbl for lbl in labels)


def test_harness_options_carry_name_as_value():
    hn = merge_harnesses({
        "openrouter": HarnessRegistration(
            name="openrouter", driver="lovelaice")})
    opts = _harness_options(hn)
    values = [value for _label, value in opts]
    assert "openrouter" in values
