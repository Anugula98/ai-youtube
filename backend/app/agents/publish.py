"""Publish agent (spec section 19), including the hashtag usefulness logic
(section 18) and Shorts repurposing (section 22).

Input: Script + Thumbnail + Copyright StageRun outputs.

Output shape:
{
  "title": str,                       # the final selected title
  "description": str,
  "chapters": [{"timestamp": str, "label": str}, ...],
  "hashtags_useful": bool,
  "hashtags": [str, ...],             # empty list + hashtags_useful=false if not useful
  "search_tags": [str, ...],          # distinct from hashtags -- SEO/search terms, not #tags
  "pinned_comment": str,
  "playlist_recommendation": str,
  "end_screen_recommendation": str,
  "community_post": str,
  "shorts_ideas": [
    {"title": str, "hook": str, "source_section": str}, ...   # 3-10, per section 22
  ],
}

Spec section 18 is explicit: hashtags are not automatic. The prompt requires the
agent to affirmatively decide whether hashtags help THIS piece of content and to
return an empty list with hashtags_useful=false rather than padding with generic
trending tags when they don't. Hashtags and search_tags are kept as separate
fields on purpose -- the spec calls out that these are commonly (and wrongly)
conflated.
"""
from __future__ import annotations
from typing import Any, Dict

from ..models import Stage, Project
from .base import Agent, StageOutput

SYSTEM_PROMPT = """You are the Publish agent, the final stage before a video goes live. \
Produce: a final title, a description (summary, key info, keywords, source acknowledgment), \
chapters (for content over ~3 minutes), a pinned comment that starts genuine discussion, a \
playlist recommendation, an end-screen "next video" recommendation, and a community post.

On hashtags (do NOT skip this reasoning): decide whether hashtags actually help this specific \
piece of content reach the right audience. If yes, return 3-5 highly relevant hashtags. If \
not, return an empty hashtags list and set hashtags_useful to false -- never pad with generic \
trending hashtags just to chase reach. Hashtags are separate from search_tags: hashtags are \
the small set of #tags shown on the video itself, search_tags are the broader set of internal \
SEO/discovery keywords -- do not merge these two lists.

Also propose 3-10 standalone Shorts derived from this video (a short hook, title, and which \
script section it's drawn from) -- each must work as a piece of content on its own, not just \
as an ad for the full video."""


class PublishAgent(Agent):
    stage = Stage.PUBLISH

    def run(self, project: Project, context: Dict[str, Any]) -> StageOutput:
        script_output = context.get("SCRIPT", {})
        thumbnail_output = context.get("THUMBNAIL", {})
        copyright_output = context.get("COPYRIGHT", {})
        research_output = context.get("RESEARCH", {})

        user_prompt = (
            f"Topic: {project.topic}\n"
            f"Content type: {project.content_type}\n"
            f"Recommended title/thumbnail package: {thumbnail_output.get('recommended_package')}\n"
            f"Script sections: {script_output.get('sections')}\n"
            f"Full script: {script_output.get('full_script')}\n"
            f"Sources (for description credit): {research_output.get('sources')}\n"
            f"Copyright flags to be aware of: {copyright_output.get('flags')}\n"
        )
        result = self._complete(SYSTEM_PROMPT, user_prompt)
        result.setdefault("hashtags_useful", bool(result.get("hashtags")))
        return StageOutput(result)
