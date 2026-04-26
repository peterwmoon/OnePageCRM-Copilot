"""
OnePageCRM Copilot — local HTTP server

Serves two things on https://localhost:8765:
  /      → index.html (CRM Copilot browser app)
  /mcp   → FastMCP SSE endpoint (for Claude chat / Cowork)

Run:  python local_server.py
Start at login via Task Scheduler (see README or CLAUDE.md).
"""
import json
import os
import sys
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
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


# ── Kiwi Dials activity log ────────────────────────────────────────────────────

_APPINSIGHTS_APP_ID  = os.environ.get('APPINSIGHTS_APP_ID', '')
_APPINSIGHTS_API_KEY = os.environ.get('APPINSIGHTS_API_KEY', '')
_ACTIVITY_LOG_HTML   = Path(__file__).parent / 'activity-log.html'
_VALID_WINDOWS       = {'24h', '48h', '5d'}


async def kiwi_page(request: Request) -> HTMLResponse:
    html = _ACTIVITY_LOG_HTML.read_text(encoding='utf-8')
    return HTMLResponse(html)


async def kiwi_events(request: Request) -> JSONResponse:
    window = request.query_params.get('window', '24h')
    if window not in _VALID_WINDOWS:
        window = '24h'

    if not _APPINSIGHTS_APP_ID or not _APPINSIGHTS_API_KEY:
        return JSONResponse({'error': 'APPINSIGHTS_APP_ID and APPINSIGHTS_API_KEY not set'}, status_code=500)

    kql = _build_kql(window)
    url = f'https://api.applicationinsights.io/v1/apps/{_APPINSIGHTS_APP_ID}/query'
    headers = {
        'x-api-key': _APPINSIGHTS_API_KEY,
        'Content-Type': 'application/json',
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json={'query': kql}, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()

    events = _normalize_results(data)
    return JSONResponse(events)


def _build_kql(window: str) -> str:
    return f"""
let window_duration = {window};
let member_emails = customEvents
| where name == "user_signed_in"
| extend memberId = tostring(customDimensions["memberId"]),
         member_email = tostring(customDimensions["email"])
| summarize arg_max(timestamp, *) by memberId
| project memberId, memberEmail=member_email;
let evts = customEvents
| where timestamp > ago(window_duration)
| where name in ("user_signed_in", "vote_submitted", "invitation_created",
                  "invitation_accepted", "team_switched", "insight_requested")
| extend memberId = tostring(customDimensions["memberId"])
| join kind=leftouter member_emails on memberId
| project timestamp, eventType=name, props=customDimensions, memberEmail;
let pages = pageViews
| where timestamp > ago(window_duration)
| where name has_any ("Dashboard", "Trends", "Insights")
| join kind=leftouter member_emails on $left.user_AuthenticatedId == $right.memberId
| project timestamp, eventType="page_view",
          props=bag_pack("page", name, "memberId", user_AuthenticatedId),
          memberEmail;
union evts, pages
| order by timestamp desc
"""


def _normalize_results(data: dict) -> list:
    tables = data.get('tables', [])
    if not tables:
        return []
    table = tables[0]
    columns = [c['name'] for c in table['columns']]
    return [ev for row in table['rows'] if (ev := _normalize_row(dict(zip(columns, row)))) is not None]


def _normalize_row(row: dict) -> dict | None:
    timestamp    = row.get('timestamp', '')
    event_type   = row.get('eventType', '')
    member_email = row.get('memberEmail', '') or ''
    props        = row.get('props', {}) or {}

    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            props = {}

    def r(et, email, detail):
        return {'timestamp': timestamp, 'eventType': et, 'email': email, 'detail': detail}

    if event_type == 'user_signed_in':
        email = props.get('email') or member_email or ''
        return r('sign_in', email, '')

    if event_type == 'vote_submitted':
        return r('vote', member_email, props.get('teamName', ''))

    if event_type == 'invitation_created':
        invitee = props.get('inviteeEmail', '')
        team    = props.get('teamName', '')
        detail  = f'→ {invitee}' + (f' · {team}' if team else '') if invitee else team
        return r('invite_sent', member_email, detail)

    if event_type == 'invitation_accepted':
        invitee = props.get('inviteeEmail', '') or member_email
        team    = props.get('teamName', '')
        return r('invite_accepted', invitee, team)

    if event_type == 'team_switched':
        email = props.get('email', '') or member_email
        team  = props.get('teamName', props.get('toTeamId', ''))
        return r('team_switch', email, f'→ {team}' if team else '')

    if event_type == 'insight_requested':
        return r('assessment', member_email, props.get('teamName', ''))

    if event_type == 'page_view':
        page = props.get('page', '').split('/')[-1].replace('Page', '') or 'page'
        return r('page_view', member_email, page)

    return None


app = Starlette(
    routes=[
        Route("/", serve_index),
        Route("/kiwi/", kiwi_page),
        Route("/kiwi/events", kiwi_events),
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
