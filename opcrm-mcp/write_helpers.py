"""
Two-phase write helpers. All functions accept confirmed=False (preview) or
confirmed=True (execute). Tag and cadence operations on unowned contacts are
blocked at the server level regardless of confirmed value.
"""
import db
import opcrm


def _is_owner(config, contact_row):
    return contact_row["owner_id"] == config["opcrm_my_user_id"]


def log_note(config, conn, contact_id, text, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found in local cache. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Log note for {contact['name']}: \"{text}\"",
            "action": "log_note",
            "contact_id": contact_id,
            "contact_name": contact["name"],
        }

    result = opcrm.create_note(config, contact_id, text)
    note_data = result.get("data", {}).get("note", {})
    if note_data.get("id"):
        db.upsert_note(conn, {
            "id": note_data["id"],
            "contact_id": contact_id,
            "text": text,
            "date": note_data.get("date", ""),
            "author_id": note_data.get("author_id", ""),
        })
        conn.commit()
    return {"success": True, "note_id": note_data.get("id")}


def create_next_action(config, conn, contact_id, text, due_date, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    owned = _is_owner(config, contact)

    if not confirmed:
        return {
            "confirmation_required": True,
            "unowned_contact": not owned,
            "preview": (
                f"Create next action for {contact['name']} (due {due_date}): \"{text}\""
                + ("" if owned else f"\n⚠ Note: this contact is owned by user {contact['owner_id']}, not you.")
            ),
            "action": "create_next_action",
        }

    result = opcrm.create_action(config, contact_id, text, due_date)
    action_data = result.get("data", {}).get("action", {})
    if action_data.get("id"):
        db.upsert_action(conn, {
            "id": action_data["id"],
            "contact_id": contact_id,
            "text": text,
            "due_date": due_date,
            "assignee_id": action_data.get("assignee_id", ""),
            "status": action_data.get("status", ""),
        })
        conn.commit()
    return {"success": True, "action_id": action_data.get("id")}


def complete_next_action(config, conn, action_id, confirmed=False):
    row = conn.execute(
        "SELECT na.*, c.name, c.owner_id FROM next_actions na "
        "JOIN contacts c ON c.id = na.contact_id WHERE na.id = ?", (action_id,)
    ).fetchone()
    if row is None:
        return {"error": f"Action {action_id} not found. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Complete action for {row['name']}: \"{row['text']}\"",
            "action": "complete_next_action",
        }

    opcrm.complete_action(config, action_id)
    conn.execute("DELETE FROM next_actions WHERE id = ?", (action_id,))
    conn.commit()
    return {"success": True}


def reschedule_next_action(config, conn, action_id, new_date, confirmed=False):
    row = conn.execute(
        "SELECT na.*, c.name, c.owner_id FROM next_actions na "
        "JOIN contacts c ON c.id = na.contact_id WHERE na.id = ?", (action_id,)
    ).fetchone()
    if row is None:
        return {"error": f"Action {action_id} not found. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": (
                f"Reschedule action for {row['name']}: \"{row['text']}\"\n"
                f"From {row['due_date']} → {new_date}"
            ),
            "action": "reschedule_next_action",
        }

    opcrm.reschedule_action(config, action_id, new_date)
    conn.execute(
        "UPDATE next_actions SET due_date = ? WHERE id = ?", (new_date, action_id)
    )
    conn.commit()
    return {"success": True}


def update_cadence(config, conn, contact_id, months, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot update cadence: contact {contact['name']} is not owned by you."}

    # Always preview first, regardless of confirmed flag
    if not confirmed:
        return {
            "confirmation_required": True,
            "current_cadence_months": contact["cadence_months"],
            "new_cadence_months": months,
            "preview": (
                f"Update cadence for {contact['name']}: "
                f"{contact['cadence_months']} months → {months} months.\n"
                f"⚠ This affects overdue detection in the CRM Co-Pilot browser app."
            ),
            "action": "update_cadence",
        }

    opcrm.update_cadence(config, contact_id, months)
    conn.execute(
        "UPDATE contacts SET cadence_months = ? WHERE id = ?", (months, contact_id)
    )
    conn.commit()
    return {"success": True}


def add_tag(config, conn, contact_id, tag, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot add tag: contact {contact['name']} is not owned by you."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Add tag \"{tag}\" to {contact['name']}",
            "action": "add_tag",
        }

    opcrm.add_tag(config, contact_id, tag)
    conn.execute(
        "INSERT OR IGNORE INTO contact_tags (contact_id, tag) VALUES (?, ?)",
        (contact_id, tag)
    )
    conn.commit()
    return {"success": True}


def remove_tag(config, conn, contact_id, tag, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot remove tag: contact {contact['name']} is not owned by you."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Remove tag \"{tag}\" from {contact['name']}",
            "action": "remove_tag",
        }

    opcrm.remove_tag(config, contact_id, tag)
    conn.execute(
        "DELETE FROM contact_tags WHERE contact_id = ? AND tag = ?", (contact_id, tag)
    )
    conn.commit()
    return {"success": True}
