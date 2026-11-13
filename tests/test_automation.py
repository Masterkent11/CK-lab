import copy
import io
import json
import logging
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

from automation.__main__ import main
from automation.bootstrap import Backlog
from automation.config import ROOT, STATUS_LABELS, Settings
from automation.github import ApiError, Http
from automation.issues import IssueStore, classify, marker
from automation.lifecycle import PullRequestMonitor, QualityMonitor, TicketLifecycleService, transition
from automation.notifications import NotificationRouter, TelegramNotificationAdapter
from automation.projects import GitHubProjectAdapter, SCHEMA
from automation.security import Finding, SecurityFindingService, dependabot_finding, secret_findings, severity
from automation.sprints import ReportGenerator, SprintPlanningService, Work, story_body


SETTINGS = Settings.load(ROOT / ".github/automation/settings.json")
NOW = datetime(2026, 11, 16, 1, 7, tzinfo=timezone.utc)
SPECS = json.loads((ROOT / ".github/automation/backlog.json").read_text(encoding="utf-8"))["sprint_zero"]


def issue(number=1, tags=(), state="open", **extra):
    return {"number": number, "id": number + 100, "node_id": f"issue{number}",
            "state": state, "title": "[story] Prototype", "body": story_body(SPECS[0]),
            "labels": [{"name": tag} for tag in tags], "html_url": f"https://github.com/test/repo/issues/{number}",
            **extra}


class Repository:
    repository = "test/repo"

    def __init__(self):
        self.data, self.comments, self.dependencies, self.children = {}, {}, {}, {}
        self.label_names, self.calls = set(), []

    def repository_url(self):
        return "https://github.com/test/repo"

    def safe_link(self, url):
        return url

    def issue(self, number):
        if number not in self.data:
            raise ApiError("mock issue", 404)
        return copy.deepcopy(self.data[number])

    def issues(self):
        return iter(copy.deepcopy(list(self.data.values())))

    def blocked_by(self, number):
        return [item for item in self.pages(f"/issues/{number}/dependencies/blocked_by") if item["state"] == "open"]

    def can_write_issues(self, login):
        return login == "maintainer"

    def pages(self, path, key=None):
        if path == "/labels":
            return iter({"name": name} for name in self.label_names)
        number = int(path.split("/")[2])
        if path.endswith("/comments"):
            return iter(self.comments.get(number, []))
        if path.endswith("/sub_issues"):
            return iter(self.issue(value) for value in self.children.get(number, []))
        if path.endswith("/dependencies/blocked_by"):
            return iter(self.issue(value) for value in self.dependencies.get(number, []))
        if path.endswith("/events"):
            return iter([])
        raise AssertionError(path)

    def rest(self, path="", *, method="GET", payload=None):
        self.calls.append((path, method, payload))
        if path == "/labels" and method == "POST":
            self.label_names.add(payload["name"])
            return payload
        if path == "/issues":
            number = len(self.data) + 1
            item = issue(number, tags=payload["labels"])
            item.update(title=payload["title"], body=payload["body"])
            self.data[number] = item
            return self.issue(number)
        number = int(path.split("/")[2])
        if path.endswith("/comments"):
            self.comments.setdefault(number, []).append({"body": payload["body"], "user": {"login": "github-actions[bot]"}})
        elif path.endswith("/sub_issues"):
            self.children.setdefault(number, []).append(payload["sub_issue_id"] - 100)
        elif path.endswith("/dependencies/blocked_by"):
            self.dependencies.setdefault(number, []).append(payload["issue_id"] - 100)
        elif path.endswith("/labels"):
            self.data[number]["labels"].extend({"name": name} for name in payload["labels"])
        elif "/labels/" in path and method == "DELETE":
            name = unquote(path.split("/")[-1])
            self.data[number]["labels"] = [tag for tag in self.data[number]["labels"] if tag["name"] != name]
        else:
            self.data[number].update(payload)
        return self.issue(number)


class Projects(GitHubProjectAdapter):
    def __init__(self, github):
        super().__init__(github, SETTINGS, "fake", True)
        self.project = {"id": "project", "url": "https://github.com/users/test/projects/1"}
        self._items = {}
        self._fields = {name: {"id": name, "dataType": kind,
                              "options": [{"name": option, "id": option} for option in options]}
                        for name, (kind, options) in SCHEMA.items()}
        self._fields["Sprint"]["configuration"] = {"completedIterations": [], "iterations": [
            {"id": "sprint0", "startDate": "2026-11-16"}]}

    def _mutate(self, name, values, result):
        if name == "addProjectV2ItemById":
            return {"item": {"id": values["contentId"], "fieldValues": {"nodes": []}}}
        if name == "updateProjectV2ItemFieldValue":
            item = next(item for item in self.items.values() if item["id"] == values["itemId"])
            name = values["fieldId"]
            attribute, value = next(iter(values["value"].items()))
            node = {"field": {"name": name}, attribute: value}
            if attribute == "singleSelectOptionId":
                node.update(name=value, optionId=value)
            if attribute == "iterationId":
                node["startDate"] = "2026-11-16"
            item["fieldValues"]["nodes"] = [
                field for field in item["fieldValues"]["nodes"] if field["field"]["name"] != name] + [node]
            return {"projectV2Item": item}
        raise AssertionError(name)


class AutomationTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.repo = Repository()
        self.store = IssueStore(self.repo, writes=True)
        self.telegram = Mock(configured=True, chat="test")
        self.router = NotificationRouter(SETTINGS, self.store, self.telegram, now=lambda: NOW)

    def test_sprint_dates(self):
        calendar = SprintPlanningService(SETTINGS, self.repo).calendar
        self.assertIsNone(calendar.current(date(2026, 11, 15)))
        self.assertEqual(calendar.at(0).end, date(2026, 11, 29))
        self.assertEqual(calendar.current(date(2026, 11, 30)).number, 1)
        self.assertEqual(calendar.at(1).end, date(2026, 12, 13))
        self.assertEqual(calendar.next(date(2026, 12, 13)).start, date(2026, 12, 14))

    def test_template_and_classification(self):
        self.assertEqual(classify(issue()).fields["Story Points"], 3)
        self.assertFalse(classify(issue()).missing)
        self.assertIn("Fibonacci estimate", classify({**issue(), "body": story_body(SPECS[0]).replace("\n\n3", "\n\n4")}).missing)
        self.assertIn("Acceptance criteria", classify({**issue(), "body": "free text"}).missing)
        with self.assertRaises(ValueError):
            story_body({**SPECS[0], "points": 13})

    def test_duplicate_closed_issue_is_reused(self):
        first = self.store.upsert("story:S0-UX", "One", "Content", ("story",))
        self.repo.data[first["number"]]["state"] = "closed"
        second = IssueStore(self.repo, writes=True).upsert("story:S0-UX", "Two", "Other", ("story",))
        self.assertEqual(first["number"], second["number"])
        self.assertEqual(second["title"], "One")
        self.assertEqual(len(self.repo.data), 1)

    def test_dry_run_cannot_write_or_send(self):
        store = IssueStore(self.repo, writes=False)
        store.ensure_labels()
        store.upsert("sample", "Draft", "Draft", ("story",))
        NotificationRouter(SETTINGS, store, self.telegram).send("test", "one", "hello")
        self.assertEqual(self.repo.calls, [])
        self.telegram.send.assert_not_called()

    def test_qa_gate_and_reopen(self):
        ticket = issue(tags=("qa-required",), state="closed")
        self.assertEqual(transition(ticket, "In Review", "merged"), "QA/UAT")
        self.assertEqual(transition(ticket, "In Review", "closed"), "QA/UAT")
        self.assertEqual(transition(ticket, "QA/UAT", "closed"), "QA/UAT")
        ticket["state"] = "open"
        self.assertEqual(transition(ticket, "QA/UAT", "reopened"), "Backlog")
        self.assertEqual(transition(ticket, "In Progress", "review", blocked=True), "Blocked")
        self.repo.data[1] = issue(tags=("qa-required", "qa-approved", STATUS_LABELS["QA/UAT"]))
        lifecycle = TicketLifecycleService(self.store, None, self.router)
        lifecycle.run(self.repo.issue(1), "qa", actor="visitor")
        self.assertEqual(self.repo.data[1]["state"], "open")
        lifecycle.run(self.repo.issue(1), "qa", actor="maintainer")
        self.assertEqual(self.repo.data[1]["state"], "closed")

    def test_sprint_smoke_with_board_and_rerun(self):
        board = Projects(self.repo)
        lifecycle = TicketLifecycleService(self.store, board, Mock())
        backlog = Backlog(self.store, board, lifecycle)
        backlog.seed()
        Backlog(IssueStore(self.repo, writes=True), board, lifecycle).seed()
        self.assertEqual(len(self.repo.data), 16)
        self.assertEqual(len(board.items), 16)
        sample = self.store.find("story:S0-UX")
        self.assertEqual(board.get(sample["number"], "Status", "name"), "Backlog")
        self.assertEqual(board.get(sample["number"], "Track", "name"), "UX/UI")
        planner = SprintPlanningService(SETTINGS, self.repo, board)
        sprint = planner.calendar.at(0)
        work = planner.collect()
        report = ReportGenerator(SETTINGS, self.repo, planner).render("sprint_start", sprint.start, work)
        self.assertIn("Proposed scope", report)
        self.assertEqual(board.get(sample["number"], "Sprint", "startDate"), None)
        planner.approve(sprint, work, (sample["number"],))
        self.assertEqual(board.get(sample["number"], "Sprint", "startDate"), "2026-11-16")
        self.assertTrue(self.router.send("sprint_start", "0", report))
        self.assertFalse(self.router.send("sprint_start", "0", report))
        self.telegram.send.assert_called_once_with(report)

    def test_blocked_work_excluded_from_draft(self):
        planner = SprintPlanningService(SETTINGS, self.repo)
        fields = classify(issue()).fields
        blocked = Work(issue(), fields, "Ready", ("#5",))
        draft = Work(issue(2), fields, "Ready", ())
        self.assertEqual(planner.plan(planner.calendar.at(0), [blocked, draft]), ([], [draft]))

    def test_security_fixture_and_deduplication(self):
        service = SecurityFindingService(self.repo, self.store, None, self.router)
        fake = Finding("test", "Synthetic", "a", "b", "c", "High", "d", "e", synthetic=True)
        service.process(fake, public=True)
        self.assertFalse(self.repo.data)
        real = next(secret_findings([{"File": "file", "RuleID": "example", "StartLine": 1,
                                     "Commit": "abcdef123456789", "Secret": "private-evidence"}]))
        service.process(real, public=True)
        service.process(real, public=True)
        self.assertEqual(len(self.repo.data), 1)
        self.assertNotIn("private-evidence", self.repo.data[1]["body"])
        self.assertNotIn("abcdef", self.repo.data[1]["body"])
        self.telegram.send.assert_not_called()

    def test_severity_mapping(self):
        self.assertEqual([severity(score) for score in (10, 9, 7, 4, 1, 0)],
                         ["Critical", "Critical", "High", "Medium", "Low", "Informational"])
        self.assertEqual(severity(None, "moderate"), "Medium")
        alert = {"number": 1, "state": "open", "security_advisory": {"ghsa_id": "GHSA-abcd-efgh-ijkl",
                 "cve_id": "bogus", "cvss": {"score": 9.1, "vector_string": "CVSS:3.1/sample"}},
                 "dependency": {"package": {"name": "sample"}, "manifest_path": "manifest"},
                 "security_vulnerability": {"vulnerable_version_range": "< 1.0"}}
        result = dependabot_finding(alert, self.repo.repository_url())
        self.assertTrue(result.validated)
        self.assertEqual(result.severity, "Critical")
        self.assertNotIn("bogus", result.advisory)

    def test_quality_annotations_and_alert_routes(self):
        self.repo.data[1] = issue()
        github = Mock(repository="test/repo")
        github.safe_link.side_effect = lambda url: url
        github.pages.return_value = [{"name": "C++", "conclusion": "failure"}]
        prs = Mock()
        prs.linked.return_value = [1]
        run = {"id": 5, "name": "Engineering CI", "conclusion": "failure", "event": "pull_request",
               "head_branch": "feature", "html_url": "https://github.com/test/repo/actions/runs/5",
               "pull_requests": [{"number": 2, "base": {"repo": {"id": 1}}}], "repository": {"id": 1}}
        monitor = QualityMonitor(github, self.store, prs, self.router)
        monitor.event(run, "master")
        monitor.event(run, "master")
        self.assertEqual(len(self.repo.comments[1]), 1)
        self.telegram.send.assert_not_called()
        monitor.event({**run, "id": 6, "event": "push", "head_branch": "master"}, "master")
        self.telegram.send.assert_called_once()

    def test_pr_link_does_not_guess_from_title(self):
        github = Mock(repository="test/repo")
        github.connection.return_value = [
            {"number": 5, "repository": {"nameWithOwner": "elsewhere/repo"}},
            {"number": 2, "repository": {"nameWithOwner": "Test/Repo"}},
        ]
        self.assertEqual(list(PullRequestMonitor(github, Mock()).linked(7)), [2])

    def test_telegram_plain_text_and_cooldown(self):
        http = Mock()
        http.request.return_value = ({"ok": True, "result": {"message_id": 123}}, {})
        adapter = TelegramNotificationAdapter("123:sample", "-1001", http=http)
        self.assertEqual(adapter.send("Review #2\nhttps://github.com/test/repo/issues/2"), 123)
        payload = http.request.call_args.kwargs["payload"]
        self.assertNotIn("parse_mode", payload)
        self.assertIn("https://github.com", payload["text"])
        self.assertTrue(self.router.send("ci", "a", "alert", group="main"))
        self.assertFalse(self.router.send("ci", "b", "alert", group="main"))
        self.assertFalse(self.router.send("unknown", "x", "alert"))

    def test_uncertain_delivery_reserves_receipt(self):
        self.telegram.send.side_effect = ApiError("send", "transport", uncertain=True)
        with self.assertRaises(ApiError):
            self.router.send("test", "a", "hello")
        self.assertFalse(self.router.send("test", "a", "hello"))
        self.telegram.send.assert_called_once()
        self.assertIn("uncertain", self.repo.data[1]["body"])

    def test_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(["daily"]), 2)
            self.assertFalse(TelegramNotificationAdapter().configured)

    def test_rate_limit_and_uncertain_writes(self):
        opener, sleep = Mock(), Mock()
        error = HTTPError("https://api.example", 429, "limited", {"Retry-After": "3"}, io.BytesIO())
        response = Mock()
        response.__enter__ = Mock(return_value=SimpleNamespace(read=lambda: b"{}", headers={}))
        response.__exit__ = Mock()
        opener.open.side_effect = [error, response]
        http = Http(opener, sleep=sleep)
        self.assertEqual(http.request("https://api.example", operation="list"), ({}, {}))
        sleep.assert_called_once_with(3)
        opener.reset_mock()
        opener.open.side_effect = URLError("offline")
        with self.assertRaises(ApiError) as failure:
            http.request("https://api.example", operation="create", method="POST")
        self.assertTrue(failure.exception.uncertain)
        opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
