"""AV — Angle & Validation agent (spec section 8).

Input: the Project's user-supplied fields only (topic, content type, duration,
audience, tone, instructions, URLs, product info) — this is the first stage,
so there's no upstream StageRun output yet.

Output shape (validated in pipeline.py before persisting):
{
  "main_angle": str,
  "alternative_angles": [str, ...],
  "viewer_promise": str,
  "target_viewer": str,
  "recommended_content_type": str,
  "recommended_duration_seconds": int,
  "title_concepts": [str, ...],
  "thumbnail_concepts": [str, ...],
  "shorts_opportunities": [str, ...],
}
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the AV (Angle & Validation) agent in a technology newsroom \
pipeline. Given a topic and content brief, determine the strongest angle for a video: \
analyze demand, competition, newsworthiness, novelty, search potential, CTR potential, \
retention potential, and evergreen value. Output the main angle, 2-3 alternative angles, \
the viewer promise, target viewer, recommended content type and duration, 3-5 title \
concepts, 2-3 thumbnail concepts, and 2-4 Shorts opportunities derivable from this topic."""


class AVAgent(Agent):
    stage = Stage.AV

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        user_prompt = (
            f"Topic: {project.topic}\n"
            f"Content type: {project.content_type}\n"
            f"Target duration (seconds): {project.duration_target_seconds}\n"
            f"Target audience: {project.target_audience}\n"
            f"Tone: {project.tone}\n"
            f"User instructions: {project.user_instructions}\n"
            f"Reference URLs: {project.source_urls}\n"
            f"Product info: {project.product_info}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        return StageOutput(result)
