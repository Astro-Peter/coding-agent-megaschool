from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime

import requests
from github import Github


@dataclass
class IssueData:
    number: int
    title: str
    body: str
    url: str
    created_at: datetime | None = None
    user_login: str | None = None
    is_pull_request: bool = False


@dataclass
class IssueCommentData:
    id: int
    body: str
    user_login: str
    created_at: datetime


@dataclass
class PullRequestData:
    number: int
    title: str
    body: str
    url: str
    head_ref: str
    updated_at: datetime
    head_sha: str = ""


@dataclass
class PullRequestFileData:
    filename: str
    status: str  # added, removed, modified, renamed, etc.
    additions: int
    deletions: int
    patch: str  # The diff patch for this file


@dataclass
class CheckRunAnnotation:
    """Annotation from a check run (usually a linter/test error)."""
    path: str
    start_line: int
    end_line: int
    annotation_level: str  # notice, warning, failure
    message: str
    title: str | None = None


@dataclass
class CheckRunData:
    id: int
    name: str
    status: str  # queued, in_progress, completed
    conclusion: str | None  # success, failure, neutral, cancelled, skipped, timed_out, action_required
    started_at: datetime | None
    completed_at: datetime | None
    html_url: str
    output_title: str | None = None
    output_summary: str | None = None
    annotations: list[CheckRunAnnotation] | None = None


@dataclass
class WorkflowRunData:
    """Data about a GitHub Actions workflow run."""
    id: int
    name: str
    status: str  # queued, in_progress, completed
    conclusion: str | None  # success, failure, neutral, cancelled, skipped, timed_out, action_required
    html_url: str
    head_sha: str
    run_attempt: int = 1


@dataclass 
class WorkflowJobData:
    """Data about a job within a workflow run."""
    id: int
    name: str
    status: str
    conclusion: str | None
    steps: list[dict] | None = None


@dataclass
class WorkflowLogData:
    """Parsed log data from a workflow run."""
    job_name: str
    log_content: str
    error_lines: list[str]  # Lines that look like errors


class GitHubClient:
    def __init__(self, token: str, repo_full_name: str) -> None:
        self._gh = Github(token)
        self._repo = self._gh.get_repo(repo_full_name)

    def get_issue(self, issue_number: int) -> IssueData:
        issue = self._repo.get_issue(number=issue_number)
        return IssueData(
            number=issue.number,
            title=issue.title or "",
            body=issue.body or "",
            url=issue.html_url,
            created_at=issue.created_at,
            user_login=issue.user.login if issue.user else "unknown",
            is_pull_request=issue.pull_request is not None,
        )

    def list_issue_comments(self, issue_number: int) -> list[IssueCommentData]:
        issue = self._repo.get_issue(number=issue_number)
        comments = []
        for comment in issue.get_comments():
            comments.append(
                IssueCommentData(
                    id=comment.id,
                    body=comment.body or "",
                    user_login=comment.user.login if comment.user else "unknown",
                    created_at=comment.created_at,
                )
            )
        return comments

    def list_issues(self, *, state: str = "open") -> list[IssueData]:
        issues = []
        for issue in self._repo.get_issues(state=state, sort="created", direction="desc"):
            issues.append(
                IssueData(
                    number=issue.number,
                    title=issue.title or "",
                    body=issue.body or "",
                    url=issue.html_url,
                    created_at=issue.created_at,
                    user_login=issue.user.login if issue.user else "unknown",
                    is_pull_request=issue.pull_request is not None,
                )
            )
        return issues

    def comment_issue(self, issue_number: int, body: str) -> None:
        issue = self._repo.get_issue(number=issue_number)
        issue.create_comment(body)

    def get_pull_request(self, pr_number: int) -> PullRequestData:
        pr = self._repo.get_pull(pr_number)
        return PullRequestData(
            number=pr.number,
            title=pr.title or "",
            body=pr.body or "",
            url=pr.html_url,
            head_ref=pr.head.ref,
            updated_at=pr.updated_at,
            head_sha=pr.head.sha,
        )

    def get_pull_request_diff(self, pr_number: int) -> str:
        """Get the unified diff for a pull request."""
        pr = self._repo.get_pull(pr_number)
        # PyGithub doesn't have a direct diff method, so we compare commits
        comparison = self._repo.compare(pr.base.sha, pr.head.sha)
        # Build diff from files
        diff_parts = []
        for file in comparison.files:
            diff_parts.append(f"--- a/{file.filename}")
            diff_parts.append(f"+++ b/{file.filename}")
            if file.patch:
                diff_parts.append(file.patch)
            diff_parts.append("")
        return "\n".join(diff_parts)

    def get_pull_request_files(self, pr_number: int) -> list[PullRequestFileData]:
        """Get list of files changed in a pull request."""
        pr = self._repo.get_pull(pr_number)
        files = []
        for file in pr.get_files():
            files.append(
                PullRequestFileData(
                    filename=file.filename,
                    status=file.status,
                    additions=file.additions,
                    deletions=file.deletions,
                    patch=file.patch or "",
                )
            )
        return files

    def get_check_runs(self, pr_number: int) -> list[CheckRunData]:
        """Get check runs (CI status) for a pull request."""
        pr = self._repo.get_pull(pr_number)
        commit = self._repo.get_commit(pr.head.sha)
        check_runs = []
        for check in commit.get_check_runs():
            check_runs.append(
                CheckRunData(
                    id=check.id,
                    name=check.name,
                    status=check.status,
                    conclusion=check.conclusion,
                    started_at=check.started_at,
                    completed_at=check.completed_at,
                    html_url=check.html_url,
                )
            )
        return check_runs

    def get_check_runs_with_details(self, pr_number: int) -> list[CheckRunData]:
        """Get check runs with detailed output and annotations for a pull request."""
        pr = self._repo.get_pull(pr_number)
        commit = self._repo.get_commit(pr.head.sha)
        check_runs = []
        for check in commit.get_check_runs():
            # Get annotations if available
            annotations = []
            try:
                for ann in check.get_annotations():
                    annotations.append(
                        CheckRunAnnotation(
                            path=ann.path,
                            start_line=ann.start_line,
                            end_line=ann.end_line,
                            annotation_level=ann.annotation_level,
                            message=ann.message,
                            title=ann.title,
                        )
                    )
            except Exception:
                pass  # Some check runs may not have annotations
            
            check_runs.append(
                CheckRunData(
                    id=check.id,
                    name=check.name,
                    status=check.status,
                    conclusion=check.conclusion,
                    started_at=check.started_at,
                    completed_at=check.completed_at,
                    html_url=check.html_url,
                    output_title=check.output.title if check.output else None,
                    output_summary=check.output.summary if check.output else None,
                    annotations=annotations if annotations else None,
                )
            )
        return check_runs

    def get_workflow_run_logs_url(self, run_id: int) -> str:
        """Get the URL to download workflow run logs."""
        return f"https://api.github.com/repos/{self._repo.full_name}/actions/runs/{run_id}/logs"

    def get_failed_check_runs(self, pr_number: int) -> list[CheckRunData]:
        """Get only the failed check runs for a pull request with details."""
        all_checks = self.get_check_runs_with_details(pr_number)
        return [
            check for check in all_checks
            if check.status == "completed" 
            and check.conclusion not in ("success", "skipped", "neutral")
        ]

    def list_pr_comments(self, pr_number: int) -> list[IssueCommentData]:
        """List comments on a pull request (issue comments, not review comments)."""
        pr = self._repo.get_pull(pr_number)
        comments = []
        for comment in pr.get_issue_comments():
            comments.append(
                IssueCommentData(
                    id=comment.id,
                    body=comment.body or "",
                    user_login=comment.user.login if comment.user else "unknown",
                    created_at=comment.created_at,
                )
            )
        return comments

    def add_issue_label(self, issue_number: int, label: str) -> None:
        """Add a label to an issue."""
        issue = self._repo.get_issue(number=issue_number)
        issue.add_to_labels(label)

    def get_issue_labels(self, issue_number: int) -> list[str]:
        """Get labels on an issue."""
        issue = self._repo.get_issue(number=issue_number)
        return [label.name for label in issue.get_labels()]

    def remove_issue_label(self, issue_number: int, label: str) -> None:
        """Remove a label from an issue."""
        issue = self._repo.get_issue(number=issue_number)
        try:
            issue.remove_from_labels(label)
        except Exception:
            pass  # Label may not exist

    def list_pull_requests(self, *, state: str = "open") -> list[PullRequestData]:
        prs = []
        for pr in self._repo.get_pulls(state=state, sort="updated", direction="desc"):
            prs.append(
                PullRequestData(
                    number=pr.number,
                    title=pr.title or "",
                    body=pr.body or "",
                    url=pr.html_url,
                    head_ref=pr.head.ref,
                    updated_at=pr.updated_at,
                    head_sha=pr.head.sha,
                )
            )
        return prs

    def comment_pull_request(self, pr_number: int, body: str) -> None:
        pr = self._repo.get_pull(pr_number)
        pr.create_issue_comment(body)

    def get_default_branch(self) -> str:
        return self._repo.default_branch

    def get_clone_url(self) -> str:
        return self._repo.clone_url

    def get_repo_full_name(self) -> str:
        return self._repo.full_name

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str | None = None,
    ) -> PullRequestData:
        """Create a pull request from head branch to base branch."""
        if base is None:
            base = self.get_default_branch()
        pr = self._repo.create_pull(title=title, body=body, head=head, base=base)
        return PullRequestData(
            number=pr.number,
            title=pr.title or "",
            body=pr.body or "",
            url=pr.html_url,
            head_ref=pr.head.ref,
            updated_at=pr.updated_at,
            head_sha=pr.head.sha,
        )

    def create_pull_request_review(
        self,
        pr_number: int,
        *,
        body: str,
        event: str,
    ) -> None:
        """Create a pull request review.
        
        Args:
            pr_number: The pull request number.
            body: The review body/summary.
            event: One of "APPROVE", "REQUEST_CHANGES", or "COMMENT".
        """
        pr = self._repo.get_pull(pr_number)
        pr.create_review(body=body, event=event)

    # --- Workflow Run Methods ---

    def get_workflow_runs_for_pr(self, pr_number: int) -> list[WorkflowRunData]:
        """Get workflow runs associated with a pull request."""
        pr = self._repo.get_pull(pr_number)
        head_sha = pr.head.sha
        
        runs = []
        # Get workflow runs for the head SHA
        for run in self._repo.get_workflow_runs(head_sha=head_sha):
            runs.append(
                WorkflowRunData(
                    id=run.id,
                    name=run.name or "",
                    status=run.status,
                    conclusion=run.conclusion,
                    html_url=run.html_url,
                    head_sha=run.head_sha,
                    run_attempt=run.run_attempt,
                )
            )
        return runs

    def get_failed_workflow_runs(self, pr_number: int) -> list[WorkflowRunData]:
        """Get only the failed workflow runs for a pull request."""
        all_runs = self.get_workflow_runs_for_pr(pr_number)
        return [
            run for run in all_runs
            if run.status == "completed" 
            and run.conclusion not in ("success", "skipped", "neutral")
        ]

    def get_workflow_run_jobs(self, run_id: int) -> list[WorkflowJobData]:
        """Get jobs for a specific workflow run."""
        run = self._repo.get_workflow_run(run_id)
        jobs = []
        for job in run.jobs():
            steps = []
            if hasattr(job, 'steps') and job.steps:
                for step in job.steps:
                    steps.append({
                        "name": step.name,
                        "status": step.status,
                        "conclusion": step.conclusion,
                        "number": step.number,
                    })
            jobs.append(
                WorkflowJobData(
                    id=job.id,
                    name=job.name,
                    status=job.status,
                    conclusion=job.conclusion,
                    steps=steps if steps else None,
                )
            )
        return jobs

    def download_workflow_run_logs(
        self, 
        run_id: int, 
        token: str | None = None,
        max_size_mb: int = 10,
    ) -> list[WorkflowLogData]:
        """Download and parse workflow run logs.
        
        Args:
            run_id: The workflow run ID
            token: GitHub token (uses instance token if not provided)
            max_size_mb: Maximum log size to download in MB
            
        Returns:
            List of WorkflowLogData with parsed logs for each job
        """
        if token is None:
            # Try to get token from the Github instance
            token = self._gh._Github__requester._Requester__auth.token
        
        logs_url = f"https://api.github.com/repos/{self._repo.full_name}/actions/runs/{run_id}/logs"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        
        try:
            response = requests.get(logs_url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            
            # Check content length
            content_length = int(response.headers.get('content-length', 0))
            if content_length > max_size_mb * 1024 * 1024:
                return [WorkflowLogData(
                    job_name="error",
                    log_content=f"Logs too large ({content_length / 1024 / 1024:.1f} MB)",
                    error_lines=[],
                )]
            
            # Download and extract zip
            zip_content = io.BytesIO(response.content)
            logs = []
            
            with zipfile.ZipFile(zip_content, 'r') as zip_file:
                for file_name in zip_file.namelist():
                    if file_name.endswith('.txt'):
                        # Parse job name from file path (format: job_name/step_name.txt)
                        job_name = file_name.split('/')[0] if '/' in file_name else file_name
                        
                        try:
                            content = zip_file.read(file_name).decode('utf-8', errors='replace')
                            
                            # Truncate very long logs
                            max_chars = 50000
                            if len(content) > max_chars:
                                content = content[:max_chars] + "\n... (truncated)"
                            
                            # Extract error-looking lines
                            error_lines = self._extract_error_lines(content)
                            
                            logs.append(WorkflowLogData(
                                job_name=job_name,
                                log_content=content,
                                error_lines=error_lines,
                            ))
                        except Exception:
                            pass
            
            return logs
            
        except requests.exceptions.RequestException as e:
            return [WorkflowLogData(
                job_name="error",
                log_content=f"Failed to download logs: {e}",
                error_lines=[],
            )]

    def _extract_error_lines(self, log_content: str) -> list[str]:
        """Extract lines that look like errors from log content."""
        error_patterns = [
            r'(?i)^.*error[:\s].*$',
            r'(?i)^.*failed[:\s].*$',
            r'(?i)^.*exception[:\s].*$',
            r'(?i)^.*traceback.*$',
            r'^.*Error:.*$',
            r'^.*FAILED.*$',
            r'^E\s+.*$',  # pytest error lines
            r'^\s*File ".*", line \d+',  # Python tracebacks
            r'(?i)^.*cannot find.*$',
            r'(?i)^.*not found.*$',
            r'(?i)^.*undefined.*$',
            r'(?i)^.*syntax error.*$',
            r'(?i)^.*type error.*$',
            r'(?i)^.*name error.*$',
            r'(?i)^.*import error.*$',
            r'(?i)^.*assertion.*failed.*$',
            r'(?i)^.*exit code [1-9].*$',
        ]
        
        error_lines = []
        for line in log_content.split('\n'):
            line = line.strip()
            if not line:
                continue
            for pattern in error_patterns:
                if re.match(pattern, line):
                    # Clean up timestamp prefixes from GitHub logs
                    cleaned = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*', '', line)
                    if cleaned and len(cleaned) < 500:  # Skip very long lines
                        error_lines.append(cleaned)
                    break
        
        # Deduplicate while preserving order
        seen = set()
        unique_errors = []
        for line in error_lines:
            if line not in seen:
                seen.add(line)
                unique_errors.append(line)
        
        return unique_errors[:50]  # Limit to 50 error lines

    def get_failed_workflow_logs(
        self, 
        pr_number: int,
        token: str | None = None,
    ) -> dict[str, list[WorkflowLogData]]:
        """Get logs for all failed workflow runs on a PR.
        
        Returns:
            Dict mapping workflow run name to list of log data
        """
        failed_runs = self.get_failed_workflow_runs(pr_number)
        
        all_logs = {}
        for run in failed_runs:
            logs = self.download_workflow_run_logs(run.id, token=token)
            if logs:
                all_logs[run.name] = logs
        
        return all_logs
