"""Copyright agent (spec section 16).

Input: Script + Visuals StageRun outputs.

Output shape:
{
  "asset_reviews": [
    {"asset": str, "risk": "LOW"|"MEDIUM"|"HIGH", "reason": str,
     "needs_human_review": bool},
    ...
  ],
  "copyright_score": 0-100,
  "flags": [str, ...],   # items specifically flagged for human review
}

This is an independent review, deliberately not just trusting the Visuals
agent's own first-pass estimates -- the prompt explicitly tells it to
re-evaluate rather than copy those risk levels forward. Per spec: never
assume attribution/credit alone grants permission, and never guarantee legal
protection (e.g. don't claim something is definitely fair use).
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the Copyright agent -- an independent review, not a rubber stamp \
on the Visuals agent's own estimates. Re-evaluate every asset, quote, and piece of \
third-party content (images, video, screenshots, music, logos, social posts, news footage) \
referenced in the script and storyboard. Classify each as LOW, MEDIUM, or HIGH risk. Never \
assume that crediting a source automatically grants permission to use it. You may note \
potential legal exceptions (e.g. fair use factors) where relevant, but never state that \
something IS definitely protected -- you are not a lawyer and cannot guarantee legal outcomes. \
Flag anything MEDIUM or HIGH for mandatory human review. Compute an overall copyright_score \
from 0-100 reflecting what fraction of assets are LOW risk."""


class CopyrightAgent(Agent):
    stage = Stage.COPYRIGHT

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        script_output = context.get("SCRIPT", {})
        visuals_output = context.get("VISUALS", {})
        user_prompt = (
            f"Storyboard assets: {visuals_output.get('storyboard')}\n"
            f"Script (for quotes/references to review): {script_output.get('full_script')}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        return StageOutput(result)
