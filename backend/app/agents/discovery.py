"""Topic Discovery agent (spec section 6).

Not one of the 9 pipeline agents -- invoked directly via
POST /api/idea-vault/discover to populate the Idea Vault / Topic Opportunity
Board.

Grounding: if a real SearchProvider is configured (see search_provider.py --
set NEWSAPI_KEY to enable it), this agent fetches real recent headlines for
the category first and feeds them into the LLM prompt, so proposals reflect
what's actually happening now rather than the model's training-time
knowledge. Without a configured provider (the default, and what every
existing test in this repo exercises), it falls back to reasoning from the
LLM's own knowledge alone -- still a real, working agent, just not a
genuinely *live* discovery system until a provider is configured. See
search_provider.py's module docstring for the honest verification status of
the one real provider implementation included here.

Output shape: a list of
{
  "topic": str, "opportunity_score": 0-100, "recommended_content_type": str,
  "suggested_title": str, "reasoning": str,
}
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List

from .base import LLMClient, get_llm_client
from .search_provider import SearchProvider, get_search_provider

logger = logging.getLogger("newsroom.discovery")

SYSTEM_PROMPT = """You are the Topic Discovery agent for a technology newsroom. Given a \
category{grounding_note}, propose {count} distinct, specific, currently-relevant topic ideas \
(not generic evergreen topics -- things with a genuine reason to cover them now: a recent \
announcement, launch, rumor, or trend). For each, score its opportunity from 0-100 \
considering newsworthiness, audience demand, novelty, search opportunity, timeliness, and \
likely viewer usefulness. Recommend the best content type for each (one of: NEWS, \
BREAKING_NEWS, LONG_FORM, SHORT, PRODUCT_REVIEW, UNBOXING, PRODUCT_FEATURES, \
PRODUCT_COMPARISON, BUYING_GUIDE, EXPLAINER, RUMOR_LEAK, SOFTWARE_OS, AI_NEWS) and suggest a \
working title."""

GROUNDED_NOTE = (" and a set of real recent headlines about it (below) -- ground your "
                  "proposals in what these headlines actually report, don't ignore them")
UNGROUNDED_NOTE = ""


class DiscoveryAgent:
    def __init__(self, llm: LLMClient | None = None, search_provider: SearchProvider | None = None):
        self.llm = llm or get_llm_client()
        self.search_provider = search_provider or get_search_provider()

    def run(self, category: str, count: int = 5) -> List[Dict[str, Any]]:
        # A configured search provider is optional grounding, not a hard
        # dependency -- if it fails (network error, bad key, rate limit, any
        # real-world API failure mode), fall back to ungrounded discovery
        # rather than letting the whole endpoint 500. This is the actual
        # degradation path the "optional" framing throughout this module's
        # docstring promises -- it needs to be enforced in code, not just implied.
        try:
            headlines = self.search_provider.search(category, max_results=10)
        except Exception:
            logger.warning("Search provider failed, falling back to ungrounded discovery", exc_info=True)
            headlines = []
        grounded = bool(headlines)

        system_prompt = SYSTEM_PROMPT.format(
            grounding_note=GROUNDED_NOTE if grounded else UNGROUNDED_NOTE, count=count
        )
        user_prompt = f"Category: {category}\nHow many ideas: {count}"
        if grounded:
            user_prompt += "\nRecent headlines:\n" + "\n".join(
                f"- {h.get('title')} ({h.get('published_at', 'date unknown')}): {h.get('snippet', '')}"
                for h in headlines
            )

        result = self.llm.complete_json(system_prompt, user_prompt, stage="DISCOVERY")
        return result.get("ideas", [])
