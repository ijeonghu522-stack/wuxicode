"""WUXICODE desktop GUI.

A windowed front end over the same AgentLoop the CLI drives. The GUI is not a
second implementation: it constructs the same router, the same loop, and the
same tools, then serves the event stream to a web view over localhost.

Three pieces:

    session.py  owns the agent. One turn at a time, cancellable.
    server.py   stdlib HTTP. Static files plus an SSE endpoint per turn.
    app.py      the pywebview window and its lifecycle.
"""

from __future__ import annotations

__all__ = ["Session", "serve", "main"]
