"""Real-time Telugu voice worker (LiveKit Agents).

Runs as its own process. It must never import Django or touch the Django ORM:
anything it needs from the backend goes through authenticated HTTP calls
(added in Phase 4). See docs/adr/0001-two-process-boundary.md.
"""
