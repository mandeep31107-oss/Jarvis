"""The dashboard (spec section 29): fourteen sections, read-only by default."""

from jarvis.dashboard.server import PAGE, Dashboard, make_server, serve

__all__ = ["Dashboard", "PAGE", "make_server", "serve"]
