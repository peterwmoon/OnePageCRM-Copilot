"""
Initial bulk sync — run this ONCE to populate 2 years of email + calendar history.

Usage:
    python initial_sync.py              # 2 years, 3-month batches
    python initial_sync.py --years 1    # 1 year instead

Close the Claude desktop app before running — it holds the database open.
After this completes, reopen the desktop app for normal incremental syncs.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta

import auth
import db
import graph as graph_module
import sync as sync_module


def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def date_batches(years, batch_months=3):
    """Yield (since, until) pairs from oldest to newest."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(days=1)
    start = add_months(now, -years * 12)
    cursor = start
    while cursor < end:
        batch_end = min(add_months(cursor, batch_months), end)
        yield cursor.strftime("%Y-%m-%dT%H:%M:%SZ"), batch_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = batch_end


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    print(f"Initial sync: {args.years} year(s) of email + calendar in 3-month batches")
    print("Make sure the Claude desktop app is closed before continuing.")
    print()

    config = auth.load_config()
    if not config.get("graph_refresh_token"):
        print("ERROR: No Graph token found. Run graph_auth_step1.py + graph_auth_step2.py first.")
        sys.exit(1)

    conn = db.get_conn()
    db.init_db(conn)

    # Build contact email map
    rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
    email_map = {row["email"].lower(): row["id"] for row in rows}
    print(f"Loaded {len(email_map)} contact email addresses for matching.")
    print()

    token = auth.get_graph_token(config)

    batches = list(date_batches(args.years))
    total_matched = total_unmatched = total_events = 0

    # ── Email sync ─────────────────────────────────────────────────────────────
    print(f"=== EMAIL SYNC ({len(batches)} batches) ===")
    for i, (since, until) in enumerate(batches, 1):
        print(f"  Batch {i}/{len(batches)}: {since[:10]} → {until[:10]} ...", end=" ", flush=True)
        try:
            seen_ids = set()
            all_msgs = []
            for folder in ["me/messages", "me/mailFolders/sentItems/messages"]:
                for msg in graph_module.fetch_emails(token, since_date=since, until_date=until, folder=folder):
                    if msg["id"] not in seen_ids:
                        seen_ids.add(msg["id"])
                        all_msgs.append(msg)

            matched, unmatched = sync_module._process_email_batch(all_msgs, email_map, conn)
            conn.commit()
            total_matched += matched
            total_unmatched += unmatched
            print(f"{matched} matched, {unmatched} unmatched")
        except Exception as e:
            print(f"ERROR: {e}")
            conn.rollback()
        time.sleep(0.5)  # be gentle with the API

    db.log_sync(conn, "graph", 0, total_matched + total_unmatched)
    conn.commit()
    print(f"\nEmail total: {total_matched} matched, {total_unmatched} unmatched")

    # ── Calendar sync ──────────────────────────────────────────────────────────
    print(f"\n=== CALENDAR SYNC ===")
    since_cal = batches[0][0]
    until_cal = batches[-1][1]
    print(f"  Fetching {since_cal[:10]} → {until_cal[:10]} ...", end=" ", flush=True)
    try:
        events = graph_module.fetch_calendar_events(token, since_date=since_cal, until_date=until_cal)
        for evt in events:
            organizer = evt.get("organizer", {}).get("emailAddress", {}).get("address", "").lower()
            attendees = [
                a["emailAddress"]["address"].lower()
                for a in evt.get("attendees", [])
                if a.get("emailAddress", {}).get("address")
            ]
            contact_id = email_map.get(organizer)
            if not contact_id:
                for addr in attendees:
                    if addr in email_map:
                        contact_id = addr
                        break
            db.upsert_calendar_event(conn, {
                "id": evt["id"],
                "subject": evt.get("subject", ""),
                "start_datetime": evt.get("start", {}).get("dateTime", ""),
                "end_datetime": evt.get("end", {}).get("dateTime", ""),
                "organizer_email": organizer,
                "attendees": json.dumps(attendees),
                "body_preview": evt.get("bodyPreview", ""),
                "contact_id": contact_id,
            })
            total_events += 1
        db.log_sync(conn, "calendar", 0, total_events)
        conn.commit()
        print(f"{total_events} events synced")
    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n=== DONE ===")
    print(f"  Emails matched to contacts : {total_matched}")
    print(f"  Unmatched emails (new leads): {total_unmatched}")
    print(f"  Calendar events            : {total_events}")
    print()
    print("Reopen the Claude desktop app and ask Claude to call find_unknown_contacts()")
    print("to review people who emailed you but aren't in your CRM.")
    conn.close()


if __name__ == "__main__":
    main()
