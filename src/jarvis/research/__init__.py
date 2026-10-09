"""Live research: fetch a source, verify it, store only what survives the gates.

Phase 2. Nothing here invents content - a page Jarvis has not retrieved is a
page Jarvis has nothing to say about.
"""

from jarvis.research.extract import Extracted, extract, extract_meta, first_sentences
from jarvis.research.fetch import (
    Fetcher,
    FetchRefused,
    FetchResult,
    FileFetcher,
    HttpFetcher,
    NullFetcher,
)
from jarvis.research.pipeline import ResearchPipeline, RetrievalRecord, assess_authority

__all__ = [
    "Extracted",
    "Fetcher",
    "FetchRefused",
    "FetchResult",
    "FileFetcher",
    "HttpFetcher",
    "NullFetcher",
    "ResearchPipeline",
    "RetrievalRecord",
    "assess_authority",
    "extract",
    "extract_meta",
    "first_sentences",
]
