import hashlib
import json
import os
from datetime import datetime, timedelta

from .github import ApiError, Http, log, utc_now
from .issues import MARKER


class TelegramNotificationAdapter:
    def __init__(self, token=None, chat=None, http=None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat = chat or os.getenv("TELEGRAM_CHAT_ID")
        self.http = http or Http()

    @property
    def configured(self):
        return bool(self.token and self.chat)

    def send(self, message):
        if not self.configured:
            raise ValueError("Telegram credentials are missing")
        if not self.token.partition(":")[0].isdigit() or any(ch in self.token for ch in "/?#\\ \r\n"):
            raise ValueError("Telegram bot token has an invalid format")
        if len(message) > 3800:
            lines = message.splitlines()
            footer = lines[-1] if lines and lines[-1].startswith("https://") else ""
            message = message[:3500].rsplit("\n", 1)[0] + "\nSee GitHub for the full report.\n" + footer
        payload, _ = self.http.request(
            f"https://api.telegram.org/bot{self.token}/sendMessage", operation="Telegram sendMessage",
            method="POST", payload={"chat_id": self.chat, "text": message,
                                    "link_preview_options": {"is_disabled": True}},
        )
        if not payload.get("ok"):
            raise ApiError("Telegram sendMessage", payload.get("error_code", "invalid"))
        return payload["result"]["message_id"]


class NotificationRouter:
    LEDGER = "ledger:notifications"
    HEADING = "Delivery records for engineering automation. Keep this issue closed."

    def __init__(self, settings, store, telegram, now=utc_now):
        self.config = settings.values["notifications"]
        self.store, self.telegram, self.now = store, telegram, now

    def _body(self, entries):
        return self.HEADING + "\n\n```json\n" + json.dumps(entries, indent=2, sort_keys=True) + "\n```"

    def _load(self):
        ledger = self.store.upsert(self.LEDGER, "Engineering automation delivery log", self._body({}),
                                   ("task",))
        if ledger["state"] != "closed":
            ledger = self.store.github.rest(f"/issues/{ledger['number']}", method="PATCH",
                                            payload={"state": "closed", "state_reason": "not_planned"})
        try:
            entries = json.loads(ledger["body"].split("```json\n", 1)[1].split("\n```", 1)[0])
        except (IndexError, ValueError):
            raise ValueError("Restore the delivery log JSON before retrying") from None
        return ledger, entries

    def _save(self, ledger, entries):
        return self.store.upsert(self.LEDGER, ledger["title"], self._body(entries), update=True)

    def send(self, event, key, message, *, group=None):
        if not self.config["enabled"] or event not in self.config["events"]:
            log("notification_disabled", route=event)
            return False
        if not self.telegram.configured:
            log("notification_unconfigured", route=event)
            return False
        if not self.store.writes:
            log("notification_dry_run", route=event)
            return False
        ledger, entries = self._load()
        now = self.now()
        cutoff = now - timedelta(days=90)
        entries = {k: item for k, item in entries.items()
                   if datetime.fromisoformat(item["at"]) > cutoff}
        identity = hashlib.sha256(f"{self.telegram.chat}:{event}:{key}".encode()).hexdigest()[:24]
        if entries.get(identity, {}).get("state") in {"sent", "reserved", "uncertain", "suppressed"}:
            return False
        bucket = hashlib.sha256(f"{self.telegram.chat}:{event}:{group or key}".encode()).hexdigest()[:16]
        recent = now - timedelta(minutes=self.config["cooldown_minutes"].get(event, 0))
        limited = any(item["group"] == bucket and item["state"] in {"sent", "reserved", "uncertain"}
                      and datetime.fromisoformat(item["at"]) > recent for item in entries.values())
        entries = dict(sorted(entries.items(), key=lambda item: item[1]["at"])[-219:])
        entry = {"at": now.isoformat(), "group": bucket, "state": "suppressed" if limited else "reserved"}
        entries[identity] = entry
        self._save(ledger, entries)
        if limited:
            log("notification_cooldown", route=event, receipt=identity)
            return False
        try:
            self.telegram.send(message)
        except ApiError as error:
            entry["state"] = "uncertain" if error.uncertain else "failed"
            self._save(ledger, entries)
            log("notification_failed", route=event, receipt=identity, status=error.status)
            raise
        entry["state"] = "sent"
        self._save(ledger, entries)
        log("notification_delivered", route=event, receipt=identity)
        return True


class ReminderService:
    def __init__(self, store):
        self.store = store

    def overdue(self, today, work):
        year, week, _ = today.isocalendar()
        for item in work:
            assignees = item.issue.get("assignees", [])
            if item.done or item.fields.get("Due Date", "9999") >= today.isoformat() or not assignees:
                continue
            if MARKER.search(item.issue.get("body") or "") and item.issue["state"] != "open":
                continue
            mentions = " ".join("@" + person["login"] for person in assignees)
            self.store.comment(item.number, f"overdue:{year}:{week}",
                               f"{mentions} This work is overdue. Please update the due date or note what is blocking it.")
