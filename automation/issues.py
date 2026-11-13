import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .config import LABELS, POINTS, PRIORITIES, STATUS_LABELS, TRACKS, TYPE_LABELS
from .github import ApiError, log


HEADINGS = (
    "Objective", "Description", "Acceptance criteria", "Story points", "Priority",
    "Component", "Dependencies", "Testing", "Definition of done",
)
MARKER = re.compile(r"^<!-- embroidery:([a-zA-Z0-9:/._-]+) -->$", re.MULTILINE)
EPIC = re.compile(r"EPIC-[0-9]{3}")
REFERENCE = re.compile(r"#([1-9][0-9]*)")
MAX_DEPENDENCIES = 10


def labels(issue):
    return {item["name"] for item in issue.get("labels", [])}


def marker(key):
    if not re.fullmatch(r"[a-zA-Z0-9:/._-]+", key):
        raise ValueError("Invalid automation identifier")
    return f"<!-- embroidery:{key} -->"


def sections(body):
    parts = re.split(r"^#{2,3} ([^\n]+)\s*$", MARKER.sub("", body or ""), flags=re.MULTILINE)
    return {name.strip().lower(): value.strip() for name, value in zip(parts[1::2], parts[2::2])
            if value.strip() and value.strip() != "_No response_"}


def issue_status(issue):
    return next((name for name, label in STATUS_LABELS.items() if label in labels(issue)), "Backlog")


@dataclass
class Metadata:
    kind: str
    fields: dict
    missing: tuple
    dependencies: tuple


def classify(issue):
    content = sections(issue.get("body"))
    kinds = [label for label in TYPE_LABELS if label in labels(issue)]
    title = (issue.get("title") or "").lower()
    inferred = next((kind for kind in TYPE_LABELS if title.startswith(f"[{kind}]")), "task")
    kind = kinds[0] if len(kinds) == 1 else inferred
    managed = "automation" in labels(issue) or "security" in labels(issue)
    required = HEADINGS if kind != "epic" else HEADINGS[:3]
    missing = [name for name in required if name.lower() not in content]
    points = content.get("story points")
    priority = content.get("priority")
    track = content.get("component", "").split(" / ", 1)[0]
    epic = content.get("epic", "")
    due = content.get("due date", "")
    dependency_text = content.get("dependencies", "")
    dependencies = tuple(dict.fromkeys(int(number) for number in REFERENCE.findall(dependency_text)))
    fields = {}
    if len(kinds) > 1:
        missing.append("One issue type")
    if points in {str(n) for n in POINTS}:
        fields["Story Points"] = int(points)
    elif points:
        missing.append("Fibonacci estimate")
    if priority in PRIORITIES:
        fields["Priority"] = priority
    elif priority:
        missing.append("Valid priority")
    if track in TRACKS:
        fields["Track"] = track
    elif track:
        missing.append("Valid component")
    if EPIC.fullmatch(epic):
        fields["Epic"] = epic
    elif epic and epic.lower() != "none":
        missing.append("Valid epic")
    if due and due.lower() != "none":
        try:
            fields["Due Date"] = date.fromisoformat(due).isoformat()
        except ValueError:
            missing.append("ISO due date")
    if dependency_text and not dependencies and dependency_text.lower() != "none":
        missing.append("Dependencies as issue numbers or None")
    if len(dependencies) > MAX_DEPENDENCIES:
        missing.append("At most ten issue references")
    if not managed:
        missing = tuple(dict.fromkeys(missing))
    else:
        missing = ()
    return Metadata(kind, fields, missing, dependencies[:MAX_DEPENDENCIES])


class IssueStore:
    def __init__(self, github, *, writes):
        self.github = github
        self.writes = writes
        self._index = None

    def ensure_labels(self):
        existing = {item["name"] for item in self.github.pages("/labels")}
        for name, color in LABELS.items():
            if name in existing:
                continue
            log("label_create", name=name, dry_run=not self.writes)
            if self.writes:
                try:
                    self.github.rest("/labels", method="POST", payload={"name": name, "color": color})
                except ApiError as error:
                    if error.status != 422 or name not in {item["name"] for item in self.github.pages("/labels")}:
                        raise

    def find(self, key, refresh=False):
        if self._index is None or refresh:
            self._index = {}
            for issue in self.github.issues():
                if "automation" not in labels(issue):
                    continue
                for value in MARKER.findall(issue.get("body") or ""):
                    if value in self._index:
                        raise ValueError(f"Duplicate managed identifier {value}")
                    self._index[value] = issue
        return self._index.get(key)

    def upsert(self, key, title, body, issue_labels=(), *, update=False):
        desired = body.rstrip() + "\n\n" + marker(key)
        issue = self.find(key)
        if issue:
            issue = self.github.issue(issue["number"])
            if update and (issue["title"] != title or (issue.get("body") or "").rstrip() != desired):
                log("issue_update", issue=issue["number"], key=key, dry_run=not self.writes)
                if self.writes:
                    issue = self.github.rest(f"/issues/{issue['number']}", method="PATCH",
                                             payload={"title": title, "body": desired})
            return issue
        log("issue_create", key=key, dry_run=not self.writes)
        if not self.writes:
            return None
        try:
            issue = self.github.rest("/issues", method="POST",
                                     payload={"title": title, "body": desired,
                                              "labels": sorted({"automation", *issue_labels})})
        except ApiError as error:
            if not error.uncertain or not (issue := self.find(key, refresh=True)):
                raise
        self._index[key] = issue
        return issue

    def sync_labels(self, issue, *, add=(), remove=()):
        current = labels(issue)
        additions, removals = set(add) - current, set(remove) & current
        if not additions and not removals:
            return
        number = issue["number"]
        log("issue_labels", issue=number, add=sorted(additions), remove=sorted(removals),
            dry_run=not self.writes)
        if not self.writes:
            return
        if additions:
            self.github.rest(f"/issues/{number}/labels", method="POST", payload={"labels": sorted(additions)})
        for name in sorted(removals):
            try:
                self.github.rest(f"/issues/{number}/labels/{quote(name, safe='')}", method="DELETE")
            except ApiError as error:
                if error.status != 404:
                    raise

    def comment(self, number, key, body):
        signature = marker(key)
        path = f"/issues/{int(number)}/comments"
        if any(signature in (item.get("body") or "") and item["user"]["login"] == "github-actions[bot]"
               for item in self.github.pages(path)):
            return
        log("issue_comment", issue=number, key=key, dry_run=not self.writes)
        if self.writes:
            self.github.rest(path, method="POST", payload={"body": body.rstrip() + "\n\n" + signature})

    def triage(self, issue):
        metadata = classify(issue)
        log("issue_classified", issue=issue["number"], kind=metadata.kind, missing=metadata.missing)
        additions = {metadata.kind} if not (set(TYPE_LABELS) & labels(issue)) else set()
        if metadata.missing:
            additions.add("needs-triage")
        self.sync_labels(issue, add=additions, remove=() if metadata.missing else ("needs-triage",))
        return metadata
