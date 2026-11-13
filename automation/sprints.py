from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import POINTS, PRIORITIES, STATUSES, TRACKS
from .issues import EPIC, HEADINGS, classify, issue_status, labels


@dataclass(frozen=True)
class Sprint:
    number: int
    start: date
    end: date

    @property
    def title(self):
        return f"Sprint {self.number}"

    def project_value(self):
        return {"title": self.title, "startDate": self.start.isoformat(),
                "duration": (self.end - self.start).days + 1}


class SprintCalendar:
    def __init__(self, settings):
        self.start = date.fromisoformat(settings.sprint["start"])
        self.days = settings.sprint["days"]
        self.horizon = settings.sprint["iteration_horizon"]

    def at(self, index):
        start = self.start + timedelta(days=index * self.days)
        return Sprint(index, start, start + timedelta(days=self.days - 1))

    def current(self, today):
        index = (today - self.start).days // self.days
        return self.at(index) if index >= 0 else None

    def next(self, today):
        current = self.current(today)
        return self.at(current.number + 1 if current else 0)


def story_body(spec):
    if spec["points"] not in POINTS or spec["priority"] not in PRIORITIES or spec["track"] not in TRACKS:
        raise ValueError(f"Invalid estimate, priority or track for {spec['id']}")
    if not EPIC.fullmatch(spec["epic"]):
        raise ValueError(f"Invalid epic for {spec['id']}")
    criteria = "\n".join(f"* [ ] {item}" for item in spec["criteria"])
    values = (
        spec["objective"], spec["description"], criteria, spec["points"], spec["priority"],
        spec["track"], spec.get("dependencies", "None"), spec["testing"], spec["done"],
    )
    if any(not str(value).strip() for value in values):
        raise ValueError(f"Incomplete story template {spec['id']}")
    return "\n\n".join(f"### {heading}\n\n{value}" for heading, value in
                       zip((*HEADINGS, "Epic"), (*values, spec["epic"])))


@dataclass
class Work:
    issue: dict
    fields: dict
    status: str
    blockers: tuple

    @property
    def number(self):
        return self.issue["number"]

    @property
    def points(self):
        return self.fields.get("Story Points", 0)

    @property
    def iteration(self):
        return self.fields.get("Sprint")

    @property
    def done(self):
        return self.status in {"Done", "Cancelled"}

    @property
    def blocked(self):
        return self.status == "Blocked" or bool(self.blockers)

    @property
    def ready(self):
        return (self.issue["state"] == "open" and self.status in {"Backlog", "Ready"}
                and not self.blocked and not classify(self.issue).missing and self.points > 0
                and self.fields.get("Priority") in PRIORITIES and self.fields.get("Track") in TRACKS
                and not (labels(self.issue) & {"epic", "security"}))


class SprintPlanningService:
    def __init__(self, settings, github, project=None):
        self.settings, self.github, self.project = settings, github, project
        self.calendar = SprintCalendar(settings)

    def collect(self):
        all_issues = {item["number"]: item for item in self.github.issues()}
        work = []
        for issue in all_issues.values():
            number = issue["number"]
            metadata = classify(issue)
            fields = metadata.fields.copy()
            status = issue_status(issue)
            if not labels(issue) & {"status:done", "status:qa-uat", "status:cancelled", "status:blocked"}:
                if issue["state"] == "closed":
                    if issue.get("state_reason") == "not_planned":
                        status = "Cancelled"
                    else:
                        status = "QA/UAT" if "qa-required" in labels(issue) else "Done"
            if self.project:
                for name, attribute in {"Story Points": "number", "Priority": "name", "Track": "name",
                                        "Due Date": "date", "Sprint": "startDate"}.items():
                    value = self.project.get(number, name, attribute)
                    if value is not None:
                        fields[name] = value
                board_status = self.project.get(number, "Status", "name")
                if board_status in STATUSES:
                    status = board_status
            if status == "Done" and "qa-required" in labels(issue):
                approvals = [event["created_at"] for event in self.github.pages(f"/issues/{number}/events")
                             if event["event"] == "labeled" and event.get("label", {}).get("name") == "qa-approved"]
                if approvals:
                    issue = {**issue, "accepted_at": max(approvals)}
            blockers = []
            if status not in {"Done", "Cancelled"} and not labels(issue) & {"epic", "security"}:
                blockers.extend(item["html_url"] for item in self.github.blocked_by(number))
            blockers.extend(f"#{ref}" for ref in metadata.dependencies
                            if ref not in all_issues or all_issues[ref]["state"] != "closed"
                            or all_issues[ref].get("state_reason") == "not_planned"
                            or "status:qa-uat" in labels(all_issues[ref]))
            work.append(Work(issue, fields, status, tuple(dict.fromkeys(blockers))))
        return work

    def plan(self, sprint, work):
        committed = [item for item in work if item.iteration == sprint.start.isoformat()
                     and item.status != "Cancelled"]
        capacity = self.settings.sprint["capacity"]
        available = capacity - sum(item.points for item in committed)
        candidates = sorted((item for item in work if not item.iteration and item.ready),
                            key=lambda item: (item.fields["Priority"], item.number))
        proposed = []
        for item in candidates:
            if len(proposed) == self.settings.sprint["max_proposals"]:
                break
            if item.points <= available:
                proposed.append(item)
                available -= item.points
        return committed, proposed

    def approve(self, sprint, work, numbers):
        if self.project is None:
            raise ValueError("Sprint approval requires a configured Project")
        if len(numbers) > 10:
            raise ValueError("Approve at most 10 issues at a time")
        committed, _ = self.plan(sprint, work)
        existing = {item.number for item in committed}
        by_number = {item.number: item for item in work}
        requested = []
        for number in numbers:
            if number in existing:
                continue
            item = by_number.get(number)
            if not item or not item.ready or item.iteration:
                raise ValueError(f"Issue #{number} is unavailable or needs triage")
            requested.append(item)
        if sum(item.points for item in committed + requested) > self.settings.sprint["capacity"]:
            raise ValueError("Approval would exceed configured sprint capacity")
        for item in requested:
            self.project.set(item.issue, "Sprint", sprint.start.isoformat())


def accepted_by(item, sprint, zone):
    if item.status != "Done":
        return False
    done_at = item.issue.get("accepted_at") or item.issue.get("closed_at")
    if not done_at:
        return False
    completed = datetime.fromisoformat(done_at.replace("Z", "+00:00")).astimezone(zone).date()
    return sprint.start <= completed <= sprint.end


class ReportGenerator:
    def __init__(self, settings, github, planner):
        self.settings, self.github, self.planner = settings, github, planner

    def issue_lines(self, items, limit=5, owners=False):
        lines = []
        for item in items[:limit]:
            points = f" · {item.points} pts" if item.points else ""
            assignees = ", ".join(person["login"] for person in item.issue.get("assignees", []))
            owner = f" · {assignees or 'Unassigned'}" if owners else ""
            lines.append(f"#{item.number}{points}{owner} {self.github.safe_link(item.issue['html_url'])}")
        if len(items) > limit:
            lines.append(f"{len(items) - limit} more in GitHub")
        return lines

    def render(self, event, today, work):
        calendar = self.planner.calendar
        active = calendar.current(today)
        if event == "sprint_end" and active and today != active.end and active.number > 0:
            active = calendar.at(active.number - 1)
        sprint = active or calendar.next(today)
        committed, proposed = self.planner.plan(sprint, work)
        scope = sum(item.points for item in committed)
        completed = sum(item.points for item in committed if accepted_by(item, sprint, self.settings.zone))
        capacity = self.settings.sprint["capacity"]
        title = {"daily": "Engineering digest", "weekly": "Weekly sprint summary",
                 "sprint_start": "Sprint planning", "sprint_end": "Sprint review"}[event]
        lines = [title, f"{sprint.title} · {sprint.start} to {sprint.end}"]
        if not self.planner.project:
            lines.append("Sprint scope and velocity unavailable until Projects is connected.")
        elif not active and event in {"daily", "weekly"}:
            lines.append("Sprint has yet to start.")
        else:
            lines.append(f"{scope}/{capacity} points committed · {completed} accepted in sprint")
            if scope > capacity:
                lines.append(f"Over capacity by {scope - capacity} points")
            blocked_scope = sum(item.blocked and not item.done for item in committed)
            if blocked_scope:
                lines.append(f"{blocked_scope} committed items blocked")
        if event == "sprint_start":
            lines += ["", "Proposed scope"] + (self.issue_lines(proposed, limit=10) or ["No ready candidates"])
            lines.append("Review dependencies and approve scope in GitHub.")
            if sprint.number and self.planner.project:
                previous = calendar.at(sprint.number - 1)
                carryover = [item for item in work if item.iteration == previous.start.isoformat()
                             and not item.done]
                if carryover:
                    lines += ["", "Review for carryover"] + self.issue_lines(carryover)
        if event == "sprint_end":
            carryover = [item for item in committed if not item.done]
            lines += ["", f"Carryover candidates · {len(carryover)}"] + self.issue_lines(carryover)
            lines.append("Review unfinished work before assigning another sprint.")
        if event in {"weekly", "sprint_end"} and self.planner.project and active:
            velocity = []
            first = max(0, sprint.number - 3)
            end = sprint.number + (1 if event == "sprint_end" else 0)
            for index in range(first, end):
                past = calendar.at(index)
                points = sum(item.points for item in work if item.iteration == past.start.isoformat()
                             and accepted_by(item, past, self.settings.zone))
                velocity.append(f"S{index} {points}")
            if velocity:
                lines += ["", "Velocity in points · " + " / ".join(velocity)]
        if event in {"daily", "weekly", "sprint_start"}:
            overdue = [item for item in work if not item.done and item.fields.get("Due Date", "9999") < today.isoformat()]
            blocked = [item for item in work if not item.done and item.blocked]
            needs_triage = [item for item in work if item.issue["state"] == "open" and classify(item.issue).missing]
            lines += ["", f"Overdue · {len(overdue)}"] + self.issue_lines(overdue, owners=True)
            lines += [f"Blocked · {len(blocked)}"] + self.issue_lines(blocked, owners=True)
            lines += [f"Needs triage · {len(needs_triage)}"] + self.issue_lines(needs_triage, limit=3)
        lines += ["", self.planner.project.project["url"] if self.planner.project else self.github.repository_url()]
        return "\n".join(lines)
