"""Tests for Policy Gauntlet Replay Runner and Scoreboard."""

import pytest
from gauntlet.runner import PolicyGauntletRunner
from gauntlet.scoreboard import Scoreboard


def test_policy_gauntlet_evaluation():
    runner = PolicyGauntletRunner()
    res = runner.evaluate_scenario("/Users/turjo/Desktop/bup_hampton/gauntlet/scenarios/01_normal_operations.yaml")
    assert res["scenario_id"] == "scenario-01-normal"
    assert res["passed"] is True
    assert "metrics" in res
    assert "deltas" in res

    md = Scoreboard.render_markdown_table(res)
    assert "Policy Gauntlet Scoreboard" in md
    assert "PASSED" in md
