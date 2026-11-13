import re

from .config import STATUS_LABELS
from .github import ApiError, log
from .issues import classify, issue_status, labels


def transition(issue, current, event, *, blocked=False, trusted=False):
    tags = labels(issue)
    if event == "reopened":
        return ("Blocked" if blocked else "Backlog") if issue["state"] == "open" else None
    if current == "Cancelled":
        return None
    if issue["state"] == "closed" and issue.get("state_reason") in {"not_planned", "duplicate"}:
        return "Cancelled"
    if "qa-approved" in tags and trusted and event == "qa":
        return "Blocked" if blocked else "Done"
    if blocked and current != "Done":
        return "Blocked"
    if event == "unblocked" and current == "Blocked":
        if issue["state"] == "closed":
            return "QA/UAT" if "qa-required" in tags else "Done"
        return "Backlog"
    if event in {"merged", "closed"}:
        if current == "Done":
            return None
        if "qa-required" in tags or issue["state"] == "open":
            return "QA/UAT"
        return "Done"
    if event == "review" and issue["state"] == "open" and current in {"Backlog", "Ready", "In Progress"}:
        return "In Review"
    if event == "intake":
        if issue["state"] == "closed" and current == "Backlog":
            return "QA/UAT" if "qa-required" in tags else "Done"
        return current
    return None


class IssueTriageService:
    def __init__(self, store, project):
        self.store, self.project = store, project

    def run(self, issue):
        metadata = self.store.triage(issue)
        if self.project:
            self.project.ensure_item(issue)
            for name, value in metadata.fields.items():
                self.project.set(issue, name, value, initial=True)
            severity = next((tag.split(":", 1)[1].title() for tag in sorted(labels(issue))
                             if tag.startswith("severity:")), None)
            if severity:
                self.project.set(issue, "Severity", severity)
        return metadata


class TicketLifecycleService:
    def __init__(self, store, project, notifier):
        self.store, self.project, self.notifier = store, project, notifier
        self.triage = IssueTriageService(store, project)

    def run(self, issue, event, *, actor=None, source=None):
        number = issue["number"]
        tags = labels(issue)
        metadata = self.triage.run(issue) if event == "intake" else classify(issue)
        current = (self.project.get(number, "Status", "name") if self.project else None) or issue_status(issue)
        approved = event == "qa" and bool(actor) and self.store.github.can_write_issues(actor)
        if event == "qa" and not approved:
            return
        dependencies = self.store.github.blocked_by(number) if current not in {"Done", "Cancelled"} or event == "reopened" else []
        blockers = [self.store.github.safe_link(item["html_url"]) for item in dependencies]
        for dependency in metadata.dependencies:
            if dependency == number:
                blockers.append(f"#{dependency}")
                continue
            try:
                related = self.store.github.issue(dependency)
            except ApiError as error:
                if error.status != 404:
                    raise
                blockers.append(f"#{dependency} unavailable")
                continue
            if ("pull_request" in related or related["state"] == "open"
                    or related.get("state_reason") != "completed"
                    or issue_status(related) == "QA/UAT"):
                blockers.append(self.store.github.safe_link(related["html_url"]))
        manual_block = "blocked" in tags
        if event == "dependencies" and current == "Blocked" and not blockers and not manual_block:
            event = "unblocked"
        target = transition(issue, current, event, blocked=bool(blockers) or manual_block, trusted=approved)
        if target:
            if self.project:
                self.project.set(issue, "Status", target)
            remove = set(STATUS_LABELS.values()) - {STATUS_LABELS[target]}
            if event == "reopened":
                remove.add("qa-approved")
            self.store.sync_labels(issue, add=(STATUS_LABELS[target],),
                                   remove=remove)
            if approved and target == "Done" and issue["state"] == "open" and self.store.writes:
                self.store.github.rest(f"/issues/{number}", method="PATCH",
                                       payload={"state": "closed", "state_reason": "completed"})
            if target != current:
                log("issue_transition", issue=number, source=source or event, previous=current, status=target,
                    dry_run=not self.store.writes)
        if target == "Blocked" and (current != "Blocked" or event == "blocked"):
            detail = "\n".join(blockers[:3]) or "A maintainer marked this issue blocked."
            self.notifier.send("blocked", f"issue:{number}",
                               f"Blocked work\nIssue #{number}\n{detail}\n{issue['html_url']}",
                               group=f"issue:{number}")

    def issue_event(self, event):
        raw = event["issue"]
        issue = self.store.github.issue(raw["number"])
        action = event["action"]
        label = event.get("label", {}).get("name", "")
        if action in {"opened", "edited"}:
            self.run(issue, "intake")
        elif action in {"reopened", "closed"} and issue["state"] == raw["state"]:
            self.run(issue, action)
        elif action == "labeled" and label == "qa-approved" and label in labels(issue):
            self.run(issue, "qa", actor=event["sender"]["login"])
        elif action in {"labeled", "unlabeled"} and label == "blocked":
            if (label in labels(issue)) == (action == "labeled"):
                self.run(issue, "blocked" if action == "labeled" else "unblocked")


class PullRequestMonitor:
    def __init__(self, github, lifecycle):
        self.github, self.lifecycle = github, lifecycle

    def linked(self, number):
        owner, name = self.github.repository.split("/")
        document = """
        query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              closingIssuesReferences(first: 50, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes { number repository { nameWithOwner } }
              }
            }
          }
        }
        """
        for item in self.github.connection(document, {"owner": owner, "name": name, "number": number},
                lambda data: data["repository"]["pullRequest"]["closingIssuesReferences"], operation="PR links"):
            if item["repository"]["nameWithOwner"].lower() == self.github.repository.lower():
                yield item["number"]

    def event(self, event):
        number = event["number"]
        pr = self.github.rest(f"/pulls/{number}")
        if event["action"] == "closed" and pr["merged"]:
            state = "merged"
        elif pr["state"] == "open":
            state = "linked" if pr["draft"] else "review"
        else:
            return
        for issue in self.linked(number):
            self.lifecycle.run(self.github.issue(issue), state, source=f"PR #{number}")
            self.lifecycle.store.comment(issue, f"pr:{number}",
                                         f"Linked pull request #{number}\n{self.github.safe_link(pr['html_url'])}")


class QualityMonitor:
    FAILURE = {"failure", "timed_out", "action_required", "startup_failure", "stale"}

    def __init__(self, github, store, pull_requests, notifier):
        self.github, self.store, self.pull_requests, self.notifier = github, store, pull_requests, notifier

    def event(self, run, default_branch):
        if run["conclusion"] not in self.FAILURE:
            return
        run_id = int(run["id"])
        attempt = int(run.get("run_attempt", 1))
        key = f"run:{run_id}:{attempt}"
        url = self.github.safe_link(run["html_url"])
        if run["name"] != "Engineering CI":
            self.notifier.send("health", key, f"Automation failed\n{run['name']} · {run['conclusion']}\n{url}",
                               group=f"workflow:{run['workflow_id']}")
            return
        jobs = self.github.pages(f"/actions/runs/{run_id}/attempts/{attempt}/jobs", key="jobs")
        failed = [job["name"] for job in jobs if job["conclusion"] in self.FAILURE]
        diagnostic = ", ".join(re.sub(r"[^A-Za-z0-9 .()+/_-]", "", name)[:80] for name in failed[:5])
        diagnostic = diagnostic or run["conclusion"]
        message = f"Engineering CI failed\n{diagnostic}\nOpen the failed job and inspect its first failing step.\n{url}"
        main_push = run["event"] == "push" and run["head_branch"] == default_branch
        if main_push:
            self.notifier.send("ci", key, message, group="default-branch")
        prs = {item["number"] for item in run.get("pull_requests", [])
               if item.get("base", {}).get("repo", {}).get("id") == run["repository"]["id"]}
        sha = run.get("head_sha", "")
        if not prs and len(sha) == 40 and sha.isalnum():
            prs = {item["number"] for item in self.github.pages(f"/commits/{sha}/pulls")
                   if item["base"]["repo"]["full_name"].lower() == self.github.repository.lower()
                   and (item["head"]["sha"] == sha or item.get("merge_commit_sha") == sha)}
        issues = {number for pr in sorted(prs) for number in self.pull_requests.linked(pr)}
        for number in sorted(issues)[:20]:
            self.store.comment(number, key, message)
