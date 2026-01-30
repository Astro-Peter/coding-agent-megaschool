# Megaschool Coding Agents

Automated SDLC agent system for GitHub that handles the full software development lifecycle: from issue analysis to code implementation to code review.

## Overview

This system automates the software development lifecycle using AI agents:

1. **Planner Agent** - Analyzes issues and creates implementation plans
2. **Coder Agent** - Implements code changes and creates pull requests (CLI tool)
3. **Reviewer Agent** - Reviews PRs, analyzes CI results, and provides feedback (runs in GitHub Actions)

### SDLC Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  1. User creates Issue ──► Planner Agent creates plan              │
│                                    │                                │
│                                    ▼                                │
│  2. Coder Agent implements plan ──► Creates Pull Request           │
│                                          │                          │
│                                          ▼                          │
│  3. CI runs quality checks ──► Reviewer Agent analyzes PR          │
│                                          │                          │
│                            ┌─────────────┴─────────────┐            │
│                            │                           │            │
│                            ▼                           ▼            │
│                    APPROVED ──► Ready        CHANGES_REQUESTED      │
│                    for merge                         │              │
│                                                      ▼              │
│                                    4. Coder Agent fixes issues      │
│                                              │                      │
│                                              └──► Back to step 3    │
│                                                                     │
│                    (Max 5 iterations before forced approval)        │
└─────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Option 1: Docker (recommended)

```bash
cp .env.example .env
# Edit .env with your values (GH_TOKEN, LLM_API_TOKEN, etc.)
docker-compose up -d
```

### Option 2: Local Python

```bash
pip install -e .
cp .env.example .env
# Edit .env with your values
./scripts/run_watcher.sh
```

### Option 3: GitHub Actions Only

The system can run entirely in GitHub Actions:

1. Configure repository secrets (see below)
2. Create an issue with your task description
3. The workflow automatically:
   - Runs the Planner Agent on issue creation
   - Runs the Coder Agent to implement the plan
   - Runs the Reviewer Agent on PR creation
   - Loops until approved or max iterations reached

## Execution Models

### Local Watcher Mode (Default)

The watcher runs locally and orchestrates all agents:

- **Planner**: Runs locally when new issues are detected
- **Coder**: Runs locally, implements changes, creates PRs
- **Reviewer**: Runs in GitHub Actions on PR events

Start with: `docker-compose up -d` or `./scripts/run_watcher.sh`

### Full GitHub Actions Mode

All agents run in GitHub Actions:

- Planner runs on issue creation
- Coder runs automatically after plan or via `/code` command
- Reviewer runs on PR creation/update and after CI checks complete

No local setup needed - just configure secrets and create issues.

## GitHub Actions Workflows

### Issue Workflow (`.github/workflows/issue.yml`)

Triggers:
- **New issue created**: Runs Planner, then automatically runs Coder
- **`/plan` comment**: Re-runs Planner
- **`/code` comment**: Manually triggers Coder

### PR Workflow (`.github/workflows/pr.yml`)

Triggers:
- **PR created/updated**: Runs Reviewer Agent
- **CI checks complete**: Re-runs Reviewer with CI results

### CI Workflow (`.github/workflows/ci.yml`)

Triggers:
- **PR created/updated**: Runs quality checks (ruff, black, mypy, pytest)
- **Push to main**: Runs quality checks

## Configuration

### Required Secrets

Configure these in your repository settings (`Settings > Secrets and variables > Actions`):

| Secret | Description |
|--------|-------------|
| `LLM_API_TOKEN` | API token for the LLM provider (OpenAI, etc.) |

Note: The workflows use GitHub's built-in `GITHUB_TOKEN` secret, mapped to `GH_TOKEN` for the agents.

### Optional Variables

Configure in `Settings > Secrets and variables > Actions > Variables`:

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | `openai` or `yandex` | `openai` |
| `LLM_API_URL` | API endpoint URL | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Model name | `gpt-4o-mini` |

### Environment Variables (Local/Docker)

| Variable | Description | Default |
|----------|-------------|---------|
| `GH_TOKEN` | GitHub API token (with repo access) | Required |
| `GH_REPOSITORY` | Repository in `owner/repo` format | Required |
| `LLM_PROVIDER` | LLM provider (`openai` or `yandex`) | `openai` |
| `LLM_API_TOKEN` | LLM API token | Required |
| `LLM_API_URL` | LLM API endpoint | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Model to use | `gpt-4o-mini` |
| `POLL_SECONDS` | Polling interval for watcher | `15` |
| `AUTO_CODE_AFTER_PLAN` | Auto-run coder after plan | `true` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Commands

Use these commands in issue comments:

- `/plan` - Creates or updates the implementation plan
- `/code` - Implements the plan and creates a PR

## Iteration Limits

The system has built-in safeguards against infinite loops:

- **Coder Agent**: Maximum 5 development iterations (`MAX_DEV_ITERATIONS`)
- **Reviewer Agent**: Maximum 5 review iterations before forced approval (`MAX_ITERATIONS`)
- **Agent Loop**: Maximum 50 LLM calls per agent run (`MAX_AGENT_ITERATIONS`)

After max iterations, the Reviewer will force-approve with warnings.

## Development

### Install Dev Dependencies

```bash
pip install -e ".[dev]"
```

### Run Quality Checks

```bash
# Linting
ruff check agents/ scripts/ tests/

# Formatting
black agents/ scripts/ tests/

# Type checking
mypy agents/

# Tests
pytest tests/ -v
```

## Project Structure

```
megaschool/
├── .github/workflows/     # GitHub Actions workflows
│   ├── issue.yml          # Issue/Planner/Coder workflow
│   ├── pr.yml             # PR Review workflow
│   └── ci.yml             # Quality checks workflow
├── agents/
│   ├── common/            # Shared utilities
│   │   ├── github_client.py
│   │   ├── openai_client.py
│   │   └── code_index.py
│   ├── planner_agent/     # Issue analysis and planning
│   ├── coder_agent/       # Code implementation
│   ├── reviewer_agent/    # PR review
│   └── orchestrator.py    # Agent coordination
├── scripts/
│   ├── watcher.py         # Event polling and orchestration
│   └── run_watcher.sh
├── tests/                 # Test files
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## Example Usage

### Creating an Issue

Create an issue with a clear task description:

```
Title: Add user authentication endpoint

Body:
Implement a /login endpoint that:
- Accepts username and password
- Validates credentials
- Returns a JWT token on success
- Returns 401 on failure
```

The system will:
1. Planner Agent analyzes the issue and creates an implementation plan
2. Coder Agent implements the changes and creates a PR
3. CI runs quality checks
4. Reviewer Agent reviews the PR and CI results
5. If issues found, Coder Agent fixes them
6. Repeat until approved

### Example PR Comment from Reviewer

The Reviewer Agent posts:
- **PR Review**: Formal GitHub review (APPROVE or REQUEST_CHANGES)
- **PR Comment**: Detailed feedback with machine-readable data
- **Actions Summary**: Overview in the workflow run summary

## Troubleshooting

### Agent not triggering

- Check that `LLM_API_TOKEN` secret is set
- Verify `GH_TOKEN` has write permissions
- Check Actions logs for errors

### Infinite loop prevention

The system automatically stops after max iterations. Check:
- Issue labels for `iteration-N` to see current count
- PR comments for forced approval messages

### CI failures blocking approval

The Reviewer will request changes if CI is failing. Fix the CI issues first, or the Reviewer will continue to block.
