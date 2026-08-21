"""Fact Check / Source Verification agent (spec section 11).

Input: Project fields + Research StageRun output (facts + sources).

Output shape:
{
  "claims": [
    {"text": str, "verification_status": one of VerificationStatus, "confidence": 0-100,
     "notes": str},
    ...
  ],
  "fact_check_score": 0-100,
}

Gate logic (enforced in pipeline.py, not here): if fact_check_score is below the
project's configured threshold, the pipeline blocks advancement and loops back
to RESEARCH rather than silently continuing — "STOP -> RESEARCH" per spec section 11.
This agent's job is only to score and classify, never to decide whether to block.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the Fact Check / Source Verification agent. You work \
independently from the Research agent — treat its output as a set of claims to \
scrutinize, not as ground truth. For every claim, classify it as one of: VERIFIED, \
PARTIALLY_VERIFIED, REPORTED, RUMOR, CONTRADICTED, UNSUPPORTED, FALSE. Check \
specifications, prices, dates, availability, company statements, quotes, and \
performance/benchmark claims against the cited sources. Do not upgrade a claim's \
status beyond what its cited sources actually support. Compute an overall \
fact_check_score from 0-100 reflecting what fraction of load-bearing claims are \
VERIFIED or PARTIALLY_VERIFIED versus RUMOR/CONTRADICTED/UNSUPPORTED/FALSE."""


class FactCheckAgent(Agent):
    stage = Stage.FACT_CHECK

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        research_output = context.get("RESEARCH", {})
        user_prompt = (
            f"Topic: {project.topic}\n"
            f"Facts from Research agent: {research_output.get('facts')}\n"
            f"Sources from Research agent: {research_output.get('sources')}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        return StageOutput(result)
