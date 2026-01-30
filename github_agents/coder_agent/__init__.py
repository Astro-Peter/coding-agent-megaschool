"""Coder agent package.

Entrypoints:
- run_from_plan: Plan-based entrypoint (loads plan from issue comments)
- run_from_pr_comments: PR comments-based entrypoint (commits into existing PR)
"""

from github_agents.coder_agent.run_from_plan import run_coder, run_coder_async
from github_agents.coder_agent.run_from_pr_comments import (
    run_coder_from_pr,
    run_coder_from_pr_async,
)

__all__ = [
    "run_coder",
    "run_coder_async",
    "run_coder_from_pr",
    "run_coder_from_pr_async",
]
