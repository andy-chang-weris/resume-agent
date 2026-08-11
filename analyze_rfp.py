#!/usr/bin/env python3
"""
analyze_rfp.py

Rule-based (no AI/ML) extraction of structured requirements from an RFP's
text, per the proposal MVP doc's requirements shape:

    {
      "page_limit": 3,
      "required_sections": [...],
      "labor_category": "...",
      "minimum_years": 10,
      "required_certifications": [...],
      "priority_topics": [...]
    }

This uses keyword/regex matching against the RFP document's raw_text
already stored in the database by ingest.py. It is intentionally
conservative: fields it can't confidently extract are left null/empty
rather than guessed. Always review the output against the actual RFP
before treating it as authoritative -- this is a starting point for a
human to confirm or correct, not a finished analysis.

USAGE:
    List proposals with RFPs ingested:
        python analyze_rfp.py --db proposals.db --list

    Analyze one proposal (matches on proposals.name, the canonical name
    without the deadline):
        python analyze_rfp.py --db proposals.db --proposal "BTS TO31 Program Support"

    Review what's stored without re-running analysis:
        python analyze_rfp.py --db proposals.db --proposal "BTS TO31 Program Support" --show
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys

KNOWN_CERTS = [
    "PMP", "AWS Certified Solutions Architect", "AWS Certified", "CISSP",
    "Security+", "CISA", "CISM", "ITIL", "Scrum Master", "CSM", "PgMP",
    "Six Sigma", "CCNA", "Azure Administrator", "Azure Solutions Architect",
]

COMMON_SECTIONS = [
    "Education", "Certifications", "Relevant Experience", "Experience",
    "Qualifications", "Skills", "Professional Experience", "Summary",
    "Clearance", "Training",
]

PAGE_LIMIT_RE = re.compile(
    r"(?:page\s*limit|not\s+to\s+exceed|no\s+more\s+than|maximum\s+of)\D{0,15}(\d{1,2})\s*page",
    re.IGNORECASE,
)

MIN_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:relevant\s+)?experience",
    re.IGNORECASE,
)

LABOR_CATEGORY_RE = re.compile(
    r"(?:labor\s+category|position\s+title|key\s+personnel\s+role)\s*[:\-]?\s*([A-Z][A-Za-z0-9 /,]{3,60})",
    re.IGNORECASE,
)


def extract_requirements(text: str) -> dict:
    result = {
        "page_limit": None,
        "required_sections": [],
        "labor_category": None,
        "minimum_years": None,
        "required_certifications": [],
        "priority_topics": [],
        "_extraction_method": "rule_based_v1",
        "_needs_review": True,
    }

    page_match = PAGE_LIMIT_RE.search(text)
    if page_match:
        result["page_limit"] = int(page_match.group(1))

    years_match = MIN_YEARS_RE.search(text)
    if years_match:
        result["minimum_years"] = int(years_match.group(1))

    labor_match = LABOR_CATEGORY_RE.search(text)
    if labor_match:
        result["labor_category"] = labor_match.group(1).strip()

    for cert in KNOWN_CERTS:
        if cert.lower() in text.lower() and cert not in result["required_certifications"]:
            result["required_certifications"].append(cert)

    for section in COMMON_SECTIONS:
        if re.search(rf"\b{re.escape(section)}\b", text, re.IGNORECASE):
            result["required_sections"].append(section)

    # priority_topics intentionally left empty -- identifying genuine
    # thematic priorities from prose is not reliable with keyword rules;
    # do not guess here.

    return result


def list_proposals(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.name, p.requirements IS NOT NULL,
               (SELECT COUNT(*) FROM documents d WHERE d.proposal_id = p.id AND d.doc_type = 'rfp')
        FROM proposals p ORDER BY p.name
    """)
    print(f"{'ID':<4} {'Has requirements':<18} {'RFP docs':<9} Name")
    for pid, name, has_req, rfp_count in cur.fetchall():
        print(f"{pid:<4} {'yes' if has_req else 'no':<18} {rfp_count:<9} {name}")


def analyze(conn: sqlite3.Connection, proposal_name: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM proposals WHERE name = ?", (proposal_name,))
    row = cur.fetchone()
    if not row:
        print(f"No proposal found with name '{proposal_name}'. Use --list to see available names.", file=sys.stderr)
        sys.exit(1)
    proposal_id = row[0]

    cur.execute(
        "SELECT id, raw_text FROM documents WHERE proposal_id = ? AND doc_type = 'rfp'",
        (proposal_id,),
    )
    rfp_docs = cur.fetchall()
    if not rfp_docs:
        print(f"No RFP document found for proposal '{proposal_name}'.", file=sys.stderr)
        sys.exit(1)

    combined_text = "\n".join(text or "" for _, text in rfp_docs)
    if not combined_text.strip():
        print("RFP document(s) found but no text was extracted (check pdfplumber/python-docx install).", file=sys.stderr)
        sys.exit(1)

    requirements = extract_requirements(combined_text)

    cur.execute(
        "UPDATE proposals SET requirements = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(requirements, indent=2), proposal_id),
    )
    conn.commit()

    print(f"Requirements extracted for '{proposal_name}' (review before trusting):\n")
    print(json.dumps(requirements, indent=2))


def show(conn: sqlite3.Connection, proposal_name: str) -> None:
    cur = conn.cursor()
    cur.execute("SELECT requirements FROM proposals WHERE name = ?", (proposal_name,))
    row = cur.fetchone()
    if not row or not row[0]:
        print(f"No stored requirements for '{proposal_name}'. Run without --show first.")
        return
    print(row[0])


def main():
    parser = argparse.ArgumentParser(description="Rule-based RFP requirements extraction (no AI/ML).")
    parser.add_argument("--db", default="proposals.db")
    parser.add_argument("--proposal", help="Canonical proposal name (see --list)")
    parser.add_argument("--list", action="store_true", help="List proposals and whether requirements exist")
    parser.add_argument("--show", action="store_true", help="Show stored requirements without re-analyzing")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    if args.list:
        list_proposals(conn)
    elif args.proposal and args.show:
        show(conn, args.proposal)
    elif args.proposal:
        analyze(conn, args.proposal)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()