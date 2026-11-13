import argparse
import json
import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .bootstrap import Backlog
from .config import ROOT, Settings
from .github import ApiError, GitHub, log, utc_now
from .issues import IssueStore, labels
from .lifecycle import PullRequestMonitor, QualityMonitor, TicketLifecycleService
from .notifications import NotificationRouter, ReminderService, TelegramNotificationAdapter
from .projects import GitHubProjectAdapter
from .security import Finding, SecurityFindingService
from .sprints import ReportGenerator, SprintCalendar, SprintPlanningService


def output(report):
    print(report)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as summary:
            summary.write("\n" + report + "\n")


def due(config, now):
    at = time.fromisoformat(config["time"])
    target = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    return now.weekday() in config["days"] and target <= now < target + timedelta(hours=config["window_hours"])


class Application:
    def __init__(self, args, settings, github):
        self.args, self.settings, self.github = args, settings, github
        self.now = utc_now().astimezone(settings.zone)
        self.today = date.fromisoformat(args.date) if args.date else self.now.date()
        self.calendar = SprintCalendar(settings)
        self.writes = args.apply and settings.writes and not args.date
        self.store = IssueStore(github, writes=self.writes)
        project_token = os.getenv("PROJECT_TOKEN")
        self.adapter = GitHubProjectAdapter(github, settings, project_token, self.writes) if project_token else None
        self.project = None
        self.notifier = NotificationRouter(settings, self.store, TelegramNotificationAdapter())

    def connect_project(self):
        if self.adapter is None:
            log("project_unconfigured")
        elif self.adapter.locate():
            self.project = self.adapter
        else:
            log("project_not_found")

    def run(self):
        args = self.args
        command = args.command
        log("automation_start", command=command, dry_run=not self.writes)
        if command == "test-notification":
            message = f"Engineering automation test\n{self.github.repository_url()}"
            if not self.notifier.send("test", os.getenv("GITHUB_RUN_ID") or self.today.isoformat(), message):
                log("test_notification_not_delivered")
            return
        if command == "classify-fixture":
            finding = Finding("sample:offline", "Fixture", "Synthetic example", "No live scanner data",
                              "Synthetic", "High", "None", "None", synthetic=True)
            SecurityFindingService(self.github, self.store, None, self.notifier).process(finding, public=True)
            return
        if command == "setup":
            if self.adapter:
                self.adapter.configure(self.calendar, self.today)
                self.project = self.adapter if self.adapter.project else None
            else:
                log("project_unconfigured")
            self.store.ensure_labels()
            return
        if command != "quality":
            self.connect_project()
        lifecycle = TicketLifecycleService(self.store, self.project, self.notifier)
        pull_requests = PullRequestMonitor(self.github, lifecycle)
        planner = SprintPlanningService(self.settings, self.github, self.project)
        if command in {"seed", "propose"}:
            backlog = Backlog(self.store, self.project, lifecycle)
            (backlog.seed if command == "seed" else backlog.propose)()
        elif command == "reconcile":
            if len(args.issues) > 20:
                raise ValueError("Reconcile at most 20 explicitly selected issues")
            for number in args.issues:
                lifecycle.run(self.github.issue(number), "intake")
        elif command == "issue":
            event = self.event()
            issue = event["issue"]
            if "automation" not in labels(issue) or event["action"] not in {"opened", "edited"}:
                lifecycle.issue_event(event)
        elif command == "pr":
            pull_requests.event(self.event())
        elif command == "quality":
            event = self.event()
            QualityMonitor(self.github, self.store, pull_requests, self.notifier).event(
                event["workflow_run"], event["repository"]["default_branch"])
        elif command == "security":
            if args.scheduled and not due(self.settings.values["schedules"]["security"], self.now):
                log("schedule_outside_window", route="security")
                return
            scanner = self.github
            if token := os.getenv("DEPENDABOT_TOKEN"):
                scanner = GitHub(self.github.repository, token)
            SecurityFindingService(scanner, self.store, self.project, self.notifier).scan(args.report)
        elif command == "approve":
            if not self.project:
                raise ValueError("Set PROJECT_TOKEN and run setup")
            if args.sprint < 0:
                raise ValueError("Sprint number must be nonnegative")
            sprint = self.calendar.at(args.sprint)
            if self.today > sprint.end:
                raise ValueError("Cannot approve a completed sprint")
            planner.approve(sprint, planner.collect(), tuple(dict.fromkeys(args.issues)))
        elif command in {"daily", "weekly", "sprint_start", "sprint_end", "schedule"}:
            self.report(planner)

    def report(self, planner):
        route = self.args.command
        routes = [route]
        if route == "schedule":
            routes = [name for name in ("daily", "weekly") if due(self.settings.values["schedules"][name], self.now)]
            current = self.calendar.current(self.today)
            if current and due(self.settings.values["schedules"]["sprint"], self.now):
                if self.today == current.start:
                    routes.append("sprint_start")
                if self.today == current.end:
                    routes.append("sprint_end")
        if not routes:
            log("schedule_outside_window", route=route)
            return
        if self.project and "sprint_start" in routes:
            self.project.ensure_iterations(self.calendar, self.today)
            Backlog(self.store, self.project, TicketLifecycleService(
                self.store, self.project, self.notifier)).propose()
        work = planner.collect()
        for event in routes:
            report = ReportGenerator(self.settings, self.github, planner).render(event, self.today, work)
            output(report)
            self.notifier.send(event, self.today.isoformat(), report)
            if event == "daily":
                lifecycle = TicketLifecycleService(self.store, self.project, self.notifier)
                for item in [item for item in work if item.blocked and not item.done][:20]:
                    lifecycle.run(item.issue, "dependencies")
                ReminderService(self.store).overdue(self.today, work)

    def event(self):
        event = json.loads(Path(self.args.event).read_text(encoding="utf-8"))
        if event["repository"]["full_name"].lower() != self.github.repository.lower():
            raise ValueError("Event repository does not match GITHUB_REPOSITORY")
        return event


def parse(argv):
    parser = argparse.ArgumentParser(prog="python -m automation")
    parser.add_argument("--config", default=ROOT / ".github/automation/settings.json", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--date", help="Manila date for a read only replay")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "seed", "propose", "daily", "weekly", "sprint_start", "sprint_end",
                 "schedule", "classify-fixture", "test-notification"):
        commands.add_parser(name)
    for name in ("issue", "pr", "quality"):
        sub = commands.add_parser(name)
        sub.add_argument("--event", default=os.getenv("GITHUB_EVENT_PATH"), required=not os.getenv("GITHUB_EVENT_PATH"))
    security = commands.add_parser("security")
    security.add_argument("--report")
    security.add_argument("--scheduled", action="store_true")
    commands.add_parser("reconcile").add_argument("issues", nargs="+", type=int)
    approve = commands.add_parser("approve")
    approve.add_argument("sprint", type=int)
    approve.add_argument("issues", nargs="+", type=int)
    return parser.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        args = parse(argv)
        settings = Settings.load(args.config)
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if not token:
            log("credentials_missing", credential="GITHUB_TOKEN")
            return 2
        github = GitHub(os.getenv("GITHUB_REPOSITORY", ""), token)
        Application(args, settings, github).run()
    except (ApiError, ValueError) as error:
        log("automation_failed", error_type=type(error).__name__,
            detail=str(error) if isinstance(error, ApiError) else "Review configuration, input and permissions")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
