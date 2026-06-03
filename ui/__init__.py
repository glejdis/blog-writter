"""Web UI for the blog-writer multi-agent system.

The ``ui`` package provides a FastAPI + WebSocket chat-style interface so
you can talk to the pipeline, watch each agent's progress live, approve
HITL checkpoints with a click, and iterate on the final draft with
free-form revision requests until you're happy.

Entry points
------------
* ``blog-writer ui``                              — recommended CLI launcher
* ``python -m ui.server``                         — equivalent direct module run
* ``uvicorn ui.server:app --host 0.0.0.0 --port 8000`` — for custom ASGI setups

The static SPA lives under ``ui/static/`` and is mounted at ``/`` so the
default ``http://127.0.0.1:8000/`` URL serves the chat UI.
"""
