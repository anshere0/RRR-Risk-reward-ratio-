"""Entry point: python -m rr_engine.

Starts the FastAPI server with live market feed and static HUD.
HUD is served at http://127.0.0.1:8000/hud/ (and root / redirects to /hud/).
Strictly bound to 127.0.0.1 per docs/06.
"""

from __future__ import annotations

import uvicorn

from rr_engine.config import get_settings


def main() -> None:
    settings = get_settings()
    host = settings.server.host
    port = settings.server.port

    print("==================================================================")
    print(" Delta Exchange BTC Options Risk-Reward Engine (HUD & API)")
    print(f" Server running at: http://{host}:{port}/hud/")
    print(f" API documentation: http://{host}:{port}/docs")
    print(f" Region: {settings.region} | Feed: {settings.active_hosts.rest}")
    print(f" Bound strictly to {host} (No trading API keys, zero order placement)")
    print(" Decision support, not financial advice. Options can lose 100% of premium.")
    print("==================================================================")

    uvicorn.run(
        "rr_engine.api.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
