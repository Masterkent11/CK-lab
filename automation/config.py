import json
import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent.parent
POINTS = (1, 2, 3, 5, 8)
PRIORITIES = ("P0", "P1", "P2", "P3")
TRACKS = ("UX/UI", "Engineering", "Security")
SEVERITIES = ("Critical", "High", "Medium", "Low", "Informational")
STATUSES = ("Backlog", "Ready", "In Progress", "In Review", "QA/UAT", "Done", "Blocked", "Cancelled")
TYPE_LABELS = ("epic", "story", "task", "bug", "spike")
STATUS_LABELS = {name: "status:" + name.lower().replace("/", " ").replace(" ", "-") for name in STATUSES}
LABELS = {
    **{name: "5319e7" for name in TYPE_LABELS},
    **{name: "d4c5f9" for name in STATUS_LABELS.values()},
    **{f"severity:{level.lower()}": "b60205" for level in SEVERITIES},
    "needs-triage": "fbca04", "proposal": "bfdadc", "qa-required": "1d76db",
    "qa-approved": "0e8a16", "security": "b60205", "automation": "c5def5", "blocked": "d93f0b",
}


@dataclass(frozen=True)
class Settings:
    values: dict

    @classmethod
    def load(cls, path):
        config = cls(json.loads(Path(path).read_text(encoding="utf-8")))
        config.zone
        date.fromisoformat(config.sprint["start"])
        for key in ("days", "capacity", "max_proposals", "iteration_horizon"):
            if not isinstance(config.sprint[key], int) or config.sprint[key] < 1:
                raise ValueError(f"Invalid sprint setting {key}")
        if config.sprint["max_proposals"] > 10 or config.sprint["iteration_horizon"] > 26:
            raise ValueError("Proposal and iteration limits exceeded")
        for name, schedule in config.values["schedules"].items():
            time.fromisoformat(schedule["time"])
            if not set(schedule["days"]) <= set(range(7)) or not 1 <= schedule["window_hours"] <= 24:
                raise ValueError(f"Invalid schedule {name}")
        return config

    @property
    def zone(self):
        try:
            return ZoneInfo(self.values["timezone"])
        except ZoneInfoNotFoundError as error:
            if self.values["timezone"] == "Asia/Manila":
                from datetime import timedelta, timezone
                return timezone(timedelta(hours=8), "Asia/Manila")
            raise ValueError("Install the system IANA timezone database") from error

    @property
    def sprint(self):
        return self.values["sprint"]

    @property
    def project(self):
        return self.values["project"]

    @property
    def project_number(self):
        value = os.getenv("ENGINEERING_PROJECT_NUMBER") or self.project.get("number")
        return int(value) if value else None

    @property
    def writes(self):
        return os.getenv("AUTOMATION_WRITES", "").lower() == "true"
