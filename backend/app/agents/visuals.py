"""Visuals agent (spec section 15).

Input: Script + Research StageRun outputs.

Output shape:
{
  "storyboard": [
    {"timestamp": str, "narration_excerpt": str, "visual": str, "source": str,
     "asset_type": str, "copyright_status": "LOW"|"MEDIUM"|"HIGH",
     "editing_instruction": str, "on_screen_text": str},
    ...
  ]
}

For product content types, the prompt steers toward the specific shot list from
spec section 15 (beauty shots, box shots, unboxing sequence, feature close-ups,
port demos, UI demos, camera demos, spec graphics, comparison charts, exploded
diagrams). copyright_status here is Visuals' own first-pass estimate per asset;
the Copyright agent (next stage) does the independent, authoritative review.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project, ContentType
from .base import Agent, StageOutput

PRODUCT_TYPES = {ContentType.PRODUCT_REVIEW, ContentType.PRODUCT_FEATURES,
                  ContentType.PRODUCT_COMPARISON, ContentType.BUYING_GUIDE, ContentType.UNBOXING}

SYSTEM_PROMPT = """You are the Visuals agent. Build a complete shot-by-shot storyboard for \
the given script. For every beat of the script, specify: a timestamp, the narration excerpt \
it covers, a description of the visual, where the asset should come from (source), the asset \
type (product beauty shot / box shot / feature close-up / UI demo / graphic / stock / \
generated), a first-pass copyright risk estimate (LOW/MEDIUM/HIGH -- e.g. an official product \
photo from the manufacturer's press kit is typically LOW, a screenshot of a competitor's \
video is typically HIGH), any editing instruction, and any on-screen text overlay. {mode_hint}"""

PRODUCT_HINT = ("This is product content -- prioritize product beauty shots, box/unboxing "
                "sequence shots, feature close-ups, port/button demonstrations, UI "
                "demonstrations, specification graphics, and exploded diagrams where useful.")
DEFAULT_HINT = "Prioritize visuals that make abstract claims concrete (charts, comparisons, on-screen callouts)."


class VisualsAgent(Agent):
    stage = Stage.VISUALS

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        hint = PRODUCT_HINT if project.content_type in PRODUCT_TYPES else DEFAULT_HINT
        system_prompt = SYSTEM_PROMPT.format(mode_hint=hint)

        script_output = context.get("SCRIPT", {})
        research_output = context.get("RESEARCH", {})
        user_prompt = (
            f"Script sections: {script_output.get('sections')}\n"
            f"Full script: {script_output.get('full_script')}\n"
            f"Available source material (from Research): {research_output.get('sources')}\n"
        )
        result = self._complete(system_prompt, user_prompt)
        return StageOutput(result)
