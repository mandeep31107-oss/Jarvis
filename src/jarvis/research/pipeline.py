"""Fetch → extract → verify → store (Phase 2, spec section 25).

This is the controlled learning pipeline the brief describes: a page is not
"knowledge" because Jarvis read it. It becomes knowledge only after it passes
the knowledge base's gates, and it is stored with the source, the retrieval
date and a confidence that reflects how much was actually established.

Two rules that shape the code:

* **Authority is never inflated.** The default is ``UNVERIFIED``. A domain is
  only promoted on evidence (a government or official-domain suffix, or an
  explicit operator-supplied list), and the promotion is recorded as a
  heuristic in the reasoning, not as a fact.
* **A fetch failure is a finding, not a silence.** A refused fetch, an access
  denial, a robots disallow and a page with too little prose all produce an
  explicit outcome the user can read. The alternative - quietly storing nothing
  - is how an agent ends up looking confident about something it never read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.confidence import Authority, Claim, Source
from jarvis.core.knowledge import Candidate, IngestReport, KnowledgeBase
from jarvis.research.extract import Extracted, extract
from jarvis.research.fetch import Fetcher, FetchResult, NullFetcher
from jarvis.util.clock import now_iso

#: Domain suffixes whose content is primary for its own jurisdiction. Matched on
#: the registrable suffix, never on a substring, so ``evil-gov.example`` cannot
#: borrow the authority of ``gov.uk``.
OFFICIAL_SUFFIXES: dict[str, str] = {
    "gov.uk": "UK",
    "gov.in": "IN",
    "gov.au": "AU",
    "gov.sg": "SG",
    "go.jp": "JP",
    "go.kr": "KR",
    "gov": "US",
    "europa.eu": "EU",
    "int": "",
}

#: Second-tier official and standards bodies. Still not primary law, but
#: authoritative about their own subject.
REPUTABLE_DOMAINS = frozenset(
    {
        "oecd.org", "imf.org", "worldbank.org", "who.int", "wto.org", "un.org",
        "iso.org", "w3.org", "ietf.org", "rfc-editor.org", "python.org",
        "github.com", "stripe.com", "ec.europa.eu", "ico.org.uk", "ftc.gov",
    }
)

#: Domains that are useful but are user-generated: treat as community unless the
#: operator says otherwise.
COMMUNITY_DOMAINS = frozenset(
    {
        "reddit.com", "news.ycombinator.com", "stackoverflow.com", "quora.com",
        "medium.com", "wikipedia.org", "youtube.com",
    }
)


def registrable_domain(host: str) -> str:
    """The last two labels, or three for known compound suffixes.

    Deliberately simple and conservative: it is only ever used to *look up* a
    suffix, and a wrong guess lands on the default (unverified), never on a
    higher authority.
    """
    host = (host or "").lower().strip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-3:]) in OFFICIAL_SUFFIXES or ".".join(labels[-2:]) in (
        "co.uk", "org.uk", "ac.uk", "com.au", "co.in", "co.jp", "com.br"
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def assess_authority(
    url: str,
    *,
    publisher: str = "",
    trusted: tuple[str, ...] = (),
) -> tuple[Authority, str]:
    """Decide how much weight a source deserves, and say why.

    Returns the authority plus a one-line reason, because an unexplained
    authority score is not auditable.
    """
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return Authority.UNVERIFIED, "no host in the URL"

    domain = registrable_domain(host)
    trusted_set = {registrable_domain(t) for t in trusted if t}

    if domain in trusted_set or host in {t.lower() for t in trusted}:
        return Authority.PRIMARY, f"{host} is on the operator's trusted source list"

    if domain in OFFICIAL_SUFFIXES:
        jurisdiction = OFFICIAL_SUFFIXES[domain]
        where = f" for {jurisdiction}" if jurisdiction else ""
        return Authority.PRIMARY, f"{domain} is an official government domain{where}"

    if domain in REPUTABLE_DOMAINS:
        return Authority.REPUTABLE, f"{domain} is an established standards or international body"

    if domain in COMMUNITY_DOMAINS or host.endswith(".wikipedia.org"):
        return (
            Authority.COMMUNITY,
            f"{domain} is user-generated; useful for orientation, not for authority",
        )

    if publisher:
        return Authority.COMMUNITY, f"no basis to rate {publisher} above community"
    return Authority.UNVERIFIED, f"nothing establishes the standing of {host}"


@dataclass
class RetrievalRecord:
    """One attempt to read a URL and what came of it."""

    url: str
    outcome: str  # stored | rejected | refused | thin
    fetch: FetchResult
    extracted: Extracted | None = None
    authority: Authority = Authority.UNVERIFIED
    authority_reason: str = ""
    report: IngestReport | None = None
    note: str = ""
    retrieved_at: str = field(default_factory=now_iso)

    @property
    def ok(self) -> bool:
        return self.outcome == "stored"

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "outcome": self.outcome,
            "note": self.note,
            "authority": self.authority.value,
            "authority_reason": self.authority_reason,
            "fetch": self.fetch.as_dict(),
            "extracted": self.extracted.as_dict() if self.extracted else None,
            "gates": (
                [
                    {"stage": s.stage, "passed": s.passed, "reason": s.reason}
                    for s in (self.report.stages if self.report else [])
                ]
                if self.report
                else []
            ),
            "retrieved_at": self.retrieved_at,
        }


class ResearchPipeline:
    """Fetches a source, checks it, and stores only what survives the gates."""

    #: Below this many words a page is not summarised - it is reported as thin.
    MIN_WORDS = 40

    def __init__(
        self,
        knowledge: KnowledgeBase,
        fetcher: Fetcher | None = None,
        *,
        trusted_domains: tuple[str, ...] = (),
        trust_user_files: bool = True,
    ) -> None:
        self.knowledge = knowledge
        self.fetcher = fetcher or NullFetcher()
        self.trusted_domains = tuple(trusted_domains)
        #: A document the user hands over is vouched for by the user. That is a
        #: real basis for authority, so it is recorded as one - explicitly, and
        #: never extended to anything fetched over the network. Fetching a page
        #: does not make anyone vouch for it.
        self.trust_user_files = trust_user_files

    # ------------------------------------------------------------------ ingest
    def ingest_url(
        self,
        url: str,
        *,
        statement: str,
        topic: str = "",
        jurisdiction: str = "",
        effective: str | None = None,
    ) -> RetrievalRecord:
        """Read ``url`` and try to store ``statement`` sourced from it.

        ``statement`` is what the *user or operator* asserts the page supports.
        Jarvis does not decide for itself what a page means and then store that
        as fact; it stores the claim that was made, with the source attached, so
        a reader can check it.
        """
        fetched = self.fetcher.fetch(url)
        if not fetched.ok:
            outcome = "refused" if (fetched.access_denied or fetched.robots_disallowed) else "refused"
            note = fetched.problem or "the fetch did not succeed"
            if fetched.access_denied:
                note = (
                    f"{note} This is an access restriction. Jarvis will not retrieve "
                    "the page another way."
                )
            elif fetched.robots_disallowed:
                note = f"{note} robots.txt was honoured."
            return RetrievalRecord(
                url=url, outcome=outcome, fetch=fetched, note=note
            )

        extracted = extract(fetched.body, content_type=fetched.content_type)
        final = fetched.final_url or url
        authority, reason = assess_authority(
            final, publisher=extracted.publisher, trusted=self.trusted_domains
        )
        if (
            authority is Authority.UNVERIFIED
            and self.trust_user_files
            and final.startswith("file://")
        ):
            authority = Authority.OFFICIAL_SECONDARY
            reason = (
                "supplied directly by the user; the user is the authority for this "
                "document, which is not the same as it being independently verified"
            )

        if not extracted.usable:
            return RetrievalRecord(
                url=url,
                outcome="thin",
                fetch=fetched,
                extracted=extracted,
                authority=authority,
                authority_reason=reason,
                note=(
                    f"the page yielded only {extracted.word_count} word(s) of readable text. "
                    "That is not enough to source a claim, so nothing was stored."
                ),
            )

        source = Source(
            title=extracted.title or url,
            url=fetched.final_url or url,
            authority=authority,
            jurisdiction=jurisdiction,
            published=effective,
            retrieved=fetched.retrieved_at,
            publisher=extracted.publisher,
        )
        candidate = Candidate(
            statement=statement,
            topic=topic or _topic_from(statement),
            jurisdiction=jurisdiction,
            source=source,
            effective=effective or fetched.retrieved_at,
        )
        report = self.knowledge.ingest(candidate)
        if report.accepted:
            return RetrievalRecord(
                url=url,
                outcome="stored",
                fetch=fetched,
                extracted=extracted,
                authority=authority,
                authority_reason=reason,
                report=report,
                note=f"stored with authority {authority.value}: {reason}",
            )
        return RetrievalRecord(
            url=url,
            outcome="rejected",
            fetch=fetched,
            extracted=extracted,
            authority=authority,
            authority_reason=reason,
            report=report,
            note=f"the knowledge base rejected it at the "
            f"{report.stages[-1].stage if report.stages else 'unknown'} gate: {report.reason}",
        )

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, *, limit: int = 4, topic: str = "") -> list[Claim]:
        """Answer only from what is in the knowledge base.

        No match returns an empty list. Returning a guess here would defeat the
        entire point of the gates upstream.
        """
        items = self.knowledge.query(topic, limit=200)
        scored = sorted(
            ((self._relevance(question, item), item) for item in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        items = [item for score, item in scored[:limit] if score > 0]
        claims: list[Claim] = []
        for item in items:
            sources = [item.source] if item.source is not None else []
            claims.append(
                Claim(
                    statement=item.statement,
                    confidence=item.confidence,
                    sources=sources,
                    caveats=_caveats_for(item.source),
                )
            )
        return claims

    @staticmethod
    def _relevance(question: str, item: Any) -> int:
        """Keyword overlap between the question and the stored item.

        Plain and deliberately unsophisticated: a wrong score only reorders
        what is returned, it never adds a claim that is not in the store.
        """
        words = {w for w in re.findall(r"[a-z]{3,}", question.lower()) if w not in _STOP}
        haystack = " ".join(
            (item.statement, item.summary, item.topic, item.key)
        ).lower()
        return sum(1 for word in words if word in haystack)

    def render(self, question: str, *, limit: int = 4, topic: str = "") -> str:
        claims = self.answer(question, limit=limit, topic=topic)
        if not claims:
            return (
                f"Nothing in the knowledge base answers {question!r}. Jarvis will not guess.\n"
                "Give it a source URL (see ingest_url) or configure a search provider."
            )
        lines = [f"What Jarvis can source for: {question}"]
        for claim in claims:
            lines.append(
                f"  - {claim.statement}\n"
                f"    confidence: {claim.confidence.value}"
                + (f" | source: {claim.sources[0].url}" if claim.sources else "")
            )
            for caveat in claim.caveats:
                lines.append(f"    caveat: {caveat}")
        return "\n".join(lines)


#: Words that appear in nearly every question and so carry no signal.
_STOP = frozenset(
    {
        "the", "and", "for", "that", "this", "with", "what", "when", "where",
        "which", "who", "how", "does", "is", "are", "was", "were", "can",
        "will", "about", "from", "into", "have", "has", "not", "you", "your",
        "jarvis", "please", "tell", "know",
    }
)


def _topic_from(statement: str) -> str:
    words = re.findall(r"[a-z]{4,}", statement.lower())
    return " ".join(words[:3]) if words else "general"


def _caveats_for(source: Source | None) -> list[str]:
    caveats: list[str] = []
    if source is None:
        return ["no source recorded"]
    if source.authority is Authority.UNVERIFIED:
        caveats.append("the standing of this source has not been established")
    elif source.authority is Authority.COMMUNITY:
        caveats.append("this source is user-generated; verify against a primary source")
    if source.is_stale():
        caveats.append("the source is past its verification window and needs re-checking")
    return caveats


__all__ = [
    "COMMUNITY_DOMAINS",
    "OFFICIAL_SUFFIXES",
    "REPUTABLE_DOMAINS",
    "ResearchPipeline",
    "RetrievalRecord",
    "assess_authority",
    "registrable_domain",
]
