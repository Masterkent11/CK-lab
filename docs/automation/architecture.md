# Automation architecture

Python 3.12 and GitHub Actions run the repository workflow. Repository issues store backlog and audit data. GitHub Projects stores sprint assignments and board fields. Telegram receives selected alerts and summaries.

| Service | Module | Responsibility |
| --- | --- | --- |
| SprintPlanningService and ReportGenerator | `sprints.py` | Calculate the calendar, suggest scope and report capacity, blockers and velocity |
| IssueTriageService and TicketLifecycleService | `lifecycle.py` | Validate intake, track implementation and enforce QA transitions |
| PullRequestMonitor and QualityMonitor | `lifecycle.py` | Read GitHub verified PR links and associate failures |
| SecurityFindingService | `security.py` | Normalize, classify and update findings |
| ReminderService and TelegramNotificationAdapter | `notifications.py` | Remind assignees and deliver routed alerts |
| GitHubProjectAdapter | `projects.py` | Configure and update the board through GraphQL |

## Workflows

| Workflow | Trigger | Repository token permissions | On failure |
| --- | --- | --- | --- |
| Engineering CI | PR, push to master, manual | Contents read | Failing job and step appear in Actions |
| Issue and PR lifecycle | Issue events and PR events targeting the default branch | Contents read, Issues write, Pull requests read | Health workflow links to the run |
| Engineering operations | Manila schedule and manual dispatch | Contents read, Issues write | Health workflow links to the run |
| Security monitor | Monday and manual dispatch | Contents read, Issues write, Security events read | Health workflow links to the run. Scanner matches fail the job after triage |
| Automation health | A monitored workflow completes with a failure | Actions read, Contents read, Issues write, Pull requests read | Inspect the health job and delivery log |

The privileged workflows check out the default branch, read event JSON as data and ignore PR code and artifacts. CI can build PR code with a read only token and no configured automation secrets. Checkout credentials are removed from the job. Third party actions are pinned to commit SHAs. See [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

GitHub serializes mutation jobs through one repository concurrency group. Local writes should run during a maintenance window with workflow writes paused. The HTTP client backs off on transient reads and rate limits. It stops on ambiguous creates and lets a rerun reconcile existing state. REST and GraphQL operations have bounded pagination. Seed and finding writes use stable markers and a complete issue lookup across open and closed issues. Automated comments and delivery records use separate identifiers.

Actions supplies the `github-actions[bot]` identity for repository writes. Project mutations use the configured Project credential identity. Public issues never include scanner evidence that would disclose an affected path, dependency or secret. Log entries record operation, route, issue and outcome without credentials.
