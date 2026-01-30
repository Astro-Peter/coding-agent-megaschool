"""Orchestrator for coordinating agents using OpenAI Agents SDK."""
from __future__ import annotations

import asyncio
from pathlib import Path

from github_agents.common.context import AgentContext
from github_agents.common.github_client import GitHubClient
from github_agents.common.sdk_config import configure_sdk
from github_agents.coder_agent.run_from_plan import run_coder, run_coder_async
from github_agents.planner_agent.agent import Plan, run_planner, run_planner_async
from github_agents.reviewer_agent.agent import (
    ReviewDecisionWithMeta,
    run_reviewer,
    run_reviewer_async,
)


class Orchestrator:
    """Orchestrates the planning, coding, and reviewing agents.
    
    Uses the OpenAI Agents SDK for agent execution with shared context.
    """
    
    def __init__(self, *, client: GitHubClient, model: str) -> None:
        """Initialize the orchestrator.
        
        Args:
            client: GitHub client for API operations.
            model: Model name to use for LLM calls.
        """
        self._client = client
        self._model = model
        
        # Configure the SDK (should be called once at startup)
        configure_sdk()
    
    def _create_context(
        self,
        *,
        issue_number: int | None = None,
        pr_number: int | None = None,
        workspace: Path | None = None,
        reviewer_feedback: list[str] | None = None,
    ) -> AgentContext:
        """Create an agent context with the given parameters."""
        return AgentContext(
            gh_client=self._client,
            model=self._model,
            issue_number=issue_number,
            pr_number=pr_number,
            workspace=workspace,
            reviewer_feedback=reviewer_feedback or [],
        )
    
    # --- Synchronous API ---
    
    def plan(self, *, issue_number: int, command: str | None = None) -> Plan:
        """Run the planner agent to create a plan for an issue.
        
        Args:
            issue_number: The GitHub issue number to plan for.
            command: Optional command that triggered the planning.
        
        Returns:
            The generated Plan.
        """
        context = self._create_context(issue_number=issue_number)
        return run_planner(context=context, plan_command=command)

    def code(
        self,
        *,
        issue_number: int,
        reviewer_feedback: list[str] | None = None,
    ) -> None:
        """Run the coder agent to implement changes for an issue.
        
        Args:
            issue_number: The GitHub issue number to implement.
            reviewer_feedback: Optional feedback from the reviewer to address.
        """
        context = self._create_context(
            issue_number=issue_number,
            reviewer_feedback=reviewer_feedback,
        )
        run_coder(context=context)

    def review(self, *, pr_number: int) -> ReviewDecisionWithMeta | None:
        """Run the reviewer agent to review a pull request.
        
        Args:
            pr_number: The pull request number to review.
        
        Returns:
            The review decision with metadata.
        """
        context = self._create_context(pr_number=pr_number)
        return run_reviewer(context=context)
    
    # --- Async API ---
    
    async def plan_async(self, *, issue_number: int, command: str | None = None) -> Plan:
        """Async version of plan()."""
        context = self._create_context(issue_number=issue_number)
        return await run_planner_async(context=context, plan_command=command)

    async def code_async(
        self,
        *,
        issue_number: int,
        reviewer_feedback: list[str] | None = None,
    ) -> None:
        """Async version of code()."""
        context = self._create_context(
            issue_number=issue_number,
            reviewer_feedback=reviewer_feedback,
        )
        await run_coder_async(context=context)

    async def review_async(self, *, pr_number: int) -> ReviewDecisionWithMeta | None:
        """Async version of review()."""
        context = self._create_context(pr_number=pr_number)
        return await run_reviewer_async(context=context)
    
    # --- Full SDLC Workflow ---
    
    async def run_sdlc_async(self, *, issue_number: int) -> None:
        """Run the full SDLC workflow: plan -> code -> review.
        
        This runs the complete software development lifecycle for an issue.
        
        Args:
            issue_number: The GitHub issue number to process.
        """
        # Step 1: Plan
        await self.plan_async(issue_number=issue_number)
        
        # Step 2: Code
        await self.code_async(issue_number=issue_number)
        
        # Note: Review is triggered separately by the PR creation
    
    def run_sdlc(self, *, issue_number: int) -> None:
        """Synchronous version of run_sdlc_async()."""
        asyncio.run(self.run_sdlc_async(issue_number=issue_number))
