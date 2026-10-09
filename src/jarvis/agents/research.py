"""Research agent (spec section 24).

Research here means: assemble what is known, attach a source and a confidence to
each claim, and say plainly what is *not* known. When no live search provider is
configured, the agent says so instead of inventing findings -- which is the whole
point of the anti-hallucination layer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Authority, Claim, Confidence, Source
from jarvis.core.knowledge import KnowledgeBase
from jarvis.util.clock import now_iso


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    authority: Authority = Authority.UNVERIFIED
    publisher: str = ""
    published: str | None = None


class SearchProvider(Protocol):
    """Anything that can look something up. No provider is bundled by default."""

    name: str

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        ...  # pragma: no cover - protocol


class FunctionSearch:
    """Adapter around a plain ``query -> list[SearchHit]`` callable."""

    def __init__(self, name: str, fn: Callable[[str, int], list[SearchHit]]) -> None:
        self.name = name
        self._fn = fn

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        return list(self._fn(query, limit))


class ResearchAgent(Agent):  # noqa: PLR0912
    name = "research"
    description = "Researches a question, verifies sources and reports confidence honestly."
    capabilities = ("research", "verify", "summarise")

    def __init__(
        self,
        runtime: Any = None,
        *,
        knowledge: KnowledgeBase | None = None,
        provider: SearchProvider | None = None,
        pipeline: Any = None,
    ) -> None:
        super().__init__(runtime)
        self.knowledge = knowledge
        self.provider = provider
        #: A ResearchPipeline (Phase 2). Without one, the agent can only repeat
        #: what is already stored - and it says so rather than implying it
        #: fetched something.
        self.pipeline = pipeline

    #: Schemes the agent will accept as "here is a source".
    _URL_RE = re.compile(r"""\b((?:https?|file)://[^\s<>"')]+)""", re.IGNORECASE)

    def run(self, request: AgentRequest) -> AgentResult:
        query = request.text or request.param("query") or ""
        if not query.strip():
            return AgentResult(agent=self.name, ok=False, summary="No research question supplied.")

        # A URL in the request means "read this", which is a different job from
        # "answer this question". Do the reading first and report what happened.
        url = (request.param("url") or "").strip()
        if not url:
            match = self._URL_RE.search(query)
            if match and request.param("mode") != "query":
                url = match.group(1)
        if url:
            return self._ingest(request, url, query)

        result = AgentResult(agent=self.name, summary=f"Research on: {query}")
        result.say(f"checking the knowledge base for '{query}'")

        known = self.knowledge.query(query, limit=5) if self.knowledge else []
        for item in known:
            result.add_claim(
                Claim(
                    statement=item.statement,
                    confidence=item.confidence,
                    sources=[item.source] if item.source else [],
                    caveats=(
                        [f"status={item.status.value}"]
                        + ([item.rejection] if item.rejection else [])
                    ),
                    time_sensitive=True,
                    last_verified=item.verified_at or item.created_at,
                )
            )

        hits: list[SearchHit] = []
        if self.provider is not None:
            result.say(f"querying {self.provider.name}")
            hits = self.provider.search(query, limit=5)

        for hit in hits:
            result.add_claim(
                Claim(
                    statement=hit.snippet or hit.title,
                    confidence=Confidence.from_score(hit.authority.weight * 0.8),
                    sources=[
                        Source(
                            title=hit.title,
                            url=hit.url,
                            authority=hit.authority,
                            publisher=hit.publisher,
                            published=hit.published,
                            retrieved=now_iso(),
                        )
                    ],
                    time_sensitive=True,
                )
            )

        if not result.claims:
            result.ok = False
            result.summary = (
                f"No verified information on '{query}'. "
                + (
                    "No search provider is configured, so Jarvis will not guess at an answer."
                    if self.provider is None
                    else "The search returned nothing usable."
                )
            )
            if self.pipeline is None:
                result.follow_ups.append(
                    "No fetcher is configured, so Jarvis cannot read a URL you give it either."
                )
            result.add_claim(Claim.unknown(f"Answer to '{query}'", why="no verified source available"))
            result.follow_ups.extend(
                [
                    "Configure a search provider to enable live research.",
                    "Or give me a source URL and I will record it with its authority and date.",
                ]
            )
            return result

        result.summary = (
            f"{len(result.claims)} claim(s) assembled, weakest confidence "
            f"{result.confidence.value}."
        )
        if result.confidence.rank < Confidence.MEDIUM.rank:
            result.follow_ups.append(
                "Confidence is below MEDIUM - treat this as a lead to verify, not as an answer."
            )
        return result

    def _ingest(self, request: AgentRequest, url: str, query: str) -> AgentResult:
        """Read a URL the user pointed at, and report honestly what came of it."""
        if self.pipeline is None:
            result = AgentResult(
                agent=self.name,
                ok=False,
                summary=(
                    f"You gave me {url}, but no fetcher is configured, so Jarvis has not "
                    "read it. It will not describe a page it has not retrieved."
                ),
            )
            result.add_claim(Claim.unknown(f"Contents of {url}", why="no fetcher configured"))
            result.follow_ups.append("Configure a fetcher to enable reading sources.")
            return result

        statement = (request.param("statement") or "").strip() or query.strip()
        statement = self._URL_RE.sub("", statement).strip() or f"Information from {url}"
        record = self.pipeline.ingest_url(
            url,
            statement=statement,
            topic=request.param("topic") or "",
            jurisdiction=request.param("jurisdiction") or "",
        )
        result = AgentResult(agent=self.name, summary=f"Read {url}: {record.outcome}")
        result.data = record.as_dict()
        if record.ok:
            result.add_claim(
                Claim(
                    statement=statement,
                    confidence=Confidence.MEDIUM,
                    sources=[
                        Source(
                            title=record.extracted.title or url,
                            url=url,
                            authority=record.authority,
                            publisher=record.extracted.publisher if record.extracted else "",
                            retrieved=record.retrieved_at,
                        )
                    ],
                    time_sensitive=True,
                    last_verified=record.retrieved_at,
                    reasoning=record.authority_reason,
                )
            )
            result.follow_ups.append(
                f"Authority {record.authority.value}: {record.authority_reason}"
            )
        else:
            result.ok = False
            result.add_claim(
                Claim.unknown(
                    f"Contents of {url}",
                    why=record.note or f"the retrieval {record.outcome}",
                )
            )
            if record.fetch.access_denied:
                result.follow_ups.append(
                    "The site restricted access. Jarvis will not retrieve it another way."
                )
            elif record.fetch.robots_disallowed:
                result.follow_ups.append("robots.txt disallows this path; that was honoured.")
            elif record.outcome == "thin":
                result.follow_ups.append(
                    "Try a page with more substantive text, or supply the document directly."
                )
            else:
                result.follow_ups.append(record.note)
        return result
    def record_source(
        self,
        statement: str,
        *,
        title: str,
        url: str = "",
        authority: Authority = Authority.UNVERIFIED,
        topic: str = "",
        jurisdiction: str = "",
        published: str | None = None,
        verifier: Callable[[Any], bool] | None = None,
    ) -> dict[str, Any]:
        """Push a source through the controlled learning pipeline (spec section 9)."""
        if self.knowledge is None:
            raise RuntimeError("no knowledge base configured")
        from jarvis.core.knowledge import Candidate

        report = self.knowledge.ingest(
            Candidate(
                statement=statement,
                topic=topic,
                jurisdiction=jurisdiction,
                source=Source(
                    title=title, url=url, authority=authority,
                    jurisdiction=jurisdiction, published=published, retrieved=now_iso(),
                ),
                verifier=verifier,
            )
        )
        return report.as_dict()

    def summarise(self, claims: Sequence[Claim]) -> str:
        if not claims:
            return "Nothing verified to summarise."
        weakest = min((c.confidence for c in claims), key=lambda c: c.rank)
        lines = [f"Confidence of this summary: {weakest.value} (weakest of {len(claims)} claims)."]
        lines.extend(f"- {c.render()}" for c in claims)
        return "\n".join(lines)
