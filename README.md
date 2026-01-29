# Megaschool Coding Agents

Automated SDLC agent system with planner, coder, and reviewer agents for GitHub.

## Overview

This system automates the software development lifecycle:

1. **Planner Agent** - Analyzes issues and creates implementation plans
2. **Coder Agent** - Implements code changes and creates pull requests
3. **Reviewer Agent** - Reviews PRs and provides feedback

## Quick Start

### Option 1: Docker (recommended)

```bash
cp .env.example .env
# Edit .env with your values (GITHUB_TOKEN, LLM_API_TOKEN, etc.)
docker-compose up -d
```

### Option 2: Local Python

```bash
pip install -e .
cp .env.example .env
# Edit .env with your values
./scripts/run_watcher.sh
```

## GitHub Actions

The repository includes workflows that run automatically:

- **Issue workflow** (`.github/workflows/issue.yml`):
  - Triggers on new issue creation (runs planner)
  - Triggers on `/plan` comment (re-runs planner)
  - Triggers on `/code` comment (runs coder)

- **PR workflow** (`.github/workflows/pr.yml`):
  - Triggers on PR creation/update (runs reviewer)

### Required Secrets

Configure these in your repository settings:

- `LLM_API_TOKEN` - API token for the LLM provider

### Optional Variables

- `LLM_PROVIDER` - `openai` (default) or `yandex`
- `LLM_API_URL` - API endpoint URL
- `OPENAI_MODEL` - Model name (default: `gpt-4o-mini`)

## Commands

Use these commands in issue comments:

- `/plan` - Creates or updates the implementation plan
- `/code` - Implements the plan and creates a PR

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub API token | Required |
| `GITHUB_REPOSITORY` | Repository in `owner/repo` format | Required |
| `LLM_PROVIDER` | LLM provider (`openai` or `yandex`) | `openai` |
| `LLM_API_TOKEN` | LLM API token | Required |
| `LLM_API_URL` | LLM API endpoint | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Model to use | `gpt-4o-mini` |
| `POLL_SECONDS` | Polling interval for watcher | `15` |
