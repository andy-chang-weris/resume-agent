#!/usr/bin/env python3
"""
set_requirements.py

Manually enter RFP requirements for a proposal, saved into
proposals.requirements as JSON (same schema analyze_rfp.py would have
produced, but typed in by a human instead of guessed by regex).

USAGE:
    List proposals:
        python set_requirements.py --db proposals.db --list

    Enter/update requirements interactively:
        python set_requirements.py --db proposals.db --proposal "FHWA Safety TO7 Nighttime Visibility"

    View what's currently stored:
        python set_requirements.py --db proposals.db --proposal "FHWA Safety TO7 Nighttime Visibility" --show
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys


def list_proposals(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id, name, requirements IS NOT NULL FROM proposals ORDER BY name")
    print(f"{'ID':<4} {'Has requirements':<18} Name")
    for pid, name, has_req in cur.fetchall():
        print(f"{pid:<4} {'yes' if has_req else 'no':<18} {name}")


def show(conn: sqlite3.Connection, proposal_name: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT requirements FROM proposals WHERE name = ?", (proposal_name,))
    row = cur.fetchone()
    if not row or not row[0]:
        print(f"No requirements stored yet for '{proposal_name}'.")
        return
    data = json.loads(row[0])
    print(json.dumps(data, indent=2))
    lcats = list(data.get("labor_categories", {}).keys())
    if lcats:
        print(f"\nLabor categories on file: {lcats}")


def prompt_list(label: str, existing: list | None = None) -> list:
    existing = existing or []
    if existing:
        print(f"    Current {label}: {existing}")
    raw = input(f"    Enter {label}, comma-separated (blank = keep current, 'clear' = empty): ").strip()
    if raw == "":
        return existing
    if raw.lower() == "clear":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def prompt_value(label: str, existing, cast=str):
    display = existing if existing is not None else "(none)"
    raw = input(f"    {label} [{display}]: ").strip()
    if raw == "":
        return existing
    if raw.lower() in ("none", "null", "clear"):
        return None
    try:
        return cast(raw)
    except ValueError:
        print(f"    Could not parse '{raw}' as {cast.__name__}, keeping existing value.")
        return existing


def enter_requirements(conn: sqlite3.Connection, proposal_name: str, lcat: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id, requirements FROM proposals WHERE name = ?", (proposal_name,))
    row = cur.fetchone()
    if not row:
        print(f"No proposal found with name '{proposal_name}'. Use --list to see available names.", file=sys.stderr)
        sys.exit(1)
    proposal_id, existing_json = row
    data = json.loads(existing_json) if existing_json else {}
    data.setdefault("labor_categories", {})
    data.setdefault("priority_topics", [])

    existing_lcat = data["labor_categories"].get(lcat, {})

    print(f"\nEntering requirements for: {proposal_name}")
    print(f"Labor category: {lcat}")
    print("Press Enter on any field to keep its current value (shown in brackets).\n")

    lcat_requirements = {
        "page_limit": prompt_value("Page limit (number)", existing_lcat.get("page_limit"), int),
        "required_sections": prompt_list("required sections", existing_lcat.get("required_sections")),
        "minimum_years": prompt_value("Minimum years of experience", existing_lcat.get("minimum_years"), int),
        "required_certifications": prompt_list("required certifications", existing_lcat.get("required_certifications")),
    }
    data["labor_categories"][lcat] = lcat_requirements

    print(f"\n  (Priority topics apply to the whole proposal, not just this labor category)")
    data["priority_topics"] = prompt_list("priority topics", data.get("priority_topics"))

    data["_extraction_method"] = "manual"
    data["_needs_review"] = False

    print("\nAbout to save (full proposal requirements, all labor categories):")
    print(json.dumps(data, indent=2))
    confirm = input("\nSave this? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Not saved.")
        return

    cur.execute(
        "UPDATE proposals SET requirements = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(data, indent=2), proposal_id),
    )
    conn.commit()
    print("Saved.")


def remove_lcat(conn: sqlite3.Connection, proposal_name: str, lcat: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id, requirements FROM proposals WHERE name = ?", (proposal_name,))
    row = cur.fetchone()
    if not row or not row[1]:
        print(f"No requirements stored for '{proposal_name}'.")
        return
    proposal_id, existing_json = row
    data = json.loads(existing_json)
    if lcat not in data.get("labor_categories", {}):
        print(f"Labor category '{lcat}' not found for this proposal.")
        return
    del data["labor_categories"][lcat]
    cur.execute(
        "UPDATE proposals SET requirements = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(data, indent=2), proposal_id),
    )
    conn.commit()
    print(f"Removed '{lcat}'.")


def main():
    parser = argparse.ArgumentParser(description="Manually enter RFP requirements for a proposal, per labor category.")
    parser.add_argument("--db", default="proposals.db")
    parser.add_argument("--proposal", help="Canonical proposal name (see --list)")
    parser.add_argument("--lcat", help="Labor category name to add/edit, e.g. 'Senior Communication Specialist'")
    parser.add_argument("--remove-lcat", help="Remove a labor category by name instead of editing")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    if args.list:
        list_proposals(conn)
    elif args.proposal and args.show:
        show(conn, args.proposal)
    elif args.proposal and args.remove_lcat:
        remove_lcat(conn, args.proposal, args.remove_lcat)
    elif args.proposal and args.lcat:
        enter_requirements(conn, args.proposal, args.lcat)
    elif args.proposal:
        print("Specify --lcat \"<labor category name>\" to add/edit requirements for that role.", file=sys.stderr)
        print("Or use --show to view what's already stored.", file=sys.stderr)
        sys.exit(1)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()