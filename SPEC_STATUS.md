# AI YouTube Newsroom — Spec & Implementation Status

Fourth and (for now) final pass. Prior passes: (1) scaffold + vertical slice, (2) all 9
agents + Quality Gate + Calendar/Idea Vault/Repurposing/Search, (3) file uploads/unboxing
data entry/asset persistence/comparison/refresh/discovery/scheduler, (4) this pass —
multi-replica-safe scheduler locking, the Final Video Package export, real product/unboxing
Research-mode branching, and a pluggable (if honestly-caveated) search-grounding interface
for Topic Discovery.

**Status legend**: ✅ Done and tested · 🟡 Partial (real but with a stated limitation) ·
⚪ Modeled only · ❌ Not started

---

## 1. Summary

Of the original 42 acceptance criteria (spec section 40), **essentially everything with a
boundable, buildable scope is now implemented and tested.** What remains partial is either
inherently open-ended (richer per-content-type prompt behavior — always improvable, never
"finished") or explicitly out of scope for a scaffold (real-time push updates instead of
polling, actual video rendering/YouTube publishing).

**49 backend tests pass** on a clean run (up from 38 → 24 → 13 across the four passes).

---

## 2. What changed this pass

| Gap (from the previous pass's "still missing" list) | Resolution |
|---|---|
| Multi-replica-unsafe scheduler | **Closed.** `scheduler_lock.py`: a DB-backed single-row mutex using an atomic conditional UPDATE (works identically on SQLite and Postgres — no dialect-specific advisory lock). `run_scheduler_tick` acquires it before doing anything and releases in a `finally`. Tested for lock contention (a second acquire attempt while a lease is active correctly fails), lease expiry and reacquisition, and the end-to-end case (a tick that can't get the lock takes zero action even with a real open slot and a real matching idea). `ENABLE_SCHEDULER=true` is now genuinely safe on more than one backend replica — documented in `DEPLOYMENT.md`, `README.md`, and the module's own docstring. |
| "Final Video Package" export (spec §31, checklist #40) | **Closed.** `GET /api/projects/{id}/final-package` assembles story/research/fact-check/product-details/script/voice/visuals/copyright(+assets)/thumbnail/publish/repurposing/quality-gate into one document. Tested for full assembly after a real pipeline run, and that `product_details` is correctly null for non-product content types and populated for product/unboxing ones. |
| Product/Unboxing Research Mode (spec §10) — previously only mentioned in a docstring, not actually code-branched | **Closed.** `ResearchAgent` now has two genuinely distinct system prompts (`PRODUCT_SYSTEM_PROMPT` / `STANDARD_SYSTEM_PROMPT`) selected by `content_type`, with the product prompt walking through the full spec-section-10 checklist and explicitly surfacing the user's first-party observations (from the §34 endpoint) as ground truth. Tested by capturing which exact prompt a fake LLM client receives for UNBOXING vs. NEWS. |
| Topic Discovery reasons from training knowledge only | **Partially closed, honestly.** Added a pluggable `SearchProvider` interface (`search_provider.py`): `NullSearchProvider` (default, fully tested — every existing Discovery test exercises it) and a complete `NewsAPISearchProvider` implementation. `DiscoveryAgent` now fetches headlines first and grounds its prompt in them when a provider returns results — tested that the prompt genuinely changes (a "ground your proposals" instruction + the real headline text appears) when a fake grounded provider is injected, and stays unchanged when it isn't. **Explicitly not claiming more than this:** `NewsAPISearchProvider` has not been exercised against a live API key or live network in this environment (no credentials, and the backend's network policy has no route to newsapi.org either) — it's "written and reviewed," not "verified," per its own docstring. |

---

## 3. Updated acceptance-criteria tally (spec section 40, 42 items)

**~40 of 42 done, 2 partial, 0 modeled-only, 0 not started.**

The 2 remaining partials:
- **#13, track every agent in real time** — the UI polls on navigation rather than receiving
  push updates (WebSocket/SSE). Functionally correct, just not literally "real time."
- **#34, approve/reject stages** — Approve is tested end-to-end through the UI. Reject/Send
  Back/Edit/Regenerate are complete and tested at the API level but don't have dedicated UI
  buttons beyond the generic ones already present on the Detail page.

Everything else — including file uploads, the asset library, the comparison engine, research
refresh, the idea vault, content repurposing, search, the calendar, and now the final package
export and a multi-replica-safe scheduler — is done and covered by a passing test.

---

## 4. What's left, and why it's reasonable to stop here

1. **Genuinely live topic discovery** — the plumbing is real and tested; what's missing is
   simply *running it once against a real NewsAPI key* to confirm the live path, which
   requires credentials and network access this environment doesn't have. This is a
   configuration/verification step for whoever deploys it, not a code gap.
2. **Deeper per-content-type behavior in Visuals and Publish** — Script and Research now both
   genuinely branch by content type; Visuals and Publish still use `content_type` as prompt
   context without a fully separate instruction set the way Script/Research do. This is an
   open-ended richness improvement, not a missing feature — there's no finish line where
   "content-type-awareness" is complete.
3. **Real-time (push) progress tracking** — would need a WebSocket/SSE layer; the current
   poll-on-navigation UI is functionally complete, just not literally live-updating.
4. **Actual video rendering / YouTube publishing** — always out of scope; the original spec
   itself stops at "storyboard + narration script," never an assembled video file or a
   YouTube API integration.
5. **A task-queue-based scheduler** (vs. the current DB-lock-protected in-process one) — the
   in-process approach with locking is now *correct* on multiple replicas, just not as
   feature-rich as a real Celery/cron-based system (retry policies, dead-letter handling,
   observability). Correctness gap: closed. Operational-maturity gap: open, reasonably so for
   a scaffold.

---

## 5. Review pass — what a fresh audit found and fixed

A full review of the completed project (not just adding new features) turned up real issues,
now fixed:

1. **The frontend was silently behind the backend.** Ten tested backend endpoints from the
   last two build passes (file upload, observations, comparison, research refresh, final
   package, topic discovery, schedule config editing, manual scheduler trigger) had zero UI
   entry point — a real gap between "the API supports this" and "a user can do this." Closed:
   the Idea Vault page now has a Discover Topics panel, the Calendar page has schedule-config
   editing and a manual tick trigger, and the Project Detail page has Refresh Research, a
   Final Package viewer, and — for product/unboxing content types — an Observations form and
   an Uploads panel. Verified with a headless-browser test exercising every new control
   against a live backend: zero JS runtime errors, and each action produced its real expected
   effect (a discovered topic actually appeared in the vault, a scheduler tick actually filled
   a slot with a new project).
2. **A real bug caught during that same UI work**: the initial fetch-fallback pattern
   (`apiSoft(...) || []`) doesn't work in JavaScript — `promise || []` evaluates the *Promise
   object* (always truthy), not its resolved value, so a failed fetch would silently pass a
   Promise where an array was expected. Neither of the two affected endpoints (`/assets`,
   `/observations`) actually fails under normal use, so this specific instance was low-risk,
   but it's exactly the kind of bug that looks fine until it isn't. Fixed by using the plain
   (non-soft) `api()` call for both, since neither endpoint has a legitimate "doesn't exist
   yet" case the way `/quality-gate` does.
3. **Config/docs drift**: `config.py`'s `enable_scheduler` docstring still described the
   *old* single-replica-only limitation after the scheduler lock had already fixed it — a
   stale comment directly contradicting the actual (correct) behavior. `NEWSAPI_KEY` bypassed
   the centralized `Settings` pattern every other config value goes through (read directly via
   `os.environ` instead). `.env.example` and `docker-compose.yml` were both missing several
   real, working config vars (`FACT_CHECK_THRESHOLD`, `ANTHROPIC_MODEL`, `MAX_UPLOAD_SIZE_MB`,
   `NEWSAPI_KEY`). All fixed.
4. **Confirmed clean**: every non-health-check endpoint requires the API key (checked
   programmatically against the route table, not by eyeballing); no stray `print()`/TODO/FIXME
   markers in application code; `ARCHITECTURE.md` was accurate for the original design but
   silently stale on everything added after — added an addendum section rather than letting it
   keep quietly under-describing the system.
5. **The Create form still doesn't expose everything the API accepts** — was already honestly
   flagged in prior passes as a "field exists on the model, not in the UI" gap for
   target_audience/tone/language/source_urls. Closed this pass: all four are now real form
   fields, verified end-to-end (submitted through the actual UI, confirmed persisted via the
   API). `product_info` (a free-form JSON blob) is intentionally still API-only — a raw JSON
   textarea in the Create form would be worse UX than not having it, and it's better served by
   the dedicated Observations form on the Detail page for the content types where it matters.

**Not fixed, and worth naming rather than leaving implicit**: the Comparison engine
(`POST /api/compare`) still has no UI — it needs a cross-project selection interface that
doesn't fit naturally into any existing page, and building one well felt lower-value than the
fixes above given it's a less central spec feature. It remains fully functional and tested at
the API level.

---

## 6. Second review pass — deeper audit, real bugs found

A follow-up review went past "is the feature list complete" into "does the implementation
actually hold up under scrutiny" — checking migration/model drift, Docker volume semantics,
external-dependency failure modes, and security assumptions that were previously stated but
not directly tested. This found and fixed real issues, not just documentation gaps:

1. **A Docker volume permission bug that would only surface in production.** The Dockerfile
   switches to a non-root user, and `docker-compose.yml` mounts a named volume at
   `/app/uploads` for persistence — but the image never created that directory before the
   `USER appuser` switch. Docker's volume-initialization behavior (copying the image's
   existing content/ownership into a freshly-created named volume) would have created that
   mount point root-owned, and the very first file upload in a real deployment would have
   failed with a permission error. This is exactly the kind of bug that passes every test in
   this repo (none of them run inside Docker) and only shows up when someone actually deploys
   it. Fixed by creating the directory with correct ownership before the user switch.
2. **A missing failure-mode handler for the one genuinely-external dependency in this
   codebase.** `DiscoveryAgent` calls out to a configurable search provider; the real
   implementation (`NewsAPISearchProvider`) talks to an external API that can fail in entirely
   ordinary ways — timeout, bad key, rate limit. The agent had no error handling around that
   call, so any of those ordinary failures would have 500'd the whole discover endpoint instead
   of falling back to ungrounded discovery, which is the entire point of the provider being
   "optional grounding." Fixed with a try/except and a warning log, tested both at the agent
   level (a provider that raises) and through the real HTTP endpoint end-to-end (confirms the
   degradation holds all the way up the stack, not just in isolation).
3. **Verified, rather than assumed, that the upload path-traversal protection actually works**
   — added a test that submits a `../../../../etc/passwd.jpg`-style filename and confirms both
   that nothing was written outside the configured upload directory *and* that the (safely
   renamed) file genuinely was written where expected. In the course of doing this, found and
   documented a real platform-specific edge case: the sanitization only strips `/`-style
   separators, so a backslash-based Windows-style traversal payload would not be stripped —
   but this is a non-issue for this specific application because backslash isn't a path
   separator on POSIX (this app's only deployment target, per the Dockerfile). Documented
   explicitly in the function's docstring rather than left as an unstated assumption, since
   that assumption would become a real vulnerability if this code were ever ported to run on
   Windows.
4. **Confirmed clean, this time by generating an actual Alembic autogenerate diff** rather than
   by eyeballing the models against the migration files: no real schema drift between
   `models.py` and the migration history (an empty no-op revision was generated and discarded
   — that's diagnostic noise, not an issue).
5. **Added edge-case tests for `final-package` and `compare`** on projects with no pipeline
   data yet — both already degraded gracefully (empty defaults, no crash) when checked live
   over HTTP, but neither had a regression test locking that behavior in, so a future change
   could have silently broken it without any test catching it.

Test count: 54 passing (up from 49) — 5 new tests, every one of them added because it caught
or would have caught a real issue, not padding.

**Genuinely nothing else turned up in this pass.** Checked and confirmed clean: every
non-health-check route requires the API key (verified programmatically against the route
table); `requirements.txt` matches actual imports in both directions (nothing missing, nothing
unused); `seed_demo.py` still runs correctly against the current models/pipeline after several
passes of changes since it was first written; CORS production guard still refuses to boot with
a wildcard origin.
