"""
OnePageCRM Copilot — local HTTP server

Serves two things on https://localhost:8765:
  /      → index.html (CRM Copilot browser app)
  /mcp   → FastMCP SSE endpoint (for Claude chat / Cowork)

Run:  python local_server.py
Start at login via Task Scheduler (see README or CLAUDE.md).
"""
import os
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route

# Add opcrm-mcp to path so server.py imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "opcrm-mcp"))

# Import the FastMCP instance from the MCP server module.
# This registers all tools (@mcp.tool() decorators run at import time).
from server import mcp  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(BASE_DIR, "index.html")
SSL_CERT = os.path.join(BASE_DIR, "localhost.crt")
SSL_KEY = os.path.join(BASE_DIR, "localhost.key")


class PrivateNetworkAccessMiddleware(BaseHTTPMiddleware):
    """Allow chrome.ai and other browser clients to connect to localhost.
    Chrome's Private Network Access policy requires servers to explicitly
    opt in via Access-Control-Allow-Private-Network: true."""

    async def dispatch(self, request: Request, call_next):
        # Handle PNA preflight (OPTIONS with Access-Control-Request-Private-Network)
        if (
            request.method == "OPTIONS"
            and request.headers.get("access-control-request-private-network") == "true"
        ):
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Private-Network": "true",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


async def serve_index(request: Request) -> FileResponse:
    return FileResponse(INDEX)


app = Starlette(
    routes=[
        Route("/", serve_index),
        Mount("/mcp", app=mcp.sse_app()),
    ],
    middleware=[
        Middleware(PrivateNetworkAccessMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["https://claude.ai"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        ),
    ],
)

if __name__ == "__main__":
    print(f"Starting OnePageCRM Copilot server on https://{HOST}:{PORT}")
    print(f"  Browser app : https://{HOST}:{PORT}/")
    print(f"  MCP endpoint: https://{HOST}:{PORT}/mcp/sse")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                ssl_certfile=SSL_CERT, ssl_keyfile=SSL_KEY)
