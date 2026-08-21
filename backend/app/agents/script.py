"""Script agent (spec sections 12-13).

Input: Project fields + AV + Research + Fact Check StageRun outputs.

Output shape:
{
  "full_script": str,               # the complete narration-ready script
  "sections": [{"name": str, "content": str}, ...],   # per structural beat
  "unverified_claims_used": [str, ...],  # claims below VERIFIED that still made it in, and how they're hedged
  "note": str (optional),
}

Structure picked by content_type per spec:
  - UNBOXING           -> Hook/Box/What's Inside/First Look/Build/Key Features/Setup/
                           First Impressions/Important Details/Verdict (section 13)
  - PRODUCT_* / REVIEW  -> Hook/Product Intro/Unboxing/Design/Features/Specifications/
                           Real-World Experience/Pros-Cons/Verdict (section 12, product variant)
  - SHORT               -> Hook/Context/Key Information/Payoff (section 12, Shorts variant)
  - everything else     -> Hook/Context/Story/Details/Evidence/Analysis/Why It Matters/
                           What's Next/Conclusion (section 12, full-video variant)

The agent is instructed never to assert a claim more confidently than its Fact Check
verification_status supports, and for UNBOXING specifically to never claim the AI
physically handled the product — spec section 13's "Observed vs Expected/specification"
distinction is enforced by prompt instruction, not just left implicit.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project, ContentType
from .base import Agent, StageOutput

FULL_VIDEO_STRUCTURE = ["HOOK", "CONTEXT", "STORY", "DETAILS", "EVIDENCE", "ANALYSIS",
                         "WHY_IT_MATTERS", "WHATS_NEXT", "CONCLUSION"]
PRODUCT_STRUCTURE = ["HOOK", "PRODUCT_INTRO", "UNBOXING", "DESIGN", "FEATURES",
                      "SPECIFICATIONS", "REAL_WORLD_EXPERIENCE", "PROS_CONS", "VERDICT"]
SHORT_STRUCTURE = ["HOOK", "CONTEXT", "KEY_INFORMATION", "PAYOFF"]
UNBOXING_STRUCTURE = ["HOOK", "BOX", "WHATS_INSIDE", "FIRST_LOOK", "BUILD", "KEY_FEATURES",
                       "SETUP", "FIRST_IMPRESSIONS", "IMPORTANT_DETAILS", "VERDICT"]

PRODUCT_TYPES = {ContentType.PRODUCT_REVIEW, ContentType.PRODUCT_FEATURES,
                  ContentType.PRODUCT_COMPARISON, ContentType.BUYING_GUIDE}


def _structure_for(content_type: ContentType) -> list[str]:
    if content_type == ContentType.UNBOXING:
        return UNBOXING_STRUCTURE
    if content_type == ContentType.SHORT:
        return SHORT_STRUCTURE
    if content_type in PRODUCT_TYPES:
        return PRODUCT_STRUCTURE
    return FULL_VIDEO_STRUCTURE


SYSTEM_PROMPT_TEMPLATE = """You are the Script agent in a technology newsroom pipeline. \
Write an original, narration-ready script following this exact section structure: {structure}. \
Never copy phrasing from source articles or imitate competitor channels -- synthesize in your \
own words. Ground every factual statement in the Fact Check agent's claims: a claim's \
verification_status controls how confidently you may state it -- VERIFIED and \
PARTIALLY_VERIFIED claims can be stated directly; REPORTED claims must be hedged \
("according to X"); RUMOR claims must be explicitly flagged as unconfirmed; never state a \
CONTRADICTED, UNSUPPORTED, or FALSE claim as fact. {mode_specific}"""

UNBOXING_MODE_INSTRUCTIONS = (
    "This is an UNBOXING script. Never write as though the AI physically opened, held, or "
    "tested the product. Every physical-world claim (box contents, build quality, weight, "
    "first impressions) must be clearly attributed as either the user's own first-party "
    "observation (if supplied in product_info) or as manufacturer-reported / expected "
    "specification -- explicitly distinguish 'Observed' from 'Expected/specification' per "
    "spec section 13. Anything not explicitly confirmed as included must be marked NOT "
    "VERIFIED rather than assumed present."
)
DEFAULT_MODE_INSTRUCTIONS = "Avoid generic, clickbait-y introductions -- open on the specific angle from the AV agent."


class ScriptAgent(Agent):
    stage = Stage.SCRIPT

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        structure = _structure_for(project.content_type)
        mode_instructions = (
            UNBOXING_MODE_INSTRUCTIONS if project.content_type == ContentType.UNBOXING
            else DEFAULT_MODE_INSTRUCTIONS
        )
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            structure=" -> ".join(structure), mode_specific=mode_instructions
        )

        av_output = context.get("AV", {})
        research_output = context.get("RESEARCH", {})
        fact_check_output = context.get("FACT_CHECK", {})

        user_prompt = (
            f"Topic: {project.topic}\n"
            f"Content type: {project.content_type}\n"
            f"Target duration (seconds): {project.duration_target_seconds}\n"
            f"Tone: {project.tone}\n"
            f"Main angle (AV): {av_output.get('main_angle')}\n"
            f"Viewer promise (AV): {av_output.get('viewer_promise')}\n"
            f"Research facts: {research_output.get('facts')}\n"
            f"Specifications: {research_output.get('specifications')}\n"
            f"Fact-checked claims: {fact_check_output.get('claims')}\n"
            f"User instructions: {project.user_instructions}\n"
            f"Product info (first-party, if supplied): {project.product_info}\n"
        )
        result = self._complete(system_prompt, user_prompt)
        result.setdefault("sections", [{"name": s, "content": ""} for s in structure])
        return StageOutput(result)
