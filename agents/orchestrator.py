from __future__ import annotations

from agents.common.github_client import GitHubClient
from agents.coder_agent.runner import run_coder
from agents.planner_agent.cli import run_planner
from agents.reviewer_agent.runner import run_reviewer


class Orchestrator:
    def __init__(self, *, client: GitHubClient, openai_client, model: str) -> None:
        self._client = client
        self._openai_client = openai_client
        self._model = model

    def plan(self, *, issue_number: int, command: str | None = None) -> None:
        run_planner(
            client=self._client,
            issue_number=issue_number,
            openai_client=self._openai_client,
            model=self._model,
            plan_command=command,
        )

    def code(
        self,
        *,
        issue_number: int,
        reviewer_feedback: list[str] | None = None,
    ) -> None:
        """Run the coder agent to implement changes for an issue."""
        run_coder(
            client=self._client,
            issue_number=issue_number,
            openai_client=self._openai_client,
            model=self._model,
            reviewer_feedback=reviewer_feedback,
        )

    def review(self, *, pr_number: int) -> None:
        """Run the reviewer agent to review a pull request."""
        run_reviewer(
            client=self._client,
            pr_number=pr_number,
            openai_client=self._openai_client,
            model=self._model,
        )
