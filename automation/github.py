import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


LOG = logging.getLogger("automation")


def log(event, **fields):
    LOG.info(json.dumps({"event": event, **fields}, ensure_ascii=True))


class ApiError(RuntimeError):
    def __init__(self, operation, status, *, uncertain=False):
        super().__init__(f"{operation} failed ({status})")
        self.status = status
        self.uncertain = uncertain


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def __init__(self, opener=None, sleep=time.sleep):
        self.opener = opener or build_opener(NoRedirect())
        self.sleep = sleep

    def request(self, url, *, operation, method="GET", payload=None, headers=None, retry_safe=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(url, data=data, method=method, headers={
            "Content-Type": "application/json", "User-Agent": "embroidery-engineering", **(headers or {})
        })
        for attempt in range(4):
            try:
                with self.opener.open(request, timeout=20) as response:
                    body = response.read()
                    return (json.loads(body) if body else None), response.headers
            except HTTPError as error:
                status = error.code
                retry_after = error.headers.get("Retry-After")
                rate_limited = status == 429 or (
                    status == 403 and (retry_after or error.headers.get("X-RateLimit-Remaining") == "0")
                )
                reset = error.headers.get("X-RateLimit-Reset", "")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                if rate_limited and not retry_after:
                    delay = max(60, int(reset) - int(time.time())) if reset.isdigit() else 60
                retry = bool(rate_limited) or (retry_safe and status >= 500)
                uncertain = status >= 500
                error.close()
            except (URLError, TimeoutError, OSError, ValueError):
                status, delay, retry, uncertain = "transport", 2 ** attempt, retry_safe, True
            if not retry or attempt == 3 or delay > 60:
                raise ApiError(operation, status, uncertain=uncertain)
            log("retry", operation=operation, status=status, delay=delay)
            self.sleep(max(1, delay))
        raise AssertionError("retry loop exhausted")


class GitHub:
    def __init__(self, repository, token, *, http=None):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository, re.ASCII):
            raise ValueError("Set GITHUB_REPOSITORY to owner/repository")
        self.repository = repository
        self.token = token
        self.http = http or Http()
        self.api = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        self.graphql = os.getenv("GITHUB_GRAPHQL_URL", self.api + "/graphql")
        self.repo_path = f"/repos/{repository}"

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def rest(self, path="", *, method="GET", payload=None):
        return self._rest(self.repo_path + path, method=method, payload=payload)[0]

    def _rest(self, path, *, method="GET", payload=None):
        operation = f"GitHub {method} {path.split('?')[0]}"
        return self.http.request(self.api + path, operation=operation, method=method, payload=payload,
                                 headers=self.headers, retry_safe=method in {"GET", "PUT", "DELETE", "PATCH"})

    def pages(self, path, key=None, limit=50):
        path = self.repo_path + path
        path += ("&" if "?" in path else "?") + "per_page=100"
        for _ in range(limit):
            result, headers = self._rest(path)
            yield from (result[key] if key else result)
            link = re.search(r'<([^>]+)>; rel="next"', headers.get("Link", ""))
            if not link:
                return
            if not link[1].startswith(self.api + "/"):
                raise ApiError("GitHub pagination origin", "invalid")
            path = link[1][len(self.api):]
        raise ApiError("GitHub pagination", "page limit")

    def query(self, document, variables=None, *, token=None, operation="query"):
        mutating = document.lstrip().startswith("mutation")
        headers = {**self.headers, "Authorization": f"Bearer {token or self.token}"}
        response, _ = self.http.request(
            self.graphql, operation=f"GitHub GraphQL {operation}", method="POST", headers=headers,
            payload={"query": document, "variables": variables or {}}, retry_safe=not mutating,
        )
        if response.get("errors"):
            types = sorted({re.sub(r"[^A-Z_]", "", e.get("type", "")) or "UNKNOWN"
                            for e in response["errors"]})
            raise ApiError(f"GitHub GraphQL {operation}", ",".join(types), uncertain=mutating)
        return response["data"]

    def connection(self, document, variables, extract, *, token=None, operation="connection"):
        cursor = None
        for _ in range(50):
            page = extract(self.query(document, {**variables, "cursor": cursor}, token=token,
                                      operation=operation))
            yield from page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            cursor = page["pageInfo"]["endCursor"]
        raise ApiError(f"GitHub GraphQL {operation}", "page limit")

    def repository_url(self):
        return f"{self.server}/{self.repository}"

    def safe_link(self, url):
        parsed = urlsplit(url or "")
        server = urlsplit(self.server)
        if (parsed.scheme, parsed.netloc) == (server.scheme, server.netloc) and url:
            return url.split("#", 1)[0]
        return self.repository_url()

    def issue(self, number):
        return self.rest(f"/issues/{int(number)}")

    def issues(self, state="all"):
        return (issue for issue in self.pages(f"/issues?state={state}") if "pull_request" not in issue)

    def can_write_issues(self, login):
        try:
            permission = self.rest(f"/collaborators/{quote(login, safe='')}/permission")
        except ApiError as error:
            if error.status == 404:
                return False
            raise
        return permission["permission"] in {"admin", "maintain", "write"}

    def blocked_by(self, number):
        return [item for item in self.pages(f"/issues/{int(number)}/dependencies/blocked_by")
                if item["state"] == "open"]


def utc_now():
    return datetime.now(timezone.utc)
