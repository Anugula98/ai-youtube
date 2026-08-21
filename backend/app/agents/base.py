"""Agent interface + LLM client abstraction.

Every agent is a pure-ish function: (project, context) -> StageOutput.
Swapping MockLLMClient for AnthropicLLMClient in `get_llm_client()` is the
*only* change needed to move an agent from offline-stub to live-LLM behavior.
"""
from __future__ import annotations
import abc
import json
from typing import Any, Dict, List, Protocol

from ..models import Stage, Project
from ..config import get_settings


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, stage: Stage | str | None = None) -> dict: ...


class MockLLMClient:
    """Deterministic offline stub so the whole pipeline runs with no API key.

    Dispatches on the caller's `Stage` enum value (passed explicitly by
    Agent._complete below), not by sniffing the system prompt text. An
    earlier version matched on substrings in the prompt and broke when one
    agent's own prompt happened to mention another agent's name — keying off
    the enum instead makes that whole class of bug impossible.
    """

    def complete_json(self, system: str, user: str, stage: Stage | str | None = None) -> dict:
        return _MOCKS.get(stage, lambda _u: {})(user)


class AnthropicLLMClient:
    """Real client. Requires ANTHROPIC_API_KEY in the environment.

    Not used by default in this scaffold (see get_llm_client below) because
    this sandbox has no network access to api.anthropic.com — but the code
    is real and ready to run wherever that key + egress are available.
    """

    def __init__(self, model: str | None = None):
        import anthropic  # local import so MockLLMClient works without the dep installed
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set — see .env.example")
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.anthropic_model

    def complete_json(self, system: str, user: str, stage: Stage | None = None) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system + "\n\nRespond with ONLY valid JSON. No markdown fences, no preamble.",
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return json.loads(text)


def get_llm_client() -> LLMClient:
    if get_settings().anthropic_api_key:
        return AnthropicLLMClient()
    return MockLLMClient()


class StageOutput(dict):
    """Marker type — agents return plain dicts, validated by the caller
    against the JSON shape documented in each agent module's docstring."""


class Agent(abc.ABC):
    stage: Stage

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_llm_client()

    def _complete(self, system: str, user: str) -> dict:
        """All concrete agents should call this (not self.llm.complete_json
        directly) so the mock dispatch always has the right stage to key on."""
        return self.llm.complete_json(system, user, stage=self.stage)

    @abc.abstractmethod
    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        ...


# ---------------------------------------------------------------------------
# Mock response generators — deliberately simple, deterministic, and clearly
# labeled as stub content so nobody mistakes this for real research.
# ---------------------------------------------------------------------------

def _mock_av(user_prompt: str) -> dict:
    return {
        "main_angle": "What's genuinely new here, explained without the marketing fluff",
        "alternative_angles": [
            "How this compares to the previous generation",
            "Whether this is actually worth upgrading for",
        ],
        "viewer_promise": "You'll know exactly what changed and whether it matters to you",
        "target_viewer": "Enthusiast upgrader deciding whether to buy",
        "recommended_content_type": "LONG_FORM",
        "recommended_duration_seconds": 480,
        "title_concepts": [
            "Everything New (And What They're Not Telling You)",
            "Is It Actually Worth It?",
            "The Honest Breakdown",
        ],
        "thumbnail_concepts": ["Product hero shot + bold 3-word claim", "Before/after split"],
        "shorts_opportunities": ["The one feature nobody's talking about", "60-second verdict"],
    }


def _mock_research(user_prompt: str) -> dict:
    return {
        "facts": [
            {
                "text": "STUB: replace with real researched fact",
                "claim_type": "spec",
                "source_urls": ["https://example-official-source.com/"],
            }
        ],
        "specifications": {},
        "historical_context": "STUB — offline mock, no real research performed",
        "sources": [
            {
                "url": "https://example-official-source.com/",
                "title": "Official product page (stub)",
                "publisher": "Manufacturer",
                "source_tier": "OFFICIAL",
            }
        ],
        "open_questions": ["What did the mock not verify?"],
    }


def _mock_fact_check(user_prompt: str) -> dict:
    return {
        "claims": [
            {
                "text": "STUB: replace with real researched fact",
                "verification_status": "REPORTED",
                "confidence": 55,
                "notes": "Mock client cannot verify — treat as unverified until a real source check runs",
            }
        ],
        "fact_check_score": 55,
    }


def _mock_script(user_prompt: str) -> dict:
    return {
        "full_script": "STUB SCRIPT — offline mock, no real writing performed.",
        "sections": [{"name": "HOOK", "content": "STUB"}],
        "unverified_claims_used": [],
    }


def _mock_voice(user_prompt: str) -> dict:
    return {
        "narration_script": "STUB — offline mock narration pass.",
        "pronunciation_notes": [],
        "pacing_notes": [],
    }


def _mock_visuals(user_prompt: str) -> dict:
    return {
        "storyboard": [
            {
                "timestamp": "00:00", "narration_excerpt": "STUB",
                "visual": "STUB placeholder shot", "source": "stub",
                "asset_type": "graphic", "copyright_status": "LOW",
                "editing_instruction": "", "on_screen_text": "",
            }
        ]
    }


def _mock_copyright(user_prompt: str) -> dict:
    return {
        "asset_reviews": [
            {"asset": "STUB placeholder shot", "risk": "LOW",
             "reason": "Mock client — no real review performed", "needs_human_review": False}
        ],
        "copyright_score": 100,
        "flags": [],
    }


def _mock_thumbnail(user_prompt: str) -> dict:
    return {
        "thumbnail_concepts": [
            {"description": "STUB concept", "main_object": "product", "text_overlay": "NEW",
             "composition": "centered", "curiosity_mechanism": "STUB"}
        ],
        "title_options": ["STUB Title Option"],
        "recommended_package": {"title": "STUB Title Option", "thumbnail_concept_index": 0},
    }


def _mock_publish(user_prompt: str) -> dict:
    return {
        "title": "STUB Title Option",
        "description": "STUB — offline mock description.",
        "chapters": [],
        "hashtags_useful": False,
        "hashtags": [],
        "search_tags": ["stub", "mock"],
        "pinned_comment": "STUB pinned comment",
        "playlist_recommendation": "",
        "end_screen_recommendation": "",
        "community_post": "",
        "shorts_ideas": [
            {"title": "STUB Short idea 1", "hook": "STUB hook 1", "source_section": "HOOK"},
            {"title": "STUB Short idea 2", "hook": "STUB hook 2", "source_section": "DETAILS"},
        ],
    }


def _mock_quality_gate(user_prompt: str) -> dict:
    # Deliberately mediocre-but-passable scores so the offline demo shows a
    # NEEDS_REVIEW verdict rather than a suspiciously perfect 120/120 —
    # closer to what an honest first pass usually looks like.
    scores = {
        "newsworthiness": 7, "accuracy": 6, "research": 6, "originality": 7,
        "script": 6, "voice": 6, "visuals": 5, "copyright": 8, "thumbnail": 6,
        "title": 6, "viewer_value": 7, "freshness": 7,
    }
    return {
        **scores,
        "overall_score": sum(scores.values()),
        "verdict": "NEEDS_REVIEW",
        "reasoning": "Offline mock scoring — replace with a real LLM pass for genuine evaluation.",
    }


def _mock_comparison(user_prompt: str) -> dict:
    return {
        "table": [{"feature": "STUB feature", "values": {}}],
        "biggest_differences": ["STUB — offline mock, no real comparison performed"],
        "best_for_performance": None,
        "best_for_camera": None,
        "best_battery": None,
        "best_value": None,
        "best_overall": None,
    }


def _mock_discovery(user_prompt: str) -> dict:
    return {
        "ideas": [
            {"topic": "STUB discovered topic 1", "opportunity_score": 70,
             "recommended_content_type": "NEWS", "suggested_title": "STUB Title 1",
             "reasoning": "Offline mock — not a real discovery."},
            {"topic": "STUB discovered topic 2", "opportunity_score": 55,
             "recommended_content_type": "SHORT", "suggested_title": "STUB Title 2",
             "reasoning": "Offline mock — not a real discovery."},
        ]
    }


_MOCKS = {
    Stage.AV: _mock_av,
    Stage.RESEARCH: _mock_research,
    Stage.FACT_CHECK: _mock_fact_check,
    Stage.SCRIPT: _mock_script,
    Stage.VOICE: _mock_voice,
    Stage.VISUALS: _mock_visuals,
    Stage.COPYRIGHT: _mock_copyright,
    Stage.THUMBNAIL: _mock_thumbnail,
    Stage.PUBLISH: _mock_publish,
    "QUALITY_GATE": _mock_quality_gate,
    "COMPARISON": _mock_comparison,
    "DISCOVERY": _mock_discovery,
}
