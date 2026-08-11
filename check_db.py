#!/usr/bin/env python3
"""
check_db.py

Quick sanity-check script for proposals.db after running ingest.py.
Run from the same folder as proposals.db:

    python check_db.py

Or point it at a different DB file:

    python check_db.py --db some_other.db
"""

import argparse
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("--db", default="proposals.db")
parser.add_argument("--limit", type=int, default=10, help="Max unclassified facts to show")
args = parser.parse_args()

conn = sqlite3.connect(args.db)
cur = conn.cursor()

print("=== Proposals ===")
cur.execute("SELECT id, name, folder_name FROM proposals")
for row in cur.fetchall():
    print(" ", row)

print("\n=== People ===")
cur.execute("SELECT id, full_name FROM people ORDER BY full_name")
for row in cur.fetchall():
    print(" ", row)

print("\n=== Fact type breakdown ===")
cur.execute("SELECT fact_type, COUNT(*) FROM resume_facts GROUP BY fact_type ORDER BY COUNT(*) DESC")
for row in cur.fetchall():
    print(" ", row)

print(f"\n=== Sample unclassified facts (up to {args.limit}) ===")
cur.execute("SELECT fact_text FROM resume_facts WHERE fact_type = 'unclassified' LIMIT ?", (args.limit,))
for row in cur.fetchall():
    print(" ", row[0])

conn.close()