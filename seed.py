"""Seed script for LastMile Alert.

Creates the SQLite schema and inserts the 2 pre-loaded sample alerts.
Recipients are NOT seeded: the database shows registered citizens only —
people appear here after they register through the citizen side.
All data is fictional demo data.
Run:  python seed.py
"""

from datetime import datetime

from app import (SAMPLE_ALERTS, TRANSLATIONS, direct_db, init_db,
                 log_event, process_alert)


def seed_alerts(conn):
    """Insert the two pre-loaded sample alerts with their hardcoded translations."""
    for sample in SAMPLE_ALERTS:
        fields = process_alert(sample["official"], sample["state"],
                               sample["disaster_type"])
        fields["simplified_text"] = TRANSLATIONS[sample["key"]]["en"]
        conn.execute(
            """INSERT INTO alerts (official_text, state, disaster_type, severity,
               severity_color, affected_area, required_action, simplified_text,
               sample_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sample["official"], sample["state"], fields["event_type"],
             fields["severity"], fields["severity_color"], fields["affected_area"],
             fields["required_action"], fields["simplified_text"],
             sample["key"], datetime.now().isoformat(timespec="seconds")))
    log_event("DB", f"seeded {len(SAMPLE_ALERTS)} sample alerts")


def reset_database():
    """Wipe the database, recreate the schema, and reseed the sample alerts.

    The recipients table is left empty on purpose: only citizens who
    register through /register appear in the database.
    """
    conn = direct_db()
    conn.execute("DROP TABLE IF EXISTS alerts")
    conn.execute("DROP TABLE IF EXISTS recipients")
    conn.commit()
    conn.close()
    init_db()
    conn = direct_db()
    seed_alerts(conn)
    conn.commit()
    conn.close()
    log_event("DB", "database reset + reseeded (recipients left empty)")


def main():
    """Entry point for `python seed.py`."""
    reset_database()
    print(f"Seeded {len(SAMPLE_ALERTS)} sample alerts into alerts.db "
          f"(0 recipients — register citizens to add them)")


if __name__ == "__main__":
    main()
