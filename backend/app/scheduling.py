"""Scheduling engine (spec sections 3, 4, 20, 32).

This computes the *proposed* daily slot layout from a ScheduleConfig --
what section 5's example queue looks like (Shorts every N minutes within the
publishing window, plus the configured number of full-video slots spread
through the day) -- and separately reports which of those slots already have
a project scheduled against them.

What this deliberately does NOT do: run as a background daemon that
auto-creates or auto-publishes content on a timer. There's no task queue/
scheduler process in this scaffold (see DEPLOYMENT.md) -- `/api/calendar`
computes the layout on request. Wiring this into an actual Celery/APScheduler
background job is the natural next step once this logic is in place, and
this module is written so that job would just call generate_daily_slots()
and act on the result, rather than needing new scheduling logic itself.
"""
from __future__ import annotations
import datetime as dt
from typing import List, Dict, Any

from . import models as m


def _parse_hhmm(value: str) -> dt.time:
    h, mi = value.split(":")
    return dt.time(hour=int(h), minute=int(mi))


def generate_daily_slots(config: m.ScheduleConfig, target_date: dt.date) -> List[Dict[str, Any]]:
    """Returns a flat, time-ordered list of proposed slots for one day:
    `shorts_per_day`/`shorts_interval_minutes`-derived Short slots within the
    publishing window, plus `full_videos_per_day` full-video slots spread
    evenly across that same window. Weekday/weekend overrides (JSON dicts
    with the same field names as ScheduleConfig) replace the base config's
    values on the appropriate days -- section 4's "weekday schedule" /
    "weekend schedule" requirement.
    """
    is_weekend = target_date.weekday() >= 5  # Mon=0 ... Sun=6
    overrides = (config.weekend_overrides if is_weekend else config.weekday_overrides) or {}

    window_start = _parse_hhmm(overrides.get("publishing_window_start", config.publishing_window_start))
    window_end = _parse_hhmm(overrides.get("publishing_window_end", config.publishing_window_end))
    shorts_interval = int(overrides.get("shorts_interval_minutes", config.shorts_interval_minutes))
    full_videos_per_day = int(overrides.get("full_videos_per_day", config.full_videos_per_day))

    start_dt = dt.datetime.combine(target_date, window_start)
    end_dt = dt.datetime.combine(target_date, window_end)
    if end_dt <= start_dt:
        return []

    slots: List[Dict[str, Any]] = []

    # Shorts: one every `shorts_interval` minutes across the window.
    if shorts_interval > 0:
        cursor = start_dt
        while cursor < end_dt:
            slots.append({"time": cursor.isoformat(), "content_kind": "SHORT"})
            cursor += dt.timedelta(minutes=shorts_interval)

    # Full videos: spread evenly across the window.
    if full_videos_per_day > 0:
        window_seconds = (end_dt - start_dt).total_seconds()
        step = window_seconds / (full_videos_per_day + 1)
        for i in range(1, full_videos_per_day + 1):
            slot_dt = start_dt + dt.timedelta(seconds=step * i)
            slots.append({"time": slot_dt.isoformat(), "content_kind": "FULL_VIDEO"})

    slots.sort(key=lambda s: s["time"])
    return slots


def annotate_slots_with_projects(slots: List[Dict[str, Any]], projects: List[m.Project]) -> List[Dict[str, Any]]:
    """Best-effort match: for each slot, attach the nearest scheduled project
    of the matching kind (SHORT slot -> ContentType.SHORT project, FULL_VIDEO
    slot -> anything else) within a 15-minute tolerance window. Unmatched
    slots stay open (project: null) -- these are what the UI would let a
    user drag an idea from the Idea Vault onto (section 4's "drag and drop").
    """
    tolerance = dt.timedelta(minutes=15)
    remaining = list(projects)
    for slot in slots:
        slot_dt = dt.datetime.fromisoformat(slot["time"])
        wants_short = slot["content_kind"] == "SHORT"
        best = None
        best_diff = None
        for p in remaining:
            if p.scheduled_publish_at is None:
                continue
            is_short = p.content_type == m.ContentType.SHORT
            if is_short != wants_short:
                continue
            diff = abs(p.scheduled_publish_at - slot_dt)
            if diff <= tolerance and (best_diff is None or diff < best_diff):
                best, best_diff = p, diff
        slot["project"] = (
            {"id": best.id, "title": best.title,
             "pipeline_state": best.pipeline_state.value if best.pipeline_state else None}
            if best else None
        )
        if best:
            remaining.remove(best)
    return slots
