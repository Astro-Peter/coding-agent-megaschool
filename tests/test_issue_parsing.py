"""Tests for planner agent plan output."""
import pytest

from github_agents.planner_agent.agent import Plan


def test_plan_model_structure():
    """Test that Plan model has the expected structure."""
    plan = Plan(summary="Do X", steps=["A", "B"])
    assert plan.summary == "Do X"
    assert plan.steps == ["A", "B"]


def test_plan_model_validation():
    """Test Plan model validates required fields."""
    with pytest.raises(Exception):
        Plan(steps=["A", "B"])  # Missing summary


def test_plan_model_allows_empty_steps():
    """Test Plan model allows empty steps list."""
    plan = Plan(summary="Empty plan", steps=[])
    assert plan.steps == []
