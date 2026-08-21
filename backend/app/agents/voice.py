"""Voice agent (spec section 14).

Input: Script StageRun output (full_script).

Output shape:
{
  "narration_script": str,     # the script rewritten for natural speech
  "pronunciation_notes": [{"term": str, "guidance": str}, ...],
  "pacing_notes": [{"section": str, "note": str}, ...],  # pauses, emphasis
}

Spec is explicit that this agent must not introduce new facts -- it only
transforms the Script agent's output for spoken delivery (pronunciation of
product names/acronyms, numbers read naturally, pause placement, emphasis).
The prompt enforces that constraint directly.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the Voice agent. Take the given script and prepare it for \
natural spoken narration. Do NOT introduce any new facts, claims, or information -- your \
only job is to optimize for speech: rewrite awkward written-for-reading phrasing into \
natural spoken sentences, specify pronunciation guidance for product names/acronyms/technical \
terms, mark where numbers should be read out in full versus as digits, and note where pauses \
or emphasis should fall. The underlying meaning and every factual claim must remain \
byte-for-byte equivalent in substance to the input script."""


class VoiceAgent(Agent):
    stage = Stage.VOICE

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        script_output = context.get("SCRIPT", {})
        user_prompt = (
            f"Script to prepare for narration: {script_output.get('full_script')}\n"
            f"Tone: {project.tone}\n"
            f"Language: {project.language}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        return StageOutput(result)
