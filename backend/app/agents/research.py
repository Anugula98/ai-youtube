"""Research agent (spec sections 9-10).

Input: Project fields + the current AV StageRun output.

Output shape:
{
  "facts": [{"text": str, "claim_type": str, "source_urls": [str, ...]}, ...],
  "specifications": {..free-form product spec dict, only for product content types..},
  "historical_context": str,
  "sources": [{"url": str, "title": str, "publisher": str, "source_tier": str}, ...],
  "open_questions": [str, ...],
}

The pipeline layer is responsible for turning "sources" into real `Source` rows
and "facts" into a first pass at `Claim` rows (verification_status defaults to
REPORTED until the Fact Check agent runs) — this agent only proposes them.

Product/Unboxing Research Mode (spec section 10): PRODUCT_TYPES gets a
distinctly richer system prompt walking through the section's full checklist
(product basics, design, box/unboxing contents, first look, features,
specifications, real-world value) rather than the one-line mention a
standard-mode prompt gets. If the project has first-party observations
(see main.py's PUT /observations endpoint), those are surfaced explicitly
and the agent is told to treat them as ground truth over any conflicting
manufacturer claim -- this is the actual data path spec section 34 describes.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project, ContentType
from .base import Agent, StageOutput

PRODUCT_TYPES = {ContentType.PRODUCT_REVIEW, ContentType.PRODUCT_FEATURES,
                  ContentType.PRODUCT_COMPARISON, ContentType.BUYING_GUIDE, ContentType.UNBOXING}

STANDARD_SYSTEM_PROMPT = """You are the Research agent in a technology newsroom pipeline. \
Research the given topic using a prioritized source hierarchy: official sources, \
company announcements, product pages, documentation, regulatory info, developer docs, \
reputable tech publications, specialist publications, then community discussion — in \
that order of trust. Collect facts, specifications, dates, prices, features, \
availability, historical context, and audience questions. For every fact, attach the \
specific source URL(s) it came from — never assert a fact without a traceable source."""

PRODUCT_SYSTEM_PROMPT = """You are the Research agent, in Product/Unboxing Research Mode \
(the content type requires an expanded product-intelligence layer). Use the same source \
hierarchy and per-fact sourcing discipline as standard research, but additionally build a \
detailed `specifications` object covering:
- PRODUCT BASICS: name, generation, model number, launch date, price, availability, regions
- DESIGN: dimensions, weight, materials, colors, build, ports, buttons, camera layout, display
- BOX / UNBOXING CONTENTS: what's included (device, charger, cable, manuals, accessories). \
Never assume an accessory is included unless a source explicitly confirms it or the user's \
own first-party observations (below, if provided) state it — mark anything else as NOT_VERIFIED.
- FIRST LOOK: build quality, ergonomics, buttons, ports, display, camera modules, speakers
- FEATURES: new / improved / removed features, AI features, camera/display/battery/performance \
features, connectivity, software, security, accessibility
- SPECIFICATIONS: processor, GPU, RAM, storage, display specs, battery, charging, cameras, \
connectivity, OS, dimensions, weight, sensors, ports
- REAL-WORLD VALUE: for each major spec, explain what it actually means for the user — don't \
just restate the spec sheet.

If the user has supplied first-party observations (hands-on-the-product notes), treat those \
as ground truth and prefer them over any conflicting manufacturer claim or third-party \
report — explicitly note where your research confirms, contradicts, or adds context to what \
the user observed themselves."""


class ResearchAgent(Agent):
    stage = Stage.RESEARCH

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        av_output = context.get("AV", {})
        is_product_mode = project.content_type in PRODUCT_TYPES
        system_prompt = PRODUCT_SYSTEM_PROMPT if is_product_mode else STANDARD_SYSTEM_PROMPT

        product_info = project.product_info or {}
        user_observations = product_info.get("user_observations")

        user_prompt = (
            f"Topic: {project.topic}\n"
            f"Content type: {project.content_type}\n"
            f"Main angle (from AV agent): {av_output.get('main_angle')}\n"
            f"Product info supplied by user: {product_info}\n"
            f"Reference URLs supplied by user: {project.source_urls}\n"
        )
        if is_product_mode and user_observations:
            user_prompt += f"User's first-party hands-on observations (treat as ground truth): {user_observations}\n"

        result = self._complete(system_prompt, user_prompt)
        return StageOutput(result)
