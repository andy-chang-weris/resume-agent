#!/usr/bin/env python3
"""
ingest.py

Ingests a locally-synced folder of proposal documents (originating from
SharePoint, synced down by you/your team through your normal SharePoint
sync tools -- this script does not talk to SharePoint itself) into a local
SQLite database, following the 5-table schema: proposals, documents,
people, resume_facts, templates.

No AI/ML. Fact extraction here is intentionally simple and rule-based
(regex/keyword matching for certifications, education, employment dates).
It is a starting point meant to be reviewed and corrected, not trusted
blindly -- flag this clearly to reviewers.

EXPECTED FOLDER LAYOUT (matches your SharePoint structure; adjust
DOC_TYPE_BY_FOLDER below if yours differs):

    <root>/
      AGENCY TO# RFP NAME/            <- one folder per proposal/Task Order
        Solicitation/
          rfp.pdf
        Resumes/
          Historical Resumes/         <- past resumes for this or other TOs
            Jane_Doe.docx
          Generated Resumes/          <- resumes this tool has produced
            Jane_Doe_v1.docx

OPTIONAL SHAREPOINT MANIFEST:
If you have a manifest mapping local files to their SharePoint URLs
(e.g. exported from SharePoint or maintained by hand), pass it with
--manifest. Format: a JSON file of {"local_relative_path": "https://..."}.
Without a manifest, sharepoint_url is left NULL and can be filled in later.

USAGE:
    python ingest.py /path/to/local/proposals --db proposals.db
    python ingest.py /path/to/local/proposals --db proposals.db --manifest sharepoint_urls.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx as docx_lib
except ImportError:
    docx_lib = None


SUPPORTED_TEXT_EXT = {".pdf", ".docx", ".txt"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id              INTEGER PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,   -- canonical name, deadline stripped
    folder_name     TEXT,                    -- most recent raw folder name seen (with deadline)
    status          TEXT DEFAULT 'active',
    requirements    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS people (
    id              INTEGER PRIMARY KEY,
    full_name       TEXT UNIQUE NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY,
    proposal_id        INTEGER REFERENCES proposals(id),
    person_id          INTEGER REFERENCES people(id),
    doc_type           TEXT NOT NULL,        -- 'rfp' | 'resume_historical' | 'resume_generated'

    source_system      TEXT DEFAULT 'sharepoint',  -- 'sharepoint' | 'local_only'
    sharepoint_url      TEXT,
    local_cache_path    TEXT UNIQUE NOT NULL,
    cache_synced_at     TEXT,
    cache_stale         INTEGER DEFAULT 0,

    file_ext           TEXT,
    file_size           INTEGER,
    mtime               REAL,
    raw_text            TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS resume_facts (
    id                  INTEGER PRIMARY KEY,
    person_id           INTEGER NOT NULL REFERENCES people(id),
    fact_type           TEXT NOT NULL,   -- 'education' | 'certification' |
                                          -- 'project_experience' | 'employment' |
                                          -- 'technology' | 'responsibility' | 'unclassified'
    fact_text           TEXT NOT NULL,
    start_date           TEXT,
    end_date             TEXT,
    source_document_id   INTEGER NOT NULL REFERENCES documents(id),
    source_section        TEXT,
    conflict_flag         INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS templates (
    id                          INTEGER PRIMARY KEY,
    proposal_id                 INTEGER NOT NULL REFERENCES proposals(id),
    version                     INTEGER DEFAULT 1,
    status                      TEXT DEFAULT 'draft',
    page_limit                  INTEGER,
    structure                   TEXT,
    source_resume_document_id   INTEGER REFERENCES documents(id),
    approved_at                 TEXT,
    created_at                  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resume_facts_person ON resume_facts(person_id);
CREATE INDEX IF NOT EXISTS idx_resume_facts_type ON resume_facts(person_id, fact_type);
CREATE INDEX IF NOT EXISTS idx_documents_proposal ON documents(proposal_id);
CREATE INDEX IF NOT EXISTS idx_documents_person ON documents(person_id);
CREATE INDEX IF NOT EXISTS idx_templates_proposal ON templates(proposal_id);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    raw_text, content='documents', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, raw_text) VALUES (new.id, new.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_text) VALUES ('delete', old.id, old.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_text) VALUES ('delete', old.id, old.raw_text);
    INSERT INTO documents_fts(rowid, raw_text) VALUES (new.id, new.raw_text);
END;
"""

DOC_TYPE_BY_FOLDER = {
    "Solicitation": "rfp",
    "Resumes/Historical Resumes": "resume_historical",
    "Resumes/Generated Resumes": "resume_generated",
}

# Known certifications to look for -- extend this list as needed.
KNOWN_CERTS = [
    "PMP", "AWS Certified Solutions Architect", "AWS Certified", "CISSP",
    "Security+", "CISA", "CISM", "ITIL", "Scrum Master", "CSM", "PgMP",
    "Six Sigma", "CCNA", "Azure Administrator", "Azure Solutions Architect",
]

DEGREE_KEYWORDS = [
    "Bachelor of", "Master of", "B.S.", "M.S.", "MBA", "Ph.D.", "PhD",
    "Bachelor's", "Master's", "Associate of",
]

DATE_RANGE_RE = re.compile(
    r"(\b(?:19|20)\d{2}\b)\s*[-\u2013\u2014to]{1,4}\s*(\b(?:19|20)\d{2}\b|present|current)",
    re.IGNORECASE,
)

# Strips a trailing "(...)" from a proposal folder name, e.g.
# "BTS TO31 Program Support (Aug 18 3pm)" -> "BTS TO31 Program Support"
# This is what most of your folder names use to hold the current deadline,
# which changes over time -- the canonical proposal name must NOT include it,
# or a deadline update would look like a brand new proposal.
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


def canonicalize_proposal_name(folder_name: str) -> str:
    canonical = TRAILING_PAREN_RE.sub("", folder_name).strip()
    return canonical or folder_name


@dataclass
class FileRecord:
    path: Path
    proposal_name: str
    doc_type: str
    person_name: str | None


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        if ext == ".pdf":
            if pdfplumber is None:
                return ""
            with pdfplumber.open(path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        if ext == ".docx":
            if docx_lib is None:
                return ""
            document = docx_lib.Document(str(path))
            return "\n".join(p.text for p in document.paragraphs)
    except Exception as exc:
        print(f"  [warn] could not extract text from {path}: {exc}", file=sys.stderr)
    return ""


def guess_person_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"(?i)[\s_-]*(formatted|final|raw|resume|cv|v\d+|draft)+$", "", stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem or filename


def _walk_files(folder: Path):
    if not folder.exists():
        return
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix.lower() in SUPPORTED_TEXT_EXT:
            yield f


def classify_and_collect(root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    for proposal_folder in sorted(p for p in root.iterdir() if p.is_dir()):
        proposal_name = proposal_folder.name

        for folder_name, doc_type in DOC_TYPE_BY_FOLDER.items():
            subfolder = proposal_folder / folder_name
            for f in _walk_files(subfolder):
                person_name = guess_person_name(f.name) if doc_type != "rfp" else None
                records.append(FileRecord(f, proposal_name, doc_type, person_name))

        for f in proposal_folder.iterdir():
            if f.is_file() and f.suffix.lower() in SUPPORTED_TEXT_EXT:
                records.append(FileRecord(f, proposal_name, "rfp", None))

    return records


def extract_facts(text: str) -> list[dict]:
    """Very simple rule-based fact extraction. Meant to be reviewed, not
    trusted as-is. Splits text into lines/sentences and tags a subset
    using keyword and regex matching; everything else is stored as
    'unclassified' so no source content is silently dropped.
    """
    facts: list[dict] = []
    lines = [ln.strip() for ln in re.split(r"[\n.]", text) if ln.strip()]

    for line in lines:
        matched = False

        for cert in KNOWN_CERTS:
            if cert.lower() in line.lower():
                facts.append({"fact_type": "certification", "fact_text": line,
                               "start_date": None, "end_date": None})
                matched = True
                break
        if matched:
            continue

        if any(kw.lower() in line.lower() for kw in DEGREE_KEYWORDS):
            facts.append({"fact_type": "education", "fact_text": line,
                           "start_date": None, "end_date": None})
            continue

        date_match = DATE_RANGE_RE.search(line)
        if date_match and len(line) > 15:
            facts.append({
                "fact_type": "employment",
                "fact_text": line,
                "start_date": date_match.group(1),
                "end_date": date_match.group(2),
            })
            continue

        if len(line) > 40:
            facts.append({"fact_type": "unclassified", "fact_text": line,
                           "start_date": None, "end_date": None})

    return facts


def get_or_create(cur: sqlite3.Cursor, table: str, key_col: str, key_val: str) -> int:
    cur.execute(f"SELECT id FROM {table} WHERE {key_col} = ?", (key_val,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(f"INSERT INTO {table} ({key_col}) VALUES (?)", (key_val,))
    return cur.lastrowid


def get_or_create_proposal(cur: sqlite3.Cursor, raw_folder_name: str) -> int:
    canonical = canonicalize_proposal_name(raw_folder_name)
    cur.execute("SELECT id, folder_name FROM proposals WHERE name = ?", (canonical,))
    row = cur.fetchone()
    if row:
        proposal_id, current_folder_name = row
        if current_folder_name != raw_folder_name:
            # deadline (or other trailing detail) changed since last ingest --
            # update the stored raw folder name but keep the same proposal row
            cur.execute(
                "UPDATE proposals SET folder_name = ?, updated_at = datetime('now') WHERE id = ?",
                (raw_folder_name, proposal_id),
            )
        return proposal_id
    cur.execute(
        "INSERT INTO proposals (name, folder_name) VALUES (?, ?)",
        (canonical, raw_folder_name),
    )
    return cur.lastrowid


def ingest(root: Path, db_path: Path, manifest_path: Path | None) -> None:
    manifest = {}
    if manifest_path and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()

    records = classify_and_collect(root)
    if not records:
        print(f"No matching files found under {root}. Check your folder layout.")
        return

    inserted, updated, skipped, facts_created = 0, 0, 0, 0

    for rec in records:
        stat = rec.path.stat()
        abs_path = str(rec.path.resolve())
        rel_path = str(rec.path.relative_to(root))

        cur.execute(
            "SELECT id, file_size, mtime FROM documents WHERE local_cache_path = ?", (abs_path,)
        )
        existing = cur.fetchone()

        if existing and existing[1] == stat.st_size and abs(existing[2] - stat.st_mtime) < 1:
            skipped += 1
            continue

        proposal_id = get_or_create_proposal(cur, rec.proposal_name)
        person_id = get_or_create(cur, "people", "full_name", rec.person_name) if rec.person_name else None
        text = extract_text(rec.path)
        sharepoint_url = manifest.get(rel_path)

        if existing:
            doc_id = existing[0]
            cur.execute(
                """UPDATE documents
                   SET proposal_id=?, person_id=?, doc_type=?, sharepoint_url=?,
                       cache_synced_at=datetime('now'), file_ext=?, file_size=?,
                       mtime=?, raw_text=?, updated_at=datetime('now')
                   WHERE id=?""",
                (proposal_id, person_id, rec.doc_type, sharepoint_url,
                 rec.path.suffix.lower(), stat.st_size, stat.st_mtime, text, doc_id),
            )
            cur.execute("DELETE FROM resume_facts WHERE source_document_id = ?", (doc_id,))
            updated += 1
        else:
            cur.execute(
                """INSERT INTO documents
                   (proposal_id, person_id, doc_type, source_system, sharepoint_url,
                    local_cache_path, cache_synced_at, file_ext, file_size, mtime, raw_text)
                   VALUES (?, ?, ?, 'sharepoint', ?, ?, datetime('now'), ?, ?, ?, ?)""",
                (proposal_id, person_id, rec.doc_type, sharepoint_url, abs_path,
                 rec.path.suffix.lower(), stat.st_size, stat.st_mtime, text),
            )
            doc_id = cur.lastrowid
            inserted += 1

        if rec.doc_type in ("resume_historical", "resume_generated") and person_id:
            for fact in extract_facts(text):
                cur.execute(
                    """INSERT INTO resume_facts
                       (person_id, fact_type, fact_text, start_date, end_date,
                        source_document_id, source_section)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (person_id, fact["fact_type"], fact["fact_text"],
                     fact["start_date"], fact["end_date"], doc_id, None),
                )
                facts_created += 1

    conn.commit()
    conn.close()

    print(f"Done. Documents inserted: {inserted}, updated: {updated}, skipped: {skipped}")
    print(f"Facts extracted: {facts_created} (rule-based -- review before trusting)")
    print(f"Database: {db_path.resolve()}")
    if not manifest:
        print("[note] No SharePoint manifest provided -- sharepoint_url left NULL for all documents.")


def cleanup_missing_files(conn: sqlite3.Connection) -> int:
    """Mark documents whose local_cache_path no longer exists on disk.
    Does not delete rows (facts/history stay intact) -- just flags them
    so you know the local cache is stale after a folder rename or move.
    """
    cur = conn.cursor()
    cur.execute("SELECT id, local_cache_path FROM documents WHERE cache_stale = 0")
    marked = 0
    for doc_id, path in cur.fetchall():
        if not Path(path).exists():
            cur.execute("UPDATE documents SET cache_stale = 1 WHERE id = ?", (doc_id,))
            marked += 1
    conn.commit()
    return marked


def main():
    parser = argparse.ArgumentParser(
        description="Ingest a locally-synced copy of SharePoint proposal folders into SQLite."
    )
    parser.add_argument("root", type=Path, help="Root folder containing one subfolder per proposal")
    parser.add_argument("--db", type=Path, default=Path("proposals.db"), help="Path to SQLite DB file")
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="Optional JSON file mapping local relative paths to SharePoint URLs",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="After ingesting, mark any documents whose local_cache_path no longer "
             "exists (e.g. due to a folder rename) as cache_stale=1",
    )
    args = parser.parse_args()

    if not args.root.exists() or not args.root.is_dir():
        print(f"Error: {args.root} is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    if pdfplumber is None:
        print("[warn] pdfplumber not installed; PDF text will not be extracted.", file=sys.stderr)
    if docx_lib is None:
        print("[warn] python-docx not installed; DOCX text will not be extracted.", file=sys.stderr)

    ingest(args.root, args.db, args.manifest)

    if args.cleanup:
        conn = sqlite3.connect(args.db)
        marked = cleanup_missing_files(conn)
        conn.close()
        print(f"Cleanup: marked {marked} document(s) as cache_stale (local file no longer found).")


if __name__ == "__main__":
    main()