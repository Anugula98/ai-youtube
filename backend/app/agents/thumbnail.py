"""Thumbnail agent (spec section 17).

Input: AV StageRun output (title/thumbnail concepts) + Script (for accuracy check).

Output shape:
{
  "thumbnail_concepts": [
    {"description": str, "main_object": str, "text_overlay": str (1-5 words),
     "composition": str, "curiosity_mechanism": str},
    ... (3-5 of these)
  ],
  "title_options": [str, ...],   # 5-10
  "recommended_package": {"title": str, "thumbnail_concept_index": int}
}

Spec is explicit: thumbnail text should normally be 1-5 words, and thumbnails/titles
must not be misleading relative to what the video actually delivers -- the prompt
requires the agent to check candidate titles/thumbnails against the actual script
content before finalizing the recommended package, not just against the original
angle (which could have drifted during scripting).
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the Thumbnail agent. Generate 3-5 thumbnail concepts (main \
object/subject, whether a face adds value, background, emotion conveyed, 1-5 word text \
overlay, composition, and the curiosity mechanism that earns a click) and 5-10 title \
options. Critically: check every title and thumbnail concept against what the actual final \
script delivers, not just the original angle -- a title promising something the script \
doesn't cover is misleading and must not be produced, even if it would perform well. Select \
the single best title + thumbnail combination as the recommended package."""


class ThumbnailAgent(Agent):
    stage = Stage.THUMBNAIL

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        av_output = context.get("AV", {})
        script_output = context.get("SCRIPT", {})
        user_prompt = (
            f"Original title concepts (AV): {av_output.get('title_concepts')}\n"
            f"Original thumbnail concepts (AV): {av_output.get('thumbnail_concepts')}\n"
            f"Viewer promise (AV): {av_output.get('viewer_promise')}\n"
            f"What the final script actually delivers: {script_output.get('full_script')}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        return StageOutput(result)
