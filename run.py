"""Production entrypoint.

Uvicorn's protocol-level WebSocket pings are disabled: the ESP32 client (and
the Render proxy in front of it) were dropping healthy sockets roughly 25 s
after connect. Keepalive is now an application JSON ping every 15 s, which
survives proxies and is visible on the dashboard.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8765")),
        log_level=os.getenv("LOG_LEVEL", "info"),
        ws_ping_interval=None,
        ws_ping_timeout=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
