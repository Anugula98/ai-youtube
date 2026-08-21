"""Quality Gate agent (spec section 30).

Unlike the 9 pipeline agents, this isn't a `Stage` in STAGE_ORDER -- it's a
cross-cutting evaluation that reviews everything the pipeline produced (AV
through Publish) and computes the 12-category score the spec calls for. It
runs once the project reaches the end of the pipeline (see
pipeline.compute_quality_gate), and its verdict is what actually determines
READY_TO_PUBLISH vs NEEDS_REVIEW vs RESEARCH_REQUIRED vs DO_NOT_PUBLISH --
spec section 30's four possible outcomes, wired to PipelineState 1:1 except
RESEARCH_REQUIRED and DO_NOT_PUBLISH share the DO_NOT_PUBLISH state (there's
no separate PipelineState for "needs new research" that isn't already
covered by looping back to the RESEARCH stage directly).

Output shape:
{
  "newsworthiness": 0-10, "accuracy": 0-10, "research": 0-10, "originality": 0-10,
  "script": 0-10, "voice": 0-10, "visuals": 0-10, "copyright": 0-10,
  "thumbnail": 0-10, "title": 0-10, "viewer_value": 0-10, "freshness": 0-10,
  "overall_score": 0-120,
  "verdict": "READY_TO_PUBLISH" | "NEEDS_REVIEW" | "RESEARCH_REQUIRED" | "DO_NOT_PUBLISH",
  "reasoning": str,
}
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Project
from .base import LLMClient, get_llm_client

SYSTEM_PROMPT = """You are the Final Quality Gate. Score this project's complete output \
across 12 categories, each 0-10: newsworthiness, accuracy, research (depth/quality of \
sourcing), originality, script (quality of writing), voice (narration quality), visuals \
(storyboard quality), copyright (how clean the risk profile is -- LOW risk assets score \
high), thumbnail, title, viewer_value (does this genuinely help the viewer decide/understand \
something), and freshness (how time-sensitive/current the information still is). Sum for an \
overall_score out of 120. Then assign exactly one verdict: READY_TO_PUBLISH (score is high \
and there are no accuracy or copyright red flags), NEEDS_REVIEW (solid but a human should \
look at specific weak points before it goes out), RESEARCH_REQUIRED (accuracy or freshness is \
the blocking issue -- send it back to re-research), or DO_NOT_PUBLISH (a hard-fail on \
accuracy or copyright regardless of how good everything else is -- never let a high overall \
score paper over an accuracy or copyright failure). Explain the verdict briefly."""


class QualityGateAgent:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_llm_client()

    def run(self, project: Project, context: Dict[str, Any]) -> dict:
        user_prompt = (
            f"Topic: {project.topic}\n"
            f"AV: {context.get('AV')}\n"
            f"Research: {context.get('RESEARCH')}\n"
            f"Fact Check: {context.get('FACT_CHECK')}\n"
            f"Script: {context.get('SCRIPT')}\n"
            f"Voice: {context.get('VOICE')}\n"
            f"Visuals: {context.get('VISUALS')}\n"
            f"Copyright: {context.get('COPYRIGHT')}\n"
            f"Thumbnail: {context.get('THUMBNAIL')}\n"
            f"Publish: {context.get('PUBLISH')}\n"
        )
        return self.llm.complete_json(SYSTEM_PROMPT, user_prompt, stage="QUALITY_GATE")
