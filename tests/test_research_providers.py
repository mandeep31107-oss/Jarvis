"""Tests for the PyPI lookup provider.

Most tests use a fake fetcher, so they run offline and deterministically and can
exercise the awkward paths - 404s, malformed JSON, empty bodies - that a live API
will not produce on demand. One test hits the real API and skips when there is no
network, rather than failing for a reason unrelated to the code.
"""

from __future__ import annotations

import json
import socket

import pytest

from jarvis.agents.research import SearchHit
from jarvis.core.confidence import Authority
from jarvis.research.fetch import FetchRefused, FetchResult
from jarvis.research.providers import (
    PyPILookup,
    normalise_package_name,
)

# --------------------------------------------------------------------- helpers


class FakeFetcher:
    """Returns canned responses and records what it was asked for."""

    name = "fake"

    def __init__(self, responses: dict[str, FetchResult | Exception] | None = None) -> None:
        self.responses = responses or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requested.append(url)
        item = self.responses.get(url)
        if item is None:
            return FetchResult(ok=False, url=url, status=404, problem="the site returned 404")
        if isinstance(item, Exception):
            raise item
        return item


def ok_payload(name: str, version: str, **extra: object) -> dict[str, object]:
    info: dict[str, object] = {
        "name": name,
        "version": version,
        "summary": f"A package called {name}.",
        "home_page": f"https://example.org/{name}",
        "author": "Someone",
        "license": "MIT",
        "requires_python": ">=3.10",
    }
    info.update(extra.pop("info", {}) or {})
    return {"info": info, "releases": extra.pop("releases", {version: []}), **extra}


def json_result(url: str, payload: dict[str, object]) -> FetchResult:
    return FetchResult(ok=True, url=url, status=200, content_type="application/json", body=json.dumps(payload))


def provider_for(payload: dict[str, object], *, name: str = "demo") -> tuple[PyPILookup, FakeFetcher]:
    url = f"https://pypi.org/pypi/{name}/json"
    fetcher = FakeFetcher({url: json_result(url, payload)})
    return PyPILookup(fetcher=fetcher), fetcher


def online() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


# --------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("requests", "requests"),
        ("Requests", "requests"),
        ("Flask_SQLAlchemy", "flask-sqlalchemy"),
        ("zope.interface", "zope-interface"),
        ("a--b__c..d", "a-b-c-d"),
        ("  padded  ", "padded"),
    ],
)
def test_pep503_normalisation(given: str, expected: str) -> None:
    """Without it "Requests" and "requests" look like different packages."""
    assert normalise_package_name(given) == expected


def test_normalisation_makes_lookups_case_insensitive() -> None:
    lookup, fetcher = provider_for(ok_payload("demo", "1.0"))

    assert lookup.lookup("Demo") is not None
    assert fetcher.requested == ["https://pypi.org/pypi/demo/json"]


# --------------------------------------------------------------------- lookup


def test_a_package_is_parsed_into_a_record() -> None:
    lookup, _ = provider_for(ok_payload("demo", "2.3.4"))

    record = lookup.lookup("demo")

    assert record is not None
    assert record.name == "demo"
    assert record.version == "2.3.4"
    assert record.requires_python == ">=3.10"
    assert record.releases == 1
    assert record.has_known_vulnerabilities is False


def test_the_release_count_comes_from_the_releases_map() -> None:
    payload = ok_payload("demo", "1.0", releases={"0.1": [], "0.2": [], "1.0": []})
    lookup, _ = provider_for(payload)

    assert lookup.lookup("demo").releases == 3


def test_a_missing_package_returns_none_and_explains_it() -> None:
    """Not found is a normal answer, and it must not look like a crash."""
    lookup, _ = provider_for(ok_payload("demo", "1.0"), name="demo")

    assert lookup.lookup("nothing-here") is None
    assert "404" in lookup.explain()


def test_an_empty_query_is_refused_without_a_network_call() -> None:
    lookup, fetcher = provider_for(ok_payload("demo", "1.0"))

    assert lookup.lookup("") is None
    assert lookup.explain() == "no package name was given"
    assert fetcher.requested == []


def test_malformed_json_is_reported_not_raised() -> None:
    url = "https://pypi.org/pypi/demo/json"
    fetcher = FakeFetcher(
        {url: FetchResult(ok=True, url=url, status=200, content_type="application/json", body="<html>not json</html>")}
    )
    lookup = PyPILookup(fetcher=fetcher)

    assert lookup.lookup("demo") is None
    assert "not JSON" in lookup.explain()


def test_an_empty_body_is_reported() -> None:
    url = "https://pypi.org/pypi/demo/json"
    fetcher = FakeFetcher({url: FetchResult(ok=True, url=url, status=200, body="")})
    lookup = PyPILookup(fetcher=fetcher)

    assert lookup.lookup("demo") is None
    assert "no body" in lookup.explain()


def test_a_refused_fetch_is_reported_rather_than_raised() -> None:
    fetcher = FakeFetcher({"https://pypi.org/pypi/demo/json": FetchRefused("blocked host")})
    lookup = PyPILookup(fetcher=fetcher)

    assert lookup.lookup("demo") is None
    assert "refused" in lookup.explain()


def test_a_network_error_is_reported_rather_than_raised() -> None:
    fetcher = FakeFetcher({"https://pypi.org/pypi/demo/json": TimeoutError("timed out")})
    lookup = PyPILookup(fetcher=fetcher)

    assert lookup.lookup("demo") is None
    assert "TimeoutError" in lookup.explain()


def test_a_failed_lookup_explains_itself_separately_from_a_missing_package() -> None:
    """'Not found' and 'the network failed' are different answers."""
    url = "https://pypi.org/pypi/demo/json"
    missing = PyPILookup(fetcher=FakeFetcher())
    missing.lookup("demo")
    failed = PyPILookup(fetcher=FakeFetcher({url: TimeoutError("timed out")}))
    failed.lookup("demo")

    assert "404" in missing.explain()
    assert "timed out" in failed.explain()
    assert missing.explain() != failed.explain()


def test_a_record_with_a_missing_info_block_does_not_crash() -> None:
    lookup, _ = provider_for({"info": {}, "releases": {}})

    record = lookup.lookup("demo")

    assert record is not None
    assert record.name == ""
    assert record.version == ""


def test_the_homepage_falls_back_through_the_alternative_fields() -> None:
    payload = ok_payload("demo", "1.0", info={"home_page": "", "project_urls": {"Homepage": "https://fallback.example"}})
    lookup, _ = provider_for(payload)

    assert lookup.lookup("demo").home_page == "https://fallback.example"


# --------------------------------------------------------------- vulnerabilities


def test_known_vulnerabilities_are_surfaced() -> None:
    payload = ok_payload("demo", "1.0", vulnerabilities=[{"id": "CVE-2024-0001", "aliases": []}])
    lookup, _ = provider_for(payload)

    record = lookup.lookup("demo")

    assert record.has_known_vulnerabilities is True
    assert record.vulnerable == ["CVE-2024-0001"]


def test_a_warning_appears_in_the_snippet_a_reader_will_see() -> None:
    """Structured data alone is not enough if nothing displays it."""
    payload = ok_payload("demo", "1.0", vulnerabilities=[{"id": "CVE-2024-0001"}])
    lookup, _ = provider_for(payload)

    hits = lookup.search("demo")

    assert "WARNING" in hits[0].snippet
    assert "CVE-2024-0001" in hits[0].snippet


def test_plain_string_vulnerability_entries_are_also_handled() -> None:
    payload = ok_payload("demo", "1.0", vulnerabilities=["GHSA-abcd-1234"])
    lookup, _ = provider_for(payload)

    assert lookup.lookup("demo").vulnerable == ["GHSA-abcd-1234"]


# ----------------------------------------------------------------------- search


def test_search_returns_a_hit_for_a_real_package() -> None:
    lookup, _ = provider_for(ok_payload("demo", "1.0"))

    hits = lookup.search("demo")

    assert len(hits) == 1
    assert isinstance(hits[0], SearchHit)
    assert hits[0].title == "demo 1.0"
    assert hits[0].authority is Authority.PRIMARY


def test_a_query_naming_several_packages_looks_each_one_up() -> None:
    urls = {
        "https://pypi.org/pypi/alpha/json": json_result(
            "https://pypi.org/pypi/alpha/json", ok_payload("alpha", "1.0")
        ),
        "https://pypi.org/pypi/beta/json": json_result(
            "https://pypi.org/pypi/beta/json", ok_payload("beta", "2.0")
        ),
    }
    lookup = PyPILookup(fetcher=FakeFetcher(urls))

    hits = lookup.search("alpha, beta")

    assert [h.title for h in hits] == ["alpha 1.0", "beta 2.0"]


def test_the_limit_is_respected() -> None:
    urls = {
        f"https://pypi.org/pypi/p{i}/json": json_result(
            f"https://pypi.org/pypi/p{i}/json", ok_payload(f"p{i}", "1.0")
        )
        for i in range(5)
    }
    lookup = PyPILookup(fetcher=FakeFetcher(urls))

    assert len(lookup.search("p0 p1 p2 p3 p4", limit=2)) == 2


def test_results_are_not_padded_out_to_the_limit() -> None:
    """Inventing extra hits to look thorough would be fabrication."""
    lookup, fetcher = provider_for(ok_payload("demo", "1.0"))

    hits = lookup.search("demo nothing-here also-missing", limit=5)

    assert len(hits) == 1
    assert len(fetcher.requested) == 3


def test_duplicate_names_in_a_query_are_only_looked_up_once() -> None:
    lookup, fetcher = provider_for(ok_payload("demo", "1.0"))

    assert len(lookup.search("demo demo DEMO")) == 1
    assert fetcher.requested == ["https://pypi.org/pypi/demo/json"]


def test_a_package_with_no_summary_says_so_rather_than_leaving_it_blank() -> None:
    payload = ok_payload("demo", "1.0", info={"summary": ""})
    lookup, _ = provider_for(payload)

    assert lookup.search("demo")[0].snippet == "No summary published on PyPI."


def test_records_serialise_to_json_safe_dicts() -> None:
    lookup, _ = provider_for(ok_payload("demo", "1.0"))

    json.dumps(lookup.lookup("demo").as_dict())


def test_the_default_fetcher_is_the_ssrf_guarded_one() -> None:
    """A second network path would be a second place to get the guards wrong."""
    from jarvis.research.fetch import HttpFetcher

    lookup = PyPILookup()

    assert isinstance(lookup.fetcher, HttpFetcher)


def test_the_provider_satisfies_the_search_protocol() -> None:
    lookup = PyPILookup(fetcher=FakeFetcher())

    assert lookup.name == "pypi"
    assert callable(lookup.search)
    assert lookup.search("anything") == []


# ------------------------------------------------------------------- live API


@pytest.mark.skipif(not online(), reason="pypi.org is not reachable from here")
def test_the_live_api_returns_a_record_for_a_well_known_package() -> None:
    """The only check that proves the URL shape and parsing match reality."""
    record = PyPILookup().lookup("requests")

    assert record is not None
    assert record.name.lower() == "requests"
    assert record.version, "a published version must be reported"
    assert record.releases > 100, "requests has a long release history"


@pytest.mark.skipif(not online(), reason="pypi.org is not reachable from here")
def test_the_live_api_reports_a_missing_package_as_absent() -> None:
    lookup = PyPILookup()

    assert lookup.lookup("jarvis-this-package-does-not-exist-9911") is None
    assert "404" in lookup.explain()


# ---------------------------------------------------------------- prose guard


@pytest.mark.parametrize(
    "query",
    [
        "tell me about the requests python package",
        "find the best http library",
        "what is the flask module",
        "search for a good csv parser",
    ],
)
def test_a_sentence_is_refused_rather_than_looked_up_word_by_word(query: str) -> None:
    """Every filler word in a sentence may be a real package name.

    "the", "python" and "package" all exist on PyPI, so naive splitting returns
    confident results for words that were never meant as names.
    """
    urls = {
        "https://pypi.org/pypi/the/json": json_result(
            "https://pypi.org/pypi/the/json", ok_payload("the", "1.0")
        ),
    }
    fetcher = FakeFetcher(urls)
    lookup = PyPILookup(fetcher=fetcher)

    assert lookup.search(query) == []
    assert "package names" in lookup.explain()
    #: Nothing was requested at all - the query was rejected before any I/O.
    assert fetcher.requested == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("requests", ["requests"]),
        ("requests, flask", ["requests", "flask"]),
        ("requests flask", ["requests", "flask"]),
        ("Flask_SQLAlchemy", ["Flask_SQLAlchemy"]),
    ],
)
def test_name_lists_are_still_accepted(query: str, expected: list[str]) -> None:
    from jarvis.research.providers import _candidate_names

    assert _candidate_names(query) == expected


def test_commas_separate_names_even_around_prose_words() -> None:
    """A comma is an explicit separator, so the prose guard does not apply."""
    from jarvis.research.providers import _candidate_names

    assert _candidate_names("requests, the, flask") == ["requests", "the", "flask"]


def test_an_explanation_is_given_when_a_query_is_not_a_name() -> None:
    lookup = PyPILookup(fetcher=FakeFetcher())

    assert lookup.search("tell me about the requests package") == []

    reason = lookup.explain()
    assert "exact name lookup" in reason
    #: States the boundary honestly instead of implying capability it lacks.
    assert "JavaScript challenge" in reason
