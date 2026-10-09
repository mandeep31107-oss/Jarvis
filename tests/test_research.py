"""Phase 2: safe fetching, extraction, authority and the ingest pipeline."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jarvis.core.confidence import Authority
from jarvis.core.knowledge import KnowledgeBase
from jarvis.research.extract import extract, extract_meta, first_sentences
from jarvis.research.fetch import FileFetcher, HttpFetcher, NullFetcher, _is_blocked_address
from jarvis.research.pipeline import (
    ResearchPipeline,
    assess_authority,
    registrable_domain,
)

# ---------------------------------------------------------------- SSRF guards


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "169.254.169.254",  # cloud metadata endpoint
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "::1",
    ],
)
def test_non_public_hosts_are_refused(host):
    """The single most important guard in the fetcher.

    A URL is data: it can come from the user, from a page Jarvis read, or from a
    model. Pointing it at an internal address is the attack this stops.
    """
    reason = _is_blocked_address(host)
    assert reason, f"{host} was not blocked"
    assert "not a public address" in reason or "resolve" in reason


def test_the_fetcher_refuses_internal_urls_without_touching_the_network():
    fetcher = HttpFetcher()
    for url in (
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.0.1/",
        "http://[::1]/",
    ):
        result = fetcher.fetch(url)
        assert not result.ok
        assert "public address" in result.problem or "resolve" in result.problem


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://a/b", ""])
def test_only_http_and_https_are_fetched(url):
    result = HttpFetcher().fetch(url)
    assert not result.ok
    assert "scheme" in result.problem or "host" in result.problem


def test_the_host_allowlist_is_enforced():
    fetcher = HttpFetcher(allowed_hosts=("example.org",))
    result = fetcher.fetch("https://93.184.216.34/")  # example.com's address
    assert not result.ok
    assert "not in the allowed host list" in result.problem


def test_the_null_fetcher_says_it_read_nothing():
    result = NullFetcher().fetch("https://example.com/")
    assert not result.ok
    assert "will not" in result.problem


# ------------------------------------------------------------- file confinement


def test_file_fetcher_refuses_to_escape_its_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    result = FileFetcher(root).fetch("../outside.txt")
    assert not result.ok
    assert "escapes the allowed root" in result.problem


def test_file_fetcher_produces_a_real_uri(tmp_path):
    (tmp_path / "doc.txt").write_text("hello there", encoding="utf-8")
    result = FileFetcher(tmp_path).fetch("doc.txt")
    assert result.ok
    assert result.final_url.startswith("file:///"), result.final_url


def test_file_fetcher_reports_a_missing_file(tmp_path):
    result = FileFetcher(tmp_path).fetch("nope.txt")
    assert not result.ok
    assert "no such file" in result.problem


# ------------------------------------------------------------------- extraction


PAGE = """<!DOCTYPE html>
<html lang="en-GB">
<head>
  <title>  Digital Services Act  </title>
  <meta name="description" content="Obligations under the DSA.">
  <meta property="og:site_name" content="EUR-Lex">
  <meta property="og:locale" content="en_GB">
  <link rel="canonical" href="https://example.eu/dsa">
  <style>body { color: red }</style>
  <script>var tracking = 1;</script>
</head>
<body>
  <nav>Home About Contact</nav>
  <h1>The Digital Services Act</h1>
  <p>The Digital Services Act regulates online intermediaries and platforms.
     It applies from 17 February 2024 to services offered in the Union.</p>
  <h2>Who it applies to</h2>
  <p>It applies regardless of where the provider is established, including providers
     based outside the Union that offer services to users inside it. National coordinators
     supervise enforcement and may impose penalties for breaches.</p>
  <a href="https://example.eu/other">more</a>
  <footer>Copyright</footer>
</body>
</html>"""


def test_extraction_reads_the_title_from_inside_head():
    """<head> is skipped wholesale, so <title> needs an explicit exception."""
    assert extract(PAGE).title == "Digital Services Act"


def test_extraction_drops_scripts_styles_and_navigation():
    text = extract(PAGE).text
    assert "color: red" not in text
    assert "var tracking" not in text
    assert "Home About Contact" not in text


def test_extraction_keeps_headings_and_metadata():
    result = extract(PAGE)
    assert result.headings == ["The Digital Services Act", "Who it applies to"]
    assert result.language == "en"
    assert result.publisher == "EUR-Lex"
    assert result.canonical_url == "https://example.eu/dsa"
    assert result.description == "Obligations under the DSA."
    assert "https://example.eu/other" in result.links


def test_a_page_with_hardly_any_text_is_not_usable():
    assert not extract("<html><body><p>hi</p></body></html>").usable
    assert extract(PAGE).usable


def test_plain_text_is_not_treated_as_html():
    result = extract("# Title\n\nSome prose.", content_type="text/markdown")
    assert "Some prose." in result.text
    assert result.title.startswith("#") or result.title


def test_meta_extraction_ignores_tags_without_content():
    meta = extract_meta('<meta name="description"><meta name="x" content="y">')
    assert meta.get("description", "") == ""


def test_the_excerpt_is_verbatim_not_paraphrased():
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    excerpt = first_sentences(text, limit=2)
    assert excerpt == "First sentence. Second sentence."


# ------------------------------------------------------------------- authority


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.legislation.gov.uk/act", Authority.PRIMARY),
        ("https://eur-lex.europa.eu/legal-content", Authority.PRIMARY),
        ("https://www.india.gov.in/topic", Authority.PRIMARY),
        ("https://example.go.jp/", Authority.PRIMARY),
        ("https://www.oecd.org/report", Authority.REPUTABLE),
        ("https://www.w3.org/TR/", Authority.REPUTABLE),
        ("https://en.wikipedia.org/wiki/DSA", Authority.COMMUNITY),
        ("https://www.reddit.com/r/x", Authority.COMMUNITY),
        ("https://random-blog.example/post", Authority.UNVERIFIED),
    ],
)
def test_authority_is_assigned_on_evidence(url, expected):
    authority, reason = assess_authority(url)
    assert authority is expected, f"{url}: {authority} != {expected}"
    assert reason, "an unexplained authority score is not auditable"


def test_a_lookalike_domain_cannot_borrow_government_authority():
    """Substring matching would let 'not-gov.uk.example' claim gov.uk standing."""
    authority, _ = assess_authority("https://not-gov.uk.example.com/")
    assert authority is not Authority.PRIMARY


def test_an_operator_can_promote_a_domain():
    authority, reason = assess_authority(
        "https://internal.example/report", trusted=("internal.example",)
    )
    assert authority is Authority.PRIMARY
    assert "trusted source list" in reason


def test_a_hostless_url_is_unverified():
    authority, reason = assess_authority("not a url")
    assert authority is Authority.UNVERIFIED
    assert "no host" in reason


@pytest.mark.parametrize(
    "host,expected",
    [
        # Conservative by design: this is only ever used to look a suffix up, and
        # "gov.uk" matches OFFICIAL_SUFFIXES just as well as the fuller form.
        ("www.legislation.gov.uk", "gov.uk"),
        ("eur-lex.europa.eu", "europa.eu"),
        ("example.com", "example.com"),
        ("a.b.c.example.org", "example.org"),
        ("shop.example.co.uk", "example.co.uk"),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


# --------------------------------------------------------- pipeline, end to end


PROSE = (
    "The Digital Services Act sets obligations for intermediary services offered "
    "to users in the European Union. " * 4
)


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "dsa.html").write_text(
        f'<html lang="en"><head><title>DSA overview</title></head><body>'
        f"<h1>Digital Services Act</h1><p>{PROSE}It applies from 17 February 2024.</p>"
        f"</body></html>",
        encoding="utf-8",
    )
    (pages / "thin.html").write_text("<html><body><p>hi</p></body></html>", encoding="utf-8")
    return pages


@pytest.fixture()
def pipeline(tmp_path: Path, source_dir: Path) -> ResearchPipeline:
    return ResearchPipeline(
        KnowledgeBase(tmp_path / "kb.sqlite3"), FileFetcher(source_dir)
    )


STATEMENT = "The DSA applies from 17 February 2024 to intermediary services in the Union."


def test_a_real_source_passes_every_gate_and_is_stored(pipeline):
    record = pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa", jurisdiction="EU")
    assert record.ok, record.note
    assert record.outcome == "stored"
    assert all(stage.passed for stage in record.report.stages)
    assert len(record.report.stages) == 8


def test_the_stored_title_is_the_document_title_not_raw_markup(pipeline):
    """Regression: FileFetcher labelled every file text/plain, so an .html
    document was extracted as plain text and its title became the first line of
    raw markup. Asserting only that a record was 'stored' did not catch it."""
    record = pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    assert record.extracted.title == "DSA overview"
    assert "<html" not in record.extracted.title

    items = pipeline.knowledge.query("dsa")
    assert items[0].source.title == "DSA overview", items[0].source.title


@pytest.mark.parametrize(
    "name,expected",
    [
        ("page.html", "text/html"),
        ("notes.md", "text/markdown"),
        ("data.csv", "text/plain"),
        ("unknown.xyz", "text/plain"),
    ],
)
def test_a_local_file_content_type_comes_from_its_extension(tmp_path, name, expected):
    (tmp_path / name).write_text("body text here", encoding="utf-8")
    assert FileFetcher(tmp_path).fetch(name).content_type == expected


def test_a_user_supplied_file_records_who_vouched_for_it(pipeline):
    record = pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    assert record.authority is Authority.OFFICIAL_SECONDARY
    assert "supplied directly by the user" in record.authority_reason


def test_vouching_for_user_files_can_be_turned_off(tmp_path, source_dir):
    """Opting out means the authority gate does its job and refuses."""
    pipe = ResearchPipeline(
        KnowledgeBase(tmp_path / "kb.sqlite3"),
        FileFetcher(source_dir),
        trust_user_files=False,
    )
    record = pipe.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    assert record.outcome == "rejected"
    assert "authority_check" in record.note


def test_a_page_with_too_little_text_stores_nothing(pipeline):
    record = pipeline.ingest_url("thin.html", statement="Nothing to source here.", topic="dsa")
    assert record.outcome == "thin"
    assert "not enough to source a claim" in record.note


def test_a_duplicate_is_rejected_not_overwritten(pipeline):
    assert pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa").ok
    second = pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    assert second.outcome == "rejected"
    assert "duplicate" in second.note


def test_a_failed_fetch_is_reported_not_silently_dropped(pipeline):
    record = pipeline.ingest_url("missing.html", statement="Anything.", topic="dsa")
    assert record.outcome == "refused"
    assert "no such file" in record.note


def test_answer_only_returns_what_was_stored(pipeline):
    pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    claims = pipeline.answer("When does the DSA apply to intermediary services?")
    assert len(claims) == 1
    assert claims[0].statement == STATEMENT
    assert claims[0].sources and claims[0].sources[0].url.endswith("dsa.html")


def test_an_unrelated_question_returns_nothing(pipeline):
    pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    assert pipeline.answer("What are the capital gains rules in India?") == []
    assert "will not guess" in pipeline.render("What are the capital gains rules in India?")


def test_the_record_serialises(pipeline):
    record = pipeline.ingest_url("dsa.html", statement=STATEMENT, topic="dsa")
    data = record.as_dict()
    assert data["outcome"] == "stored"
    assert data["extracted"]["usable"] is True
    assert len(data["gates"]) == 8


# ------------------------------------------------- HTTP against a local server


class _Handler(BaseHTTPRequestHandler):
    robots = b"User-agent: *\nDisallow: /private/\n"
    pages = {
        "/ok": (200, "text/html", b"<html><body><p>Public page.</p></body></html>"),
        "/secret": (403, "text/html", b"<html><body>no</body></html>"),
        "/binary": (200, "application/octet-stream", b"\x00\x01\x02"),
    }

    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path == "/robots.txt":
            body, ctype, status = self.robots, "text/plain", 200
        elif self.path in self.pages:
            status, ctype, body = self.pages[self.path]
        else:
            status, ctype, body = 404, "text/plain", b"missing"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # keep the test output clean
        pass


class LocalHttpFetcher(HttpFetcher):
    """HttpFetcher with the SSRF guard lifted, for tests only.

    The guard itself is tested directly above; a test server has to be reachable
    at 127.0.0.1, which the guard exists to refuse.
    """

    def _check_host(self, url: str) -> None:  # noqa: ARG002
        return None


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def local() -> LocalHttpFetcher:
    return LocalHttpFetcher(min_interval_s=0.0)


def test_a_normal_page_is_fetched(local, server):
    result = local.fetch(server + "/ok")
    assert result.ok
    assert result.status == 200
    assert "Public page" in result.body


def test_robots_txt_disallow_is_honoured(local, server):
    result = local.fetch(server + "/private/data")
    assert not result.ok
    assert result.robots_disallowed
    assert "honours that" in result.problem


def test_an_access_denial_is_not_worked_around(local, server):
    result = local.fetch(server + "/secret")
    assert not result.ok
    assert result.access_denied
    assert result.status == 403
    assert "will not work around it" in result.problem


def test_a_non_text_content_type_is_not_extracted(local, server):
    result = local.fetch(server + "/binary")
    assert not result.ok
    assert "not text" in result.problem


def test_a_missing_page_is_reported(local, server):
    result = local.fetch(server + "/nothing")
    assert not result.ok
    assert result.status == 404


def test_the_user_agent_identifies_the_bot(local):
    assert "JarvisBot" in local.user_agent


def test_the_rate_limiter_spaces_requests_out(local, server):
    import time

    local.min_interval_s = 0.3
    start = time.monotonic()
    local.fetch(server + "/ok")
    local.fetch(server + "/ok")
    assert time.monotonic() - start >= 0.3


# ------------------------------------------------- every agent must return one


def test_every_agent_returns_a_result_rather_than_none(runtime):
    """Guard against a method being split by an edit and losing its return.

    This happened for real: inserting a helper in front of an anchor that sat
    mid-function orphaned the tail of ResearchAgent.run inside the helper, so
    run() fell off the end and returned None. The supervisor swallowed it and
    reported "no result to verify", which looked like an empty answer rather
    than a broken agent.
    """
    from jarvis.agents.base import AgentRequest, AgentResult

    for name in sorted(runtime.registry.names):
        if name in {"vision", "computer"}:
            continue  # capability-gated; covered by test_host
        request = AgentRequest(intent=name, text="a plain request", params={})
        result = runtime.registry.get(name).run(request)
        assert isinstance(result, AgentResult), f"{name}.run() returned {result!r}"


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    rt = build_runtime(settings_from_env(), with_memory=False)
    yield rt
    rt.shutdown()
