import json
from datetime import datetime, timezone

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
    """Sync all OPCRM data into the database. Pass conn for testing."""
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        raw_contacts = opcrm.fetch_all_contacts(config)
        records = 0
        synced_contact_ids = set()

        for raw in raw_contacts:
            contact = _parse_contact(raw)
            db.upsert_contact(conn, contact)
            synced_contact_ids.add(contact["id"])

            c = raw.get("contact", raw)
            db.upsert_tags(conn, contact["id"], c.get("tags", []))

            for raw_note in opcrm.fetch_notes(config, contact["id"]):
                db.upsert_note(conn, _parse_note(raw_note, contact["id"]))
                records += 1

            for raw_call in opcrm.fetch_calls(config, contact["id"]):
                db.upsert_call(conn, _parse_call(raw_call, contact["id"]))
                records += 1

            for raw_meeting in opcrm.fetch_meetings(config, contact["id"]):
                db.upsert_meeting(conn, _parse_meeting(raw_meeting, contact["id"]))
                records += 1

        for raw_action in opcrm.fetch_next_actions(config):
            action = _parse_action(raw_action)
            if action["contact_id"] in synced_contact_ids:
                db.upsert_action(conn, action)
                records += 1

        db.log_sync(conn, "opcrm", len(raw_contacts), records)
        conn.commit()
        return {"contacts_synced": len(raw_contacts), "records_synced": records, "error": None}

    except Exception as e:
        db.log_sync(conn, "opcrm", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def sync_graph(config, conn=None):
    """Sync Outlook emails matched to OPCRM contacts. Pass conn for testing."""
    token = auth.get_graph_token(config)

    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
        email_map = {row["email"].lower(): row["id"] for row in rows}

        seen_ids = set()
        all_emails = []
        for folder in ["me/messages", "me/mailFolders/sentItems/messages"]:
            for msg in graph.fetch_emails(token, folder=folder):
                if msg["id"] not in seen_ids:
                    seen_ids.add(msg["id"])
                    all_emails.append(msg)

        inserted = 0
        for msg in all_emails:
            from_addr = (
                msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            )
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

            if not contact_id:
                continue

            db.upsert_email(conn, {
                "id": msg["id"],
                "contact_id": contact_id,
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": direction,
                "thread_id": msg.get("threadId", ""),
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
            })
            inserted += 1

        db.log_sync(conn, "graph", 0, inserted)
        conn.commit()
        return {"emails_synced": inserted, "error": None}

    except Exception as e:
        db.log_sync(conn, "graph", 0, 0, str(e))
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
