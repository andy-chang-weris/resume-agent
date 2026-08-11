#!/usr/bin/env python3
"""
merge_proposals.py

Merges two proposal records into one, for cases the automatic
deadline-stripping rename detection doesn't cover (e.g. the folder's
actual title changed, not just its deadline suffix). Moves all documents,
resume_facts (via those documents), and requirements from the "loser"
proposal into the "keeper", then deletes the loser. Always confirms
before making changes.

USAGE:
    python merge_proposals.py --db proposals.db --list
    python merge_proposals.py --db proposals.db --keep 1 --merge 2
"""

import argparse
import sqlite3
import sys


def list_proposals(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.name, p.folder_name,
               (SELECT COUNT(*) FROM documents d WHERE d.proposal_id = p.id) as doc_count
        FROM proposals p ORDER BY p.id
    """)
    print(f"{'ID':<4} {'Docs':<6} {'Name':<50} FolderName")
    for row in cur.fetchall():
        print(f"{row[0]:<4} {row[3]:<6} {row[1]:<50} {row[2]}")


def merge(conn, keep_id, merge_id):
    if keep_id == merge_id:
        print("Cannot merge a proposal into itself.")
        return

    cur = conn.cursor()
    cur.execute("SELECT name, folder_name FROM proposals WHERE id = ?", (keep_id,))
    keep_row = cur.fetchone()
    cur.execute("SELECT name, folder_name FROM proposals WHERE id = ?", (merge_id,))
    merge_row = cur.fetchone()

    if not keep_row or not merge_row:
        print("One or both proposal IDs not found. Use --list to check.")
        return

    print(f"Keep:  [{keep_id}] {keep_row[0]}  (folder: {keep_row[1]})")
    print(f"Merge: [{merge_id}] {merge_row[0]}  (folder: {merge_row[1]})")
    print(f"\nThis will move all documents/facts from proposal {merge_id} into {keep_id},")
    print(f"then DELETE proposal {merge_id}. This cannot be undone.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Not merged.")
        return

    cur.execute("UPDATE documents SET proposal_id = ? WHERE proposal_id = ?", (keep_id, merge_id))

    # If the keeper has no requirements yet but the merged one does, carry them over.
    cur.execute("SELECT requirements FROM proposals WHERE id = ?", (keep_id,))
    keep_requirements = cur.fetchone()[0]
    if not keep_requirements:
        cur.execute("SELECT requirements FROM proposals WHERE id = ?", (merge_id,))
        merge_requirements = cur.fetchone()[0]
        if merge_requirements:
            cur.execute("UPDATE proposals SET requirements = ? WHERE id = ?", (merge_requirements, keep_id))
            print("Carried over requirements from the merged proposal (keeper had none).")

    cur.execute("SELECT rfp_metadata FROM proposals WHERE id = ?", (keep_id,))
    keep_meta = cur.fetchone()[0]
    if not keep_meta:
        cur.execute("SELECT rfp_metadata FROM proposals WHERE id = ?", (merge_id,))
        merge_meta = cur.fetchone()[0]
        if merge_meta:
            cur.execute("UPDATE proposals SET rfp_metadata = ? WHERE id = ?", (merge_meta, keep_id))
            print("Carried over RFP metadata from the merged proposal (keeper had none).")

    cur.execute("DELETE FROM proposals WHERE id = ?", (merge_id,))
    conn.commit()
    print(f"Merged. Proposal {merge_id} removed; its documents now belong to {keep_id}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="proposals.db")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--keep", type=int, help="Proposal ID to keep")
    parser.add_argument("--merge", type=int, help="Proposal ID to merge into --keep, then delete")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.list:
        list_proposals(conn)
    elif args.keep and args.merge:
        merge(conn, args.keep, args.merge)
    else:
        parser.print_help()
    conn.close()


if __name__ == "__main__":
    main()