#!/usr/bin/env python3
"""
debug_extraction.py

Diagnostic tool: shows which lines in a document's raw_text were detected
as headers (and what they mapped to), WITHOUT printing the actual body
content under each header -- safe to share output from, since it doesn't
expose resume/PII content, just structure.

USAGE:
    List documents with an index number to pick from:
        python debug_extraction.py --db proposals.db --list

    Inspect one document's header structure:
        python debug_extraction.py --db proposals.db --doc-id 7
"""

import argparse
import sqlite3

from ingest import looks_like_header, normalize_header, BULLET_PREFIX_RE


def list_docs(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.doc_type, p.full_name, pr.name, LENGTH(d.raw_text)
        FROM documents d
        LEFT JOIN people p ON d.person_id = p.id
        JOIN proposals pr ON d.proposal_id = pr.id
        WHERE d.doc_type IN ('resume_historical', 'resume_generated')
        ORDER BY d.id
    """)
    print(f"{'ID':<5} {'Type':<18} {'Person':<20} {'Proposal':<35} {'TextLen'}")
    for row in cur.fetchall():
        print(f"{row[0]:<5} {row[1]:<18} {(row[2] or '-'):<20} {(row[3] or '-')[:35]:<35} {row[4]}")


def inspect(conn, doc_id):
    cur = conn.cursor()
    cur.execute("SELECT raw_text FROM documents WHERE id = ?", (doc_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        print(f"No raw_text found for document id {doc_id}.")
        return

    text = row[0]
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip()]

    print(f"Total non-empty lines: {len(lines)}\n")
    print(f"{'Line#':<6} {'IsHeader':<9} {'MappedTo':<22} {'AliasHit':<9} {'Len<=46':<9} {'EndsPunct':<10} {'RegexOK':<8} {'IsBullet':<9} {'LineLen'}")
    for i, line in enumerate(lines):
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        is_header = looks_like_header(line, next_line)
        stripped = line.strip().rstrip(":")
        alias_hit = normalize_header(stripped) is not None
        mapped = normalize_header(line) if is_header else ""
        is_bullet = bool(BULLET_PREFIX_RE.match(line.strip()))
        len_ok = len(stripped) <= 46
        ends_punct = stripped.endswith((".", ","))
        import re as _re
        regex_ok = bool(_re.match(r"^[A-Za-z][A-Za-z0-9 &/\-']{1,45}$", stripped))
        marker = "<<< HEADER" if is_header else ("<<< ALIAS BUT BLOCKED" if alias_hit and not is_header else "")
        print(f"{i:<6} {str(is_header):<9} {(mapped or ('other' if is_header else '-')):<22} {str(alias_hit):<9} {str(len_ok):<9} {str(ends_punct):<10} {str(regex_ok):<8} {str(is_bullet):<9} {len(line):<8} {marker}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="proposals.db")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--doc-id", type=int)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.list:
        list_docs(conn)
    elif args.doc_id:
        inspect(conn, args.doc_id)
    else:
        parser.print_help()
    conn.close()


if __name__ == "__main__":
    main()