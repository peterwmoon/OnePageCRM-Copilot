"""
Import LinkedIn connections from a data export CSV or zip.

Usage:
    python linkedin_import.py Connections.csv
    python linkedin_import.py linkedin_data_export.zip

How to get your LinkedIn export:
    LinkedIn → Settings → Data Privacy → Get a copy of your data → Connections
    Download the zip (usually ready within minutes).

Run this every ~3 months. After importing, ask Claude:
    "Who changed jobs since my last LinkedIn import?"
"""
import sys
import zipfile
from datetime import date
from pathlib import Path

import db
import linkedin as li


def main():
    if len(sys.argv) < 2:
        print("Usage: python linkedin_import.py <Connections.csv or export.zip>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            csv_name = next(
                (n for n in names if "Connections" in n and n.endswith(".csv")),
                None,
            )
            if not csv_name:
                print(f"ERROR: Could not find Connections.csv in zip.")
                print(f"Files in zip: {names}")
                sys.exit(1)
            text = zf.read(csv_name).decode("utf-8-sig")
    else:
        text = path.read_text(encoding="utf-8-sig")

    try:
        connections = li.parse_connections_csv(text)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    conn = db.get_conn()
    db.init_db(conn)

    snapshot_date = date.today().isoformat()

    # Check existing snapshots before inserting
    existing_dates = [
        r["snapshot_date"]
        for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM linkedin_connections "
            "ORDER BY snapshot_date DESC"
        ).fetchall()
    ]

    inserted = 0
    for c in connections:
        before = conn.total_changes
        db.insert_linkedin_connection(conn, c, snapshot_date)
        if conn.total_changes > before:
            inserted += 1

    skipped = len(connections) - inserted
    db.log_linkedin_import(conn, snapshot_date, inserted)
    conn.commit()
    conn.close()

    print(f"Snapshot date : {snapshot_date}")
    print(f"Connections   : {len(connections)} total, {inserted} new, {skipped} already existed")

    if not existing_dates:
        print()
        print("First import complete.")
        print('Run again in ~3 months, then ask Claude: "Who changed jobs since my last LinkedIn import?"')
    elif snapshot_date not in existing_dates:
        prev = existing_dates[0]
        print()
        print(f"Two snapshots available: {prev} and {snapshot_date}.")
        print('Ask Claude: "Who changed jobs since my last LinkedIn import?"')


if __name__ == "__main__":
    main()
