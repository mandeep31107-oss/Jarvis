"""AI provider adapters and the features built on them."""

from .base import AIError, ImageAdapter, LLMAdapter, ParsedCommand, ProviderInfo, VisionAdapter
from .features import (analyse_image, answer_question, before_after, budget_commentary,
                       build_facts, design_suggestions, explain_concepts, explain_design,
                       generate_image, style_advice)
from .registry import get_image_provider, get_llm, get_vision, provider_infos

__all__ = ["AIError", "LLMAdapter", "VisionAdapter", "ImageAdapter", "ProviderInfo",
           "ParsedCommand", "get_llm", "get_vision", "get_image_provider", "provider_infos",
           "build_facts", "explain_design", "explain_concepts", "answer_question",
           "style_advice", "budget_commentary", "analyse_image", "generate_image",
           "design_suggestions", "before_after"]
