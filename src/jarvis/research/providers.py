"""Search and lookup providers that work without an API key.

The bundled provider is a PyPI lookup. It is deliberately named a *lookup* and
not a search engine, because that is what it is: PyPI's JSON API answers "tell me
about this package", not "find me packages like this". PyPI does have a
full-text search page, but it is gated behind a JavaScript client challenge, and
working around an access control is not something Jarvis does. So the honest
capability is exact lookup, and the docstring says so rather than implying
discovery.

Everything goes through the same :class:`~jarvis.research.fetch.HttpFetcher` as
the rest of the research path, so the SSRF guard, robots.txt handling, rate
limit, size cap and user agent are inherited rather than reimplemented - a second
network path would be a second place to get those wrong.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from jarvis.agents.research import SearchHit
from jarvis.core.confidence import Authority
from jarvis.research.fetch import FetchRefused, FetchResult, HttpFetcher

__all__ = [
    "PROSE_WORDS",
    "PYPI_BASE",
    "PyPILookup",
    "PyPIRecord",
    "normalise_package_name",
]

PYPI_BASE = "https://pypi.org/pypi"

#: PEP 503 normalisation: runs of [-_.] collapse to a single dash, lowercased.
#: Without it "Requests" and "requests" look like different packages and one of
#: the two queries silently returns nothing.
_NORMALISE = re.compile(r"[-_.]+")


def normalise_package_name(name: str) -> str:
    """PEP 503 normalise a distribution name."""

    return _NORMALISE.sub("-", name.strip()).lower()


#: Words that mark a phrase as prose rather than a list of distribution names.
#: Deliberately small and unambiguous: these never appear inside a real package
#: name, so a false positive costs a lookup while a false negative returns junk.
PROSE_WORDS = frozenset(
    {
        "the", "a", "an", "about", "for", "of", "to", "is", "are", "what", "which",
        "tell", "me", "find", "search", "package", "packages", "library", "libraries",
        "python", "module", "modules", "on", "in", "with", "please", "can", "you",
        "does", "do", "how", "any", "some", "best", "good", "popular",
    }
)


def _candidate_names(query: str) -> list[str]:
    """Split a query into package names, or return [] if it reads as prose.

    Commas always separate names. Whitespace separates names only when no token
    is an ordinary English word, so "requests, flask" and "requests flask" both
    work while "tell me about the requests package" is refused rather than
    answered with whatever "the" happens to resolve to.
    """
    text = query.strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    tokens = text.split()
    if any(token.lower() in PROSE_WORDS for token in tokens):
        return []
    return tokens


@dataclass(slots=True)
class PyPIRecord:
    """What the JSON API says about one distribution.

    Attributes
    ----------
    vulnerable:
        Populated from the API's own vulnerability list. Surfaced because
        recommending a package with known CVEs would be a disservice, and the
        API already tells us.
    """

    name: str
    version: str
    summary: str = ""
    home_page: str = ""
    author: str = ""
    licence: str = ""
    requires_python: str = ""
    releases: int = 0
    vulnerable: list[str] = None  # type: ignore[assignment]
    url: str = ""

    def __post_init__(self) -> None:
        if self.vulnerable is None:
            self.vulnerable = []

    @property
    def has_known_vulnerabilities(self) -> bool:
        return bool(self.vulnerable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "home_page": self.home_page,
            "author": self.author,
            "licence": self.licence,
            "requires_python": self.requires_python,
            "releases": self.releases,
            "vulnerable": list(self.vulnerable),
            "url": self.url,
        }


class PyPILookup:
    """Look a Python package up on PyPI. Satisfies the ``SearchProvider`` protocol.

    This is exact lookup, not discovery. ``search("requests")`` returns the
    ``requests`` distribution; it will not find "an HTTP library" from a
    description. Callers that need discovery need a real search API and a key.
    """

    name = "pypi"

    def __init__(
        self,
        fetcher: Any = None,
        *,
        base: str = PYPI_BASE,
        timeout: float = 20.0,
    ) -> None:
        #: Reusing the guarded fetcher means the SSRF check, robots handling and
        #: rate limit apply here too. A hand-rolled request would bypass all of it.
        self.fetcher = fetcher or HttpFetcher(timeout_s=timeout)
        self.base = base.rstrip("/")

    # --- protocol ------------------------------------------------------------
    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        """Look up ``query`` as one or more package names.

        This is exact lookup, not full-text search, and the difference matters.
        Splitting an ordinary sentence on whitespace and looking up every word
        finds real packages for filler words - "the", "python" and "package" all
        exist on PyPI - and returns confident nonsense. So a multi-word query is
        only treated as a name list when it does not read as prose.
        """
        self._last_reason = ""
        names = _candidate_names(query)
        if not names:
            self._last_reason = (
                "PyPI lookup takes package names, not a description. This provider "
                "does exact name lookup; PyPI's own full-text search sits behind a "
                "JavaScript challenge that Jarvis will not work around."
            )
            return []

        hits: list[SearchHit] = []
        seen: set[str] = set()
        for candidate in names:
            if len(hits) >= max(1, limit):
                break
            normalised = normalise_package_name(candidate)
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            record = self.lookup(normalised)
            if record is None:
                continue
            hits.append(self._as_hit(record))
        return hits

    # --- lookup --------------------------------------------------------------
    def lookup(self, name: str) -> PyPIRecord | None:
        """Fetch one distribution, or None if it does not exist.

        A missing package is a normal answer, not an error. A refused or failed
        fetch is also None, but the caller can distinguish the two through
        :meth:`explain`.
        """
        self._last_reason = ""
        normalised = normalise_package_name(name)
        if not normalised:
            self._last_reason = "no package name was given"
            return None
        url = f"{self.base}/{quote(normalised)}/json"
        try:
            result: FetchResult = self.fetcher.fetch(url)
        except FetchRefused as exc:
            self._last_reason = f"the request was refused: {exc}"
            return None
        except Exception as exc:  # network failure, timeout, decode error
            self._last_reason = f"the lookup failed: {type(exc).__name__}: {exc}"
            return None

        if not getattr(result, "ok", False):
            #: FetchResult carries `problem`, not `reason`.
            self._last_reason = getattr(result, "problem", "") or "the lookup did not succeed"
            return None
        text = getattr(result, "body", "") or ""
        if not text:
            self._last_reason = "the response had no body"
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._last_reason = f"the response was not JSON: {exc}"
            return None
        return self._parse(payload, url)

    def explain(self) -> str:
        """Why the last lookup returned nothing.

        "Not found" and "the network refused" are different answers and a caller
        should not have to guess between them.
        """
        return getattr(self, "_last_reason", "")

    # --- parsing -------------------------------------------------------------
    @staticmethod
    def _parse(payload: dict[str, Any], url: str) -> PyPIRecord:
        info = payload.get("info") or {}
        releases = payload.get("releases") or {}
        #: The API's vulnerability list is per-file in some responses and a flat
        #: list in others; handle both rather than dropping the information.
        raw_vulns = payload.get("vulnerabilities") or []
        vulnerable: list[str] = []
        for item in raw_vulns:
            if isinstance(item, dict):
                identifier = item.get("id") or item.get("aliases") or ""
                if isinstance(identifier, list):
                    identifier = ", ".join(str(v) for v in identifier)
                if identifier:
                    vulnerable.append(str(identifier))
            elif isinstance(item, str):
                vulnerable.append(item)

        return PyPIRecord(
            name=str(info.get("name") or ""),
            version=str(info.get("version") or ""),
            summary=str(info.get("summary") or "").strip(),
            home_page=str(
                info.get("home_page")
                or info.get("project_url")
                or (info.get("project_urls") or {}).get("Homepage", "")
                or ""
            ),
            author=str(info.get("author") or info.get("author_email") or ""),
            licence=str(info.get("license") or ""),
            requires_python=str(info.get("requires_python") or ""),
            releases=len(releases),
            vulnerable=vulnerable,
            url=url,
        )

    @staticmethod
    def _as_hit(record: PyPIRecord) -> SearchHit:
        snippet = record.summary or "No summary published on PyPI."
        if record.has_known_vulnerabilities:
            #: Warnings belong in the snippet, where a reader will see them,
            #: not only in structured data that may never be displayed.
            snippet = (
                f"{snippet} WARNING: PyPI reports known vulnerabilities: "
                f"{', '.join(record.vulnerable[:5])}."
            )
        return SearchHit(
            title=f"{record.name} {record.version}".strip(),
            url=record.home_page or record.url,
            snippet=snippet,
            #: pypi.org is the authoritative registry for Python packages, so
            #: the record itself is primary - but that says nothing about the
            #: package's claims, which remain the author's.
            authority=Authority.PRIMARY,
            publisher=record.author or "PyPI",
            published=None,
        )
