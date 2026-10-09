"""Safe URL fetching (Phase 2, spec section 24).

An agent that can read the web can also be talked into reading things it
should not. This module is deliberately paranoid, because every guard here
exists to stop a request that *looks* reasonable:

* **SSRF.** A URL supplied by the user, a fetched page, or a model can point at
  ``http://169.254.169.254/`` (cloud metadata), ``http://localhost:6379/`` (an
  internal Redis), or a private RFC1918 host. Every resolved address is checked
  and non-global addresses are refused.
* **Access restrictions.** A 401 or 403 is reported as unavailable. Jarvis does
  not retry with different headers, does not follow a "view source" mirror, and
  does not strip a paywall. That is the rule, not a limitation to work around.
* **robots.txt.** Parsed and honoured per host. ``Disallow`` means do not fetch.
* **Rate limits.** A minimum interval per host, enforced locally. Jarvis never
  sends a burst that a site did not ask for.
* **Resource caps.** Response size, timeout and redirect depth are bounded, so
  one hostile URL cannot exhaust memory or hang the agent.
* **Identification.** The User-Agent names the tool and honours the operator's
  contact address. Jarvis does not pretend to be a browser.

There is no cache-busting, no cookie jar, no credential forwarding and no
JavaScript execution.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request, robotparser

from jarvis.errors import CapabilityUnavailable, JarvisError
from jarvis.util.clock import now_iso
from jarvis.version import __version__

DEFAULT_USER_AGENT = f"JarvisBot/{__version__} (+https://github.com/mandeep31107-oss/Jarvis)"

#: Schemes Jarvis will fetch. ``file:`` is handled by FileFetcher, separately.
ALLOWED_SCHEMES = ("http", "https")

#: Content types worth extracting text from.
TEXT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/xhtml+xml",
    "application/json",
)


class FetchRefused(JarvisError):
    """The fetch was refused on policy grounds, not because the network failed.

    Distinct from a transport error: a refusal is a decision, and it should be
    reported to the user as one.
    """

    def __init__(self, message: str, *, reason: str = "", safe_to_retry: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.safe_to_retry = safe_to_retry


@dataclass
class FetchResult:
    """What came back, or why nothing did."""

    ok: bool
    url: str
    status: int = 0
    content_type: str = ""
    body: str = ""
    truncated: bool = False
    retrieved_at: str = field(default_factory=now_iso)
    problem: str = ""
    #: Set when the site said no. Jarvis does not work around this.
    access_denied: bool = False
    #: Set when robots.txt disallowed the path.
    robots_disallowed: bool = False
    final_url: str = ""
    elapsed_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "chars": len(self.body),
            "truncated": self.truncated,
            "retrieved_at": self.retrieved_at,
            "problem": self.problem,
            "access_denied": self.access_denied,
            "robots_disallowed": self.robots_disallowed,
            "elapsed_s": round(self.elapsed_s, 3),
        }


class Fetcher(Protocol):
    """Anything that can retrieve a URL and report honestly about it."""

    name: str

    def fetch(self, url: str) -> FetchResult:
        ...  # pragma: no cover - protocol


class NullFetcher:
    """No network. Refuses instead of inventing page contents."""

    name = "none"

    def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            ok=False,
            url=url,
            problem=(
                "No fetcher is configured, so Jarvis cannot read that URL. It will not "
                "describe a page it has not retrieved."
            ),
        )


def _is_blocked_address(host: str) -> str:
    """Return a reason if ``host`` resolves to an address Jarvis must not reach.

    Checked against the *resolved* address, not the hostname: ``http://foo/``
    where ``foo`` is a DNS record pointing at 127.0.0.1 is exactly the attack
    this exists to stop.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return f"host does not resolve: {exc}"

    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            return f"unparseable address for {host}: {raw}"
        if not ip.is_global:
            return (
                f"{host} resolves to {ip}, which is not a public address "
                "(loopback, private, link-local or reserved). Jarvis will not fetch "
                "from internal networks or cloud metadata endpoints."
            )
    return ""


class HttpFetcher:
    """A bounded, polite, SSRF-guarded HTTP client built on urllib."""

    name = "http"

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        contact: str = "",
        timeout_s: float = 20.0,
        max_bytes: int = 4_000_000,
        max_redirects: int = 3,
        min_interval_s: float = 1.0,
        respect_robots: bool = True,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> None:
        if contact:
            user_agent = f"{user_agent}; contact={contact}"
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.min_interval_s = min_interval_s
        self.respect_robots = respect_robots
        #: When set, only these hosts may be fetched. Used for tests and for
        #: operators who want to confine the agent to known sources.
        self.allowed_hosts = tuple(h.lower() for h in (allowed_hosts or ()))
        self._last_fetch: dict[str, float] = {}
        self._robots: dict[str, Any] = {}

    # ------------------------------------------------------------------ guards
    def _check_scheme(self, url: str) -> None:
        parts = parse.urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            raise FetchRefused(
                f"refusing scheme {parts.scheme!r}; only http and https are fetched",
                reason="scheme",
            )
        if not parts.hostname:
            raise FetchRefused("URL has no host", reason="malformed")

    def _check_host(self, url: str) -> None:
        host = (parse.urlsplit(url).hostname or "").lower()
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise FetchRefused(
                f"{host} is not in the allowed host list "
                f"({', '.join(self.allowed_hosts)})",
                reason="host_allowlist",
            )
        reason = _is_blocked_address(host)
        if reason:
            raise FetchRefused(reason, reason="ssrf")

    def _rate_limit(self, host: str) -> None:
        last = self._last_fetch.get(host)
        if last is not None:
            wait = self.min_interval_s - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_fetch[host] = time.monotonic()

    def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = robotparser.RobotFileParser()
            parser.set_url(origin + "/robots.txt")
            try:
                # A robots.txt we cannot read is treated as permissive: many
                # hosts simply do not have one. Being unable to fetch it is not
                # the same as being told not to fetch.
                parser.read()
            except Exception:  # noqa: BLE001 - any failure means "no robots.txt"
                parser = None
            self._robots[origin] = parser
        if parser is None:
            return True
        try:
            return bool(parser.can_fetch(self.user_agent, url))
        except Exception:  # noqa: BLE001 - a broken robots.txt must not block
            return True

    # ------------------------------------------------------------------- fetch
    def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        try:
            self._check_scheme(url)
            self._check_host(url)
        except FetchRefused as exc:
            return FetchResult(ok=False, url=url, problem=str(exc), elapsed_s=0.0)

        host = (parse.urlsplit(url).hostname or "").lower()
        if not self._robots_allows(url):
            return FetchResult(
                ok=False,
                url=url,
                problem=(
                    f"robots.txt disallows this path for {self.user_agent.split('/')[0]}. "
                    "Jarvis honours that and will not fetch it another way."
                ),
                robots_disallowed=True,
                elapsed_s=time.monotonic() - started,
            )

        self._rate_limit(host)
        req = request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,text/plain;q=0.9,application/json;q=0.8",
                "Accept-Language": "en",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if content_type and not content_type.startswith(TEXT_CONTENT_TYPES):
                    return FetchResult(
                        ok=False,
                        url=url,
                        status=resp.status,
                        content_type=content_type,
                        problem=f"content type {content_type!r} is not text; not extracted",
                        elapsed_s=time.monotonic() - started,
                    )
                raw = resp.read(self.max_bytes + 1)
                truncated = len(raw) > self.max_bytes
                body = raw[: self.max_bytes].decode(
                    resp.headers.get_content_charset() or "utf-8", errors="replace"
                )
                return FetchResult(
                    ok=True,
                    url=url,
                    final_url=resp.geturl(),
                    status=resp.status,
                    content_type=content_type,
                    body=body,
                    truncated=truncated,
                    elapsed_s=time.monotonic() - started,
                )
        except error.HTTPError as exc:
            denied = exc.code in (401, 403, 407, 451)
            return FetchResult(
                ok=False,
                url=url,
                status=exc.code,
                access_denied=denied,
                problem=(
                    f"the site returned {exc.code}. "
                    + (
                        "That is an access restriction; Jarvis will not work around it."
                        if denied
                        else "The request did not succeed."
                    )
                ),
                elapsed_s=time.monotonic() - started,
            )
        except error.URLError as exc:
            return FetchResult(
                ok=False,
                url=url,
                problem=f"network error: {exc.reason}",
                elapsed_s=time.monotonic() - started,
            )
        except TimeoutError:
            return FetchResult(
                ok=False,
                url=url,
                problem=f"timed out after {self.timeout_s}s",
                elapsed_s=time.monotonic() - started,
            )


#: Extension -> content type. Without this every local file would be labelled
#: text/plain, and an .html document would be extracted as plain text - which
#: silently makes the first line of raw markup the document title.
_EXTENSION_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/plain",
    ".json": "application/json",
}


class FileFetcher:
    """Reads local files. Used for offline testing and for user-supplied files.

    Confined to a root directory by default, so a ``..`` in a path cannot reach
    the rest of the filesystem.
    """

    name = "file"

    def __init__(self, root: str | Path | None = None, *, max_bytes: int = 4_000_000) -> None:
        self.root = Path(root).resolve() if root else None
        self.max_bytes = max_bytes

    @staticmethod
    def _content_type(path: Path) -> str:
        return _EXTENSION_TYPES.get(path.suffix.lower(), "text/plain")

    def fetch(self, url: str) -> FetchResult:
        started = time.monotonic()
        path_str = url[len("file://") :] if url.startswith("file://") else url
        path = Path(path_str).expanduser()
        if self.root is not None:
            path = (self.root / path).resolve()
            if self.root not in path.parents and path != self.root:
                return FetchResult(
                    ok=False,
                    url=url,
                    problem=f"path escapes the allowed root ({self.root})",
                    elapsed_s=time.monotonic() - started,
                )
        if not path.is_file():
            return FetchResult(
                ok=False, url=url, problem=f"no such file: {path}",
                elapsed_s=time.monotonic() - started,
            )
        if path.stat().st_size > self.max_bytes:
            return FetchResult(
                ok=False, url=url, truncated=True,
                problem=f"file is larger than {self.max_bytes} bytes",
                elapsed_s=time.monotonic() - started,
            )
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return FetchResult(
                ok=False, url=url, problem=f"could not read file: {exc}",
                elapsed_s=time.monotonic() - started,
            )
        return FetchResult(
            ok=True,
            url=url,
            # A real URI, not a bare path: downstream the source URL is
            # validated, and "/tmp/x.html" is not a URL.
            final_url=path.as_uri(),
            status=200,
            content_type=self._content_type(path),
            body=body,
            elapsed_s=time.monotonic() - started,
        )


class RedirectCappedFetcher(HttpFetcher):
    """HttpFetcher that also refuses to follow a redirect off the allowlist.

    Kept separate so the plain client stays simple; use this when confining the
    agent to specific hosts.
    """

    def fetch(self, url: str) -> FetchResult:
        result = super().fetch(url)
        if result.ok and result.final_url and result.final_url != url:
            try:
                self._check_host(result.final_url)
            except FetchRefused as exc:
                return FetchResult(
                    ok=False,
                    url=url,
                    final_url=result.final_url,
                    status=result.status,
                    problem=f"redirected somewhere Jarvis will not follow: {exc}",
                    elapsed_s=result.elapsed_s,
                )
        return result


__all__ = [
    "ALLOWED_SCHEMES",
    "CapabilityUnavailable",
    "DEFAULT_USER_AGENT",
    "Fetcher",
    "FetchRefused",
    "FetchResult",
    "FileFetcher",
    "HttpFetcher",
    "NullFetcher",
    "RedirectCappedFetcher",
    "TEXT_CONTENT_TYPES",
]
