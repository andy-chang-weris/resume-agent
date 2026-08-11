#!/usr/bin/env python3
"""
check_missing_headers.py

Checks whether specific expected header phrases appear ANYWHERE in a
document's raw_text (not necessarily in the right position). Only prints
True/False per header -- never prints the actual resume content -- so
it's safe to share output from.

Use this to test whether a header is missing from extraction entirely
(e.g. because it's rendered as a text box/shape in the Word template,
which python-docx cannot read) vs. present but not being detected as a
header by looks_like_header().

USAGE:
    python check_missing_headers.py --db proposals.db --doc-id 8
"""

import argparse
import sqlite3

CANDIDATE_HEADERS = [
    "CORE QUALIFICATIONS", "Core Qualifications",
    "QUALIFICATIONS", "Qualifications",
    "CERTIFICATIONS", "Certifications",
    "AREAS OF EXPERTISE", "Areas of Expertise",
    "PROFESSIONAL SUMMARY", "Professional Summary",
    "SUMMARY", "Summary",
]

parser = argparse.ArgumentParser()
parser.add_argument("--db", default="proposals.db")
parser.add_argument("--doc-id", type=int, required=True)
args = parser.parse_args()

conn = sqlite3.connect(args.db)
cur = conn.cursor()
cur.execute("SELECT raw_text FROM documents WHERE id = ?", (args.doc_id,))
row = cur.fetchone()
if not row or not row[0]:
    print("No raw_text found for that document id.")
else:
    text = row[0]
    print(f"raw_text length: {len(text)} characters\n")
    print(f"{'Header phrase':<30} Present in raw_text?")
    for header in CANDIDATE_HEADERS:
        print(f"{header:<30} {header in text}")
conn.close()