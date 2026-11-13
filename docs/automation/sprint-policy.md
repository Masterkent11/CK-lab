# Sprint policy

Sprints are fourteen calendar days in Asia/Manila. The baseline is Sprint 0 from November 16 through 29, 2026, then Sprint 1 from November 30 through December 13. The calendar reads the start, duration, capacity and iteration horizon from `.github/automation/settings.json`.

The proposed team capacity is 21 points. Adjust it when staffing changes. Estimates use 1, 2, 3, 5 or 8 points. Intake checks the objective, description, acceptance criteria, estimate, priority, component, dependencies, testing and definition of done. An epic needs an objective, description and acceptance criteria. Use native dependency links. The issue form also accepts local issue references in Dependencies.

Set Status to Ready in Projects after grooming. The planning report also considers fully described Backlog work. Draft scope uses priority then issue number, excludes blocked items and stays within remaining capacity. Maintainers approve scope through the `approve` operation with a sprint number, explicit issues and Apply. Project editors can also assign Sprint in the board after review. Sprint 0 work starts as proposals. Carryover requires a new assignment decision. Review prior reports before reassigning issues because velocity is calculated from current iteration assignments.

Add reviewed templates to `approved_proposals` in `.github/automation/backlog.json`. Each entry uses a stable ID and an existing epic. The sprint start job can create the proposed tickets. Review existing unfinished work and the carryover list before adding a template.

## Lifecycle

| Event | Result |
| --- | --- |
| New issue | Backlog and metadata validation |
| Explicit PR link | Issue comment with PR link |
| Linked PR ready for review | In Review for eligible open tickets |
| Linked PR merged | QA/UAT if QA is required or the ticket remains open. Done for a completed closed issue without the QA gate |
| Maintainer adds `qa-approved` | Done and issue closed when blockers are resolved |
| Issue reopened | Backlog or Blocked |
| Maintainer adds `blocked` | Blocked, with a notification when configured |
| Issue closed as not planned or duplicate | Cancelled |

Set `qa-required` before merge. Review the evidence before adding `qa-approved`. GitHub closing references can close an issue at merge, while its Project status remains QA/UAT pending acceptance. Open unresolved dependencies are reflected by daily reconciliation. Remove the manual `blocked` label when the blocker is resolved.

The sprint review reports accepted points and unfinished scope. Weekly summaries include up to three preceding sprints of velocity. The daily digest includes overdue and blocked work. An overdue assignee receives at most one issue comment per week under the standard workflow identity.
