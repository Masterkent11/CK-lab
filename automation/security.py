import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import SEVERITIES
from .github import ApiError, log
from .issues import labels


CVE = re.compile(r"CVE-\d{4}-\d{4,}")
GHSA = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}")
REPO_PATH = re.compile(r"[-\w./@]+", re.ASCII)
SEVERITY_TAGS = {f"severity:{level.lower()}" for level in SEVERITIES}


def severity(score, vendor=None):
    if score is not None and math.isfinite(score) and 0 <= score <= 10:
        for minimum, level in ((9, "Critical"), (7, "High"), (4, "Medium"), (0.1, "Low")):
            if score >= minimum:
                return level
        return "Informational"
    return {level.lower(): level for level in SEVERITIES}.get(vendor, {
        "moderate": "Medium",
    }.get(vendor, "Informational"))


@dataclass(frozen=True)
class Finding:
    key: str
    source: str
    affected: str
    evidence: str
    confidence: str
    severity: str
    impact: str
    remediation: str
    owner: str = "Security triage"
    advisory: str = "Unavailable"
    state: str = "Open"
    validated: bool = False
    synthetic: bool = False

    def body(self):
        fields = (
            ("Finding ID", self.key), ("Scanner", self.source), ("Affected", self.affected),
            ("Evidence", self.evidence), ("Confidence", self.confidence), ("CVE and CVSS", self.advisory),
            ("Impact", self.impact), ("Suggested severity", self.severity), ("Remediation", self.remediation),
            ("Status", self.state), ("Owner", self.owner),
        )
        return "\n\n".join(f"### {name}\n\n{value}" for name, value in fields)


def dependabot_finding(alert, repository_url):
    advisory = alert["security_advisory"]
    dependency = alert["dependency"]
    vulnerability = alert["security_vulnerability"]
    cvss = advisory.get("cvss_severities") or {}
    scores = [cvss.get(version) or {} for version in ("cvss_v4", "cvss_v3")]
    scores.append(advisory.get("cvss") or {})
    cvss_data = next((value for value in scores if value.get("vector_string") and value.get("score") is not None), {})
    score = cvss_data.get("score")
    ghsa = advisory.get("ghsa_id") or ""
    cve = advisory.get("cve_id") or ""
    package = dependency["package"]["name"]
    affected = f"{package} in {dependency['manifest_path']}"
    patched = (vulnerability.get("first_patched_version") or {}).get("identifier")
    remediation = f"Upgrade to {patched} or later and retest." if patched else "Review vendor mitigation or replace the dependency."
    validated = bool(GHSA.fullmatch(ghsa) and package and vulnerability.get("vulnerable_version_range")
                     and alert["state"] == "open")
    advisory_info = [cve] if CVE.fullmatch(cve) else []
    if score is not None:
        advisory_info.append(f"CVSS {score:g} ({cvss_data['vector_string']})")
    number = int(alert["number"])
    return Finding(
        key=f"dependabot:{number}", source="Dependabot", affected=affected,
        evidence=f"{ghsa} · {vulnerability.get('vulnerable_version_range', 'Range unavailable')}\n\n"
                 f"{repository_url}/security/dependabot/{number}",
        confidence="Advisory and affected dependency matched" if validated else "Review scanner state",
        severity=severity(score, vulnerability.get("severity") or advisory.get("severity")),
        impact="Review reachability, data exposure and machine safety in the deployed product.",
        remediation=remediation, advisory=" · ".join(advisory_info) or "Unavailable",
        state=alert["state"].replace("_", " ").title(), validated=validated,
    )


def secret_findings(report):
    for record in report:
        rule, path, commit, line = (record.get(key) for key in ("RuleID", "File", "Commit", "StartLine"))
        if not rule or not path or not commit or not isinstance(line, int) or line < 1:
            raise ValueError("Gitleaks returned an incomplete finding")
        identity = hashlib.sha256(f"{rule}:{path}:{commit}:{line}".encode()).hexdigest()[:24]
        affected = f"{path}:{line}" if REPO_PATH.fullmatch(path) else "See restricted scan evidence"
        evidence = f"Rule {rule} at commit {commit[:12]}" if REPO_PATH.fullmatch(rule) else "Matched a secret rule"
        yield Finding(f"gitleaks:{identity}", "Gitleaks", affected, evidence, "Pattern match",
                      "Medium", "A credential may have been exposed. Its validity is unknown.",
                      "Review in private. Revoke and rotate any live credential.")


class SecurityFindingService:
    def __init__(self, github, store, project, notifier):
        self.github, self.store, self.project, self.notifier = github, store, project, notifier

    def process(self, finding, *, public):
        log("finding_classified", finding=finding.key, source=finding.source, severity=finding.severity,
            validated=finding.validated, synthetic=finding.synthetic)
        if finding.synthetic:
            return
        title = f"Review {finding.source} finding {finding.key}"
        if public:
            body = (
                f"### Finding ID\n\n{finding.key}\n\n### Scanner\n\n{finding.source}\n\n"
                "### Evidence\n\nReview the scanner evidence using the repository security process.\n\n"
                f"### Suggested severity\n\n{finding.severity}\n\n### Status\n\n{finding.state}\n\n"
                f"### Owner\n\n{finding.owner}"
            )
        else:
            body = finding.body()
        tag = f"severity:{finding.severity.lower()}"
        issue = self.store.upsert("finding:" + finding.key, title, body, ("security", tag), update=True)
        if issue:
            self.store.sync_labels(issue, add=(tag,), remove=SEVERITY_TAGS - {tag})
            if self.project:
                self.project.ensure_item(issue)
                self.project.set(issue, "Track", "Security")
                self.project.set(issue, "Severity", finding.severity)
                self.project.set(issue, "Status", "Backlog", initial=True)
        if finding.validated and finding.severity in {"Critical", "High"}:
            url = self.github.safe_link(issue["html_url"]) if issue else self.github.repository_url()
            self.notifier.send("security", f"{finding.key}:{finding.severity}",
                               f"{finding.severity} security finding\n{finding.source} · {finding.key}\n"
                               f"Review affected versions and reachability.\n{url}")

    def scan(self, report_path=None):
        public = self.github.rest()["visibility"] == "public"
        if report_path:
            findings = list(secret_findings(json.loads(Path(report_path).read_text(encoding="utf-8")) or []))
            log("scan_complete", source="Gitleaks", findings=len(findings))
            self._bounded(findings, public)
        try:
            alerts = list(self.github.pages("/dependabot/alerts", limit=10))
        except ApiError as error:
            if error.status not in {403, 404} or os.getenv("DEPENDABOT_TOKEN"):
                raise
            log("scanner_unavailable", source="Dependabot", status=error.status)
            return
        log("scan_complete", source="Dependabot", findings=len(alerts))
        active = [dependabot_finding(alert, self.github.repository_url()) for alert in alerts
                  if alert["state"] == "open" or self.store.find(f"finding:dependabot:{alert['number']}")]
        self._bounded(active, public)

    def _bounded(self, findings, public):
        ordered = sorted(findings, key=lambda item: (SEVERITIES.index(item.severity), item.key))
        new = 0
        skipped = 0
        for finding in ordered:
            if not self.store.find("finding:" + finding.key):
                if new >= 10:
                    skipped += 1
                    continue
                new += 1
            self.process(finding, public=public)
        if skipped:
            log("finding_creation_limit", deferred=skipped)
