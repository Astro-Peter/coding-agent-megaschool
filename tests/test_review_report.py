"""Tests for reviewer agent review decision output."""
import pytest

from github_agents.reviewer_agent.agent import ReviewDecision


def test_review_decision_approved():
    """Test ReviewDecision for approved PRs."""
    decision = ReviewDecision(
        status="APPROVED",
        summary="Looks good!",
        issues=[],
        suggestions=["Consider adding tests"],
    )
    assert decision.status == "APPROVED"
    assert decision.summary == "Looks good!"
    assert decision.issues == []
    assert decision.suggestions == ["Consider adding tests"]


def test_review_decision_changes_requested():
    """Test ReviewDecision for PRs needing changes."""
    decision = ReviewDecision(
        status="CHANGES_REQUESTED",
        summary="Needs work",
        issues=["Missing error handling", "No tests"],
    )
    assert decision.status == "CHANGES_REQUESTED"
    assert len(decision.issues) == 2


def test_review_decision_status_validation():
    """Test ReviewDecision validates status values."""
    with pytest.raises(Exception):
        ReviewDecision(
            status="INVALID",
            summary="Test",
            issues=[],
        )
