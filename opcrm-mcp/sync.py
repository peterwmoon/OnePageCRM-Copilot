import json
from datetime import datetime, timezone, timedelta

import auth
import db
import graph
import opcrm

CADENCE_FIELD_ID = "699669362480755968b7997e"
DEFAULT_CADENCE_MONTHS = 6


def _parse_contact(raw):
    """Extract structured fields from a raw OPCRM contact wrapper."""
    c = raw.get("contact", raw)

    cadence_months = DEFAULT_CADENCE_MONTHS
    for field in c.get("custom_fields", []):
        nested_id = field.get("custom_field", {}).get("id", "")
        if nested_id == CADENCE_FIELD_ID and field.get("value"):
            try:
                cadence_months = int(field["value"])
            except (ValueError, TypeError):
                pass

    emails = c.get("emails", [])
    primary_email = emails[0].get("value", "").strip() if emails else ""

    phones = c.get("phones", [])
    primary_phone = phones[0].get("value", "").strip() if phones else ""

    return {
        "id": c["id"],
        "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
        "company": c.get("company_name", ""),
        "email": primary_email,
        "phone": primary_phone,
        "owner_id": c.get("owner_id", ""),
        "status": c.get("status", ""),
        "cadence_months": cadence_months,
        "raw_json": json.dumps(c),
    }


def _parse_note(raw, contact_id):
    n = raw.get("note", raw)
    return {
        "id": n["id"],
        "contact_id": contact_id,
        "text": n.get("text", ""),
        "date": n.get("date", ""),
        "author_id": n.get("author_id", n.get("user_id", "")),
    }


def _parse_call(raw, contact_id):
    c = raw.get("call", raw)
    return {
        "id": c["id"],
        "contact_id": contact_id,
        "text": c.get("text", ""),
        "date": c.get("date", ""),
        "author_id": c.get("author_id", c.get("user_id", "")),
        "duration": c.get("duration", 0) or 0,
    }


def _parse_meeting(raw, contact_id):
    m = raw.get("meeting", raw)
    return {
        "id": m["id"],
        "contact_id": contact_id,
        "text": m.get("text", ""),
        "date": m.get("date", ""),
        "author_id": m.get("author_id", m.get("user_id", "")),
    }


def _parse_action(raw):
    a = raw.get("action", raw)
    return {
        "id": a["id"],
        "contact_id": a.get("contact_id", ""),
        "text": a.get("text", ""),
        "due_date": a.get("date", ""),
        "assignee_id": a.get("assignee_id", a.get("user_id", "")),
        "status": a.get("status", ""),
    }


def sync_opcrm(config, conn=None):
    """Sync contacts and next actions from OPCRM. Fast: ~3 API calls total."""
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        raw_contacts = opcrm.fetch_all_contacts(config)
        synced_contact_ids = set()

        for raw in raw_contacts:
            contact = _parse_contact(raw)
            db.upsert_contact(conn, contact)
            synced_contact_ids.add(contact["id"])
            c = raw.get("contact", raw)
            db.upsert_tags(conn, contact["id"], c.get("tags", []))

        actions_synced = 0
        for raw_action in opcrm.fetch_next_actions(config):
            action = _parse_action(raw_action)
            if action["contact_id"] in synced_contact_ids:
                db.upsert_action(conn, action)
                actions_synced += 1

        db.log_sync(conn, "opcrm", len(raw_contacts), actions_synced)
        conn.commit()
        return {
            "contacts_synced": len(raw_contacts),
            "actions_synced": actions_synced,
            "error": None,
            "note": "Run sync_history() to sync notes, calls, and meetings (slower).",
        }

    except Exception as e:
        db.log_sync(conn, "opcrm", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def sync_history(config, conn=None):
    """Bulk-sync notes, calls, and meetings across all contacts. Slower — run once or weekly."""
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        known_ids = {
            row["id"] for row in conn.execute("SELECT id FROM contacts").fetchall()
        }

        notes = calls = meetings = 0
        for raw in opcrm.fetch_all_notes(config):
            n = raw.get("note", raw)
            cid = n.get("contact_id", "")
            if cid in known_ids:
                db.upsert_note(conn, _parse_note(raw, cid))
                notes += 1

        for raw in opcrm.fetch_all_calls(config):
            c = raw.get("call", raw)
            cid = c.get("contact_id", "")
            if cid in known_ids:
                db.upsert_call(conn, _parse_call(raw, cid))
                calls += 1

        for raw in opcrm.fetch_all_meetings(config):
            m = raw.get("meeting", raw)
            cid = m.get("contact_id", "")
            if cid in known_ids:
                db.upsert_meeting(conn, _parse_meeting(raw, cid))
                meetings += 1

        db.log_sync(conn, "opcrm_history", 0, notes + calls + meetings)
        conn.commit()
        return {"notes_synced": notes, "calls_synced": calls, "meetings_synced": meetings, "error": None}

    except Exception as e:
        db.log_sync(conn, "opcrm_history", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def _process_email_batch(msgs, email_map, conn):
    """Match a list of Graph messages to contacts; store matched and unmatched. Returns (matched, unmatched) counts."""
    matched = unmatched = 0
    for msg in msgs:
        from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
        to_addrs = [
            r["emailAddress"]["address"].lower()
            for r in msg.get("toRecipients", [])
        ]
        contact_id = email_map.get(from_addr)
        direction = "in"
        if not contact_id:
            for addr in to_addrs:
                if addr in email_map:
                    contact_id = email_map[addr]
                    direction = "out"
                    break

        if contact_id:
            db.upsert_email(conn, {
                "id": msg["id"],
                "contact_id": contact_id,
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": direction,
                "thread_id": msg.get("conversationId", ""),
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
            })
            matched += 1
        else:
            db.upsert_unmatched_email(conn, {
                "id": msg["id"],
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": "in" if from_addr and from_addr not in to_addrs else "out",
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
                "conversation_id": msg.get("conversationId", ""),
            })
            unmatched += 1
    return matched, unmatched


def sync_graph(config, conn=None, since_date=None):
    """
    Sync Outlook emails incrementally. Uses last sync timestamp if since_date not given.
    Matched emails go to emails table; unmatched go to unmatched_emails.
    """
    import time
    if not (config.get("graph_access_token") and config.get("graph_token_expiry", 0) > time.time() + 60):
        if not config.get("graph_refresh_token"):
            raise RuntimeError(
                "Outlook not authorized. Call start_graph_auth() then complete_graph_auth() first."
            )
    token = auth.get_graph_token(config)

    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        if since_date is None:
            last = db.get_last_sync_time(conn, "graph")
            if last:
                since_date = last.replace(" ", "T") + "Z"
            else:
                dt = datetime.now(timezone.utc) - timedelta(days=90)
                since_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
        email_map = {row["email"].lower(): row["id"] for row in rows}

        seen_ids = set()
        all_msgs = []
        for folder in ["me/messages", "me/mailFolders/sentItems/messages"]:
            for msg in graph.fetch_emails(token, since_date=since_date, folder=folder):
                if msg["id"] not in seen_ids:
                    seen_ids.add(msg["id"])
                    all_msgs.append(msg)

        matched, unmatched = _process_email_batch(all_msgs, email_map, conn)

        db.log_sync(conn, "graph", 0, matched + unmatched)
        conn.commit()
        return {"emails_matched": matched, "emails_unmatched": unmatched, "since": since_date, "error": None}

    except Exception as e:
        db.log_sync(conn, "graph", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def sync_calendar(config, conn=None, since_date=None, until_date=None):
    """Sync Outlook calendar events. Incremental by default using last sync timestamp."""
    import time
    if not config.get("graph_refresh_token") and not (
        config.get("graph_access_token") and config.get("graph_token_expiry", 0) > time.time() + 60
    ):
        raise RuntimeError("Outlook not authorized. Run start_graph_auth() first.")

    token = auth.get_graph_token(config)

    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        if since_date is None:
            last = db.get_last_sync_time(conn, "calendar")
            if last:
                since_date = last.replace(" ", "T") + "Z"
            else:
                dt = datetime.now(timezone.utc) - timedelta(days=90)
                since_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
        email_map = {row["email"].lower(): row["id"] for row in rows}

        events = graph.fetch_calendar_events(token, since_date=since_date, until_date=until_date)

        inserted = 0
        for evt in events:
            organizer = evt.get("organizer", {}).get("emailAddress", {}).get("address", "").lower()
            attendees = [
                a["emailAddress"]["address"].lower()
                for a in evt.get("attendees", [])
                if a.get("emailAddress", {}).get("address")
            ]
            # Collect all matching CRM contacts (organizer + all attendees)
            contact_ids = []
            for addr in [organizer] + attendees:
                cid = email_map.get(addr)
                if cid and cid not in contact_ids:
                    contact_ids.append(cid)

            db.upsert_calendar_event(conn, {
                "id": evt["id"],
                "subject": evt.get("subject", ""),
                "start_datetime": evt.get("start", {}).get("dateTime", ""),
                "end_datetime": evt.get("end", {}).get("dateTime", ""),
                "organizer_email": organizer,
                "attendees": json.dumps(attendees),
                "body_preview": evt.get("bodyPreview", ""),
                "contact_id": contact_ids[0] if contact_ids else None,
            })
            db.upsert_calendar_event_contacts(conn, evt["id"], contact_ids)
            inserted += 1

        db.log_sync(conn, "calendar", 0, inserted)
        conn.commit()
        return {"events_synced": inserted, "since": since_date, "error": None}

    except Exception as e:
        db.log_sync(conn, "calendar", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def full_sync(config, conn=None):
    """Run OPCRM sync then Graph sync. Returns combined summary."""
    result_opcrm = sync_opcrm(config, conn=conn)
    result_graph = sync_graph(config, conn=conn)
    return {**result_opcrm, **result_graph}
