"""Product Comparison Engine (spec section 35).

Not one of the 9 pipeline agents -- this is invoked directly via
POST /api/compare with 2+ project IDs, using only the verified
specifications each project's Research stage already produced. Spec is
explicit: "Only use verified specifications" -- this agent is given
specifications only (not raw research facts, not rumors), so it has nothing
unverified to accidentally compare.

Output shape:
{
  "table": [{"feature": str, "values": {project_id: value, ...}}, ...],
  "biggest_differences": [str, ...],
  "best_for_performance": {"project_id": int, "reason": str},
  "best_for_camera": {"project_id": int, "reason": str},
  "best_battery": {"project_id": int, "reason": str},
  "best_value": {"project_id": int, "reason": str},
  "best_overall": {"project_id": int, "reason": str},
}
"""
from __future__ import annotations
from typing import Any, Dict, List

from .base import LLMClient, get_llm_client

SYSTEM_PROMPT = """You are the Product Comparison agent. You are given ONLY verified \
specifications for each product -- never invent or infer a spec that isn't provided. Build \
a feature-by-feature comparison table (one row per feature, one column per product), then \
identify the biggest differences, and name a winner (with a one-line reason) for: \
performance, camera, battery, value, and overall. If a category can't be judged from the \
given specifications (e.g. no camera specs were provided), say so explicitly rather than \
guessing -- do not fabricate a winner for a category you have no data for."""


class ComparisonAgent:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_llm_client()

    def run(self, projects: List[Dict[str, Any]]) -> dict:
        user_prompt = "\n".join(
            f"Project {p['id']} ({p['title']}) specifications: {p['specifications']}"
            for p in projects
        )
        return self.llm.complete_json(SYSTEM_PROMPT, user_prompt, stage="COMPARISON")
