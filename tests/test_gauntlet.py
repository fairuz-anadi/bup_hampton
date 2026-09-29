"""Tests for Policy Gauntlet Replay Runner and Scoreboard."""

from pathlib import Path

from gauntlet.runner import PolicyGauntletRunner
from gauntlet.scoreboard import Scoreboard


def test_policy_gauntlet_evaluation():
    runner = PolicyGauntletRunner()
    scenario_path = Path(__file__).resolve().parent.parent / "gauntlet" / "scenarios" / "01_normal_operations.yaml"
    res = runner.evaluate_scenario(str(scenario_path))
    assert res["scenario_id"] == "scenario-01-normal"
    assert res["passed"] is True
    assert "metrics" in res
    assert "deltas" in res

    md = Scoreboard.render_markdown_table(res)
    assert "Policy Gauntlet Scoreboard" in md
    assert "PASSED" in md
