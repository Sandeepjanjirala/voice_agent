# ADR 0001: The voice worker and Django are separate processes

Status: accepted, 13 November 2026.

## Context

A phone conversation needs replies within about a second. The LiveKit Agents worker
holds an asyncio event loop that streams audio to and from STT, LLM and TTS providers
for the whole call. Django serves short HTTP requests, and much of its ORM is
synchronous. Running one inside the other makes both worse: a slow query stalls live
audio, and a call lasting minutes does not fit a request or a Celery task.

## Decision

One Git repository, two independently started processes.

| | Voice worker | Django backend |
| :--- | :--- | :--- |
| Code | `voice_agent/` | `config/`, `apps/` |
| Started with | `lk agent dev voice_agent/agent.py` or `uv run voice_agent/agent.py start` | `uv run python manage.py runserver` (ASGI server in production) |
| Owns | STT, LLM, TTS, turn-taking, call lifecycle | Data, auth, bookings, consent, dashboard, outbound intent |
| Deployed to | LiveKit Cloud | Its own host |

Rules:

1. `voice_agent/` never imports Django, DRF, `config`, `apps` or Celery.
2. `config/` and `apps/` never import `livekit.agents`, `livekit.plugins` or `voice_agent`.
   Django may use the `livekit-api` server SDK later to request dispatches.
3. No FastAPI and no SQLAlchemy anywhere.
4. The voice loop never runs inside a Django request or a Celery task.

`tests/test_process_boundary.py` fails the build if rules 1 to 3 are broken.

## How the two will talk (Phase 4, not built yet)

- Worker to Django: authenticated HTTPS calls with short timeouts, for tool actions
  (check or book an appointment) and for call and transcript events. Each request
  carries an idempotency key, a timestamp and an HMAC signature made with
  `INTERNAL_WORKER_API_KEY`; Django rejects stale or replayed requests and deduplicates
  by event id.
- Django to worker: never directly. Django asks LiveKit to dispatch the agent
  (`create_dispatch` with job metadata); LiveKit delivers the job to a worker.
- Events are sent without blocking the audio loop. A failed tool call produces a spoken
  fallback, not a retry loop.

## Consequences

- Both processes read `.env.local`, but each uses only its own variables.
- They share one `pyproject.toml` for now, so the worker image also installs Django.
  Split into dependency groups if image size becomes a concern.
- The contract between them needs its own tests (`tests/integration/`, Phase 4).
