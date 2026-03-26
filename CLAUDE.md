# CLAUDE.md — OnePageCRM Copilot

Personal tool. Not affiliated with Lunous.

---

## What This Is

A single-page CRM dashboard that overlays intelligence on top of OnePageCRM — contact action status (overdue, due today, upcoming, waiting, queued), Outlook email intelligence (unanswered inbound emails), and cadence exception detection (contacts overdue for touch based on a per-contact cadence field).

**Owner**: Peter Moon — pmoon@navicet.com

---

## Architecture

**Single HTML file. This is intentional and must be preserved.**

- No build system. No npm. No bundler.
- Vanilla JS + CSS only. No frameworks, no libraries (Google Fonts CDN is the only external resource besides the APIs).
- The entire application is `index.html`. Do not split it into components, modules, or separate files unless Peter explicitly asks.
- Deployable by opening the file in a browser. No server required.

---

## External APIs

| API | Purpose | Auth |
|---|---|---|
| OnePageCRM v3 (`https://app.onepagecrm.com/api/v3`) | Contacts, next actions, deals | Basic Auth: User ID + API Key (entered at runtime, never hardcoded) |
| Microsoft Graph (`https://graph.microsoft.com/v1.0`) | Outlook email intelligence | OAuth — token stored in localStorage |
| corsproxy.io | CORS proxy for OnePageCRM API calls | None — required because app is client-side |

### Key hardcoded values
- **Cadence custom field ID**: `699669362480755968b7997e` — this is Peter's OnePageCRM account-specific field. Do not change without confirming it still matches.
- **Default cadence**: 6 months — applied to contacts without an explicit cadence field value.

---

## Shell Environment

Windows PowerShell.

| Bash | PowerShell equivalent |
|---|---|
| `&&` to chain | `;` to chain |
| `$VAR` | `$env:VAR` |
| `rm -rf` | `Remove-Item -Recurse -Force` |

---

## Never Do

- Add a build system, package.json, or npm dependencies
- Split `index.html` into multiple files
- Hardcode API keys or user IDs
- Replace corsproxy.io without understanding why it's there (OnePageCRM API does not support CORS from browser clients)
- Add a backend — the tool is intentionally client-side only

---

## Session Protocol

No parallel sessions. This is a solo personal project.

**Startup**: Read this file, run `git log --oneline -5`, ask Peter what the session goal is.

**Close**: Commit with a clear message and push. No formal session doc needed.
