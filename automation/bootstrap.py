import json
import re

from .config import ROOT, STATUS_LABELS
from .issues import classify, labels
from .sprints import story_body


class Backlog:
    def __init__(self, store, project, lifecycle):
        self.store, self.project, self.lifecycle = store, project, lifecycle
        self.data = json.loads((ROOT / ".github/automation/backlog.json").read_text(encoding="utf-8"))
        epic_ids = {item["id"] for item in self.data["epics"]}
        ids = [item["id"] for group in self.data.values() for item in group]
        if len(ids) > 26 or len(set(ids)) != len(ids) or any(
                not re.fullmatch(r"[A-Z][A-Z0-9-]{1,29}", item) for item in ids):
            raise ValueError("Backlog IDs must be distinct, stable and limited to 26")
        for spec in self.data["sprint_zero"] + self.data["approved_proposals"]:
            if spec["epic"] not in epic_ids:
                raise ValueError(f"Unknown epic for {spec['id']}")
            story_body(spec)

    def _attach(self, issue, parent):
        path = f"/issues/{parent['number']}/sub_issues"
        existing = {item["id"] for item in self.store.github.pages(path)}
        if issue["id"] not in existing and self.store.writes:
            self.store.github.rest(path, method="POST", payload={"sub_issue_id": issue["id"]})

    def seed(self):
        self.store.ensure_labels()
        epics = {}
        for spec in self.data["epics"]:
            issue = self.store.upsert(f"epic:{spec['id']}", f"{spec['id']} · {spec['title']}",
                                      f"### Objective\n\n{spec['title']}\n\n"
                                      f"### Description\n\n{spec['description']}\n\n"
                                      "### Acceptance criteria\n\nLink approved deliverables and validation evidence.\n\n"
                                      f"### Component\n\n{spec['track']}\n\n### Epic\n\n{spec['id']}",
                                      ("epic", STATUS_LABELS["Backlog"]))
            if issue:
                epics[spec["id"]] = issue
                self.lifecycle.run(issue, "intake")
        self._stories(self.data["sprint_zero"], epics)

    def propose(self):
        epics = {}
        for spec in self.data["epics"]:
            issue = self.store.find(f"epic:{spec['id']}")
            if issue:
                epics[spec["id"]] = issue
        self._stories(self.data["approved_proposals"], epics)

    def _stories(self, specs, epics):
        created = {}
        for spec in specs:
            parent = epics.get(spec["epic"])
            if not parent and self.store.writes:
                raise ValueError(f"Seed the approved epic {spec['epic']} first")
            depends_on = spec.get("depends_on", [])
            if set(depends_on) - created.keys() and self.store.writes:
                raise ValueError(f"Seed dependencies before {spec['id']}")
            if depends_on and self.store.writes:
                spec = {**spec, "dependencies": ", ".join(f"#{created[key]['number']}" for key in depends_on)}
            tags = [spec.get("kind", "story"), "proposal", STATUS_LABELS["Backlog"]]
            if spec.get("qa"):
                tags.append("qa-required")
            issue = self.store.upsert(f"story:{spec['id']}", spec["title"], story_body(spec), tags)
            if not issue:
                continue
            created[spec["id"]] = issue
            if parent:
                self._attach(issue, parent)
            blockers = {item["id"] for item in self.store.github.pages(
                f"/issues/{issue['number']}/dependencies/blocked_by")}
            for key in depends_on:
                if key not in created:
                    continue
                parent_issue = created[key]
                if parent_issue["id"] not in blockers and self.store.writes:
                    self.store.github.rest(f"/issues/{issue['number']}/dependencies/blocked_by",
                                           method="POST", payload={"issue_id": parent_issue["id"]})
            self.lifecycle.run(issue, "intake")
