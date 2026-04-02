# OnePageCRM Copilot

Personal tooling built on top of [OnePageCRM](https://www.onepagecrm.com/). Not affiliated with Onepoint.

This repo contains **two separate applications** that share the same OnePageCRM API credentials:

---

## 1. Copilot — browser dashboard (`index.html`)

A single-file HTML/JS dashboard that overlays intelligence on top of OnePageCRM:

- Contact action status (overdue, due today, upcoming, waiting, queued)
- Outlook email intelligence (unanswered inbound emails)
- Cadence exception detection (contacts overdue for touch based on a per-contact cadence field)

**Usage:** Open `index.html` in a browser. Enter your OnePageCRM User ID and API Key at runtime. Requires a Microsoft Graph OAuth token for email features.

No build step. No dependencies. No server.

---

## 2. MCP server — Claude integration (`opcrm-mcp/`)

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives Claude access to your CRM data. Intended for use with Claude Desktop or claude.ai/code.

Syncs contacts, next actions, notes, calls, meetings, emails, calendar events, deals, and LinkedIn connections into a local SQLite cache (`crm_cache.db`), then exposes read and write tools to Claude.

**Setup:**

```bash
cd opcrm-mcp
pip install -r requirements.txt
cp config.json.template config.json
# Edit config.json with your OnePageCRM User ID and API Key
python server.py
```

Configure Claude Desktop to connect via stdio. See `opcrm-mcp/config.json.template` for the expected structure.

---

## Owner

Peter Moon — pmoon@navicet.com
