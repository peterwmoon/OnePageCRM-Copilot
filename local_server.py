"""
OnePageCRM Copilot — local HTTP server

Serves two things on http://localhost:8765:
  /      → index.html (CRM Copilot browser app)
  /mcp   → FastMCP SSE endpoint (for Claude chat / Cowork)

Run:  python local_server.py
Start at login via Task Scheduler (see README or CLAUDE.md).
"""
import os
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route

# Add opcrm-mcp to path so server.py imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "opcrm-mcp"))

# Import the FastMCP instance from the MCP server module.
# This registers all tools (@mcp.tool() decorators run at import time).
from server import mcp  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
INDEX = os.path.join(os.path.dirname(__file__), "index.html")


async def serve_index(request: Request) -> FileResponse:
    return FileResponse(INDEX)


app = Starlette(
    routes=[
        Route("/", serve_index),
        Mount("/mcp", app=mcp.sse_app()),
    ]
)

if __name__ == "__main__":
    print(f"Starting OnePageCRM Copilot server on http://{HOST}:{PORT}")
    print(f"  Browser app : http://{HOST}:{PORT}/")
    print(f"  MCP endpoint: http://{HOST}:{PORT}/mcp")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
