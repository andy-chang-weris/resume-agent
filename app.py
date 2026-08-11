#!/usr/bin/env python3
"""
app.py

Local web UI for browsing and searching proposals.db. No AI/ML, no
external network calls -- just SQLite queries surfaced through a simple
Flask app so you don't have to write Python one-liners to look things up.

USAGE:
    python app.py --db proposals.db
    (then open http://127.0.0.1:5000 in your browser)

WHAT IT DOES:
    - Browse proposals and their labor-category requirements
    - Browse personnel and every document/proposal they appear in
    - Keyword search across all resume/RFP text (SQLite FTS5)
    - "Match personnel to LCAT" -- for a given proposal + labor category,
      ranks personnel by how many of that LCAT's required_certifications
      and priority_topics keywords appear in their historical resume text.
      This is a keyword-overlap heuristic, not a judgment of fit -- always
      review the actual resume before deciding someone matches.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from flask import Flask, g, render_template_string, request

app = Flask(__name__)
DB_PATH = "proposals.db"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


BASE = """
<!doctype html>
<html>
<head>
<title>Proposal Resume Database</title>
<style>
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; max-width: 960px; margin: 30px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 1.4em; } h2 { font-size: 1.15em; margin-top: 1.5em; }
  nav a { margin-right: 16px; color: #0b5ed7; text-decoration: none; font-size: 0.95em; }
  nav { margin-bottom: 24px; border-bottom: 1px solid #ddd; padding-bottom: 12px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; font-size: 0.92em; vertical-align: top; }
  th { background: #f7f7f7; }
  .tag { display: inline-block; background: #eef; border-radius: 3px; padding: 1px 7px; margin: 2px; font-size: 0.85em; }
  .snippet { color: #555; font-size: 0.88em; }
  .warn { color: #a15c00; background: #fff6e5; padding: 8px 12px; border-radius: 4px; font-size: 0.88em; }
  input[type=text] { padding: 6px 10px; width: 320px; font-size: 0.95em; }
  button { padding: 6px 14px; font-size: 0.95em; }
  .score { font-weight: bold; color: #0b5ed7; }
  a.name { text-decoration: none; color: #0b5ed7; }
</style>
</head>
<body>
<nav>
  <a href="/">Proposals</a>
  <a href="/people">Personnel</a>
  <a href="/people/search">Search Personnel</a>
  <a href="/search">Search Documents</a>
</nav>
{{ content|safe }}
</body>
</html>
"""


import html as html_lib


def _escape(text: str) -> str:
    return html_lib.escape(text)


def render(content_html: str) -> str:
    return render_template_string(BASE, content=content_html)


@app.route("/")
def proposals():
    db = get_db()
    rows = db.execute("""
        SELECT p.id, p.name, p.folder_name, p.requirements,
               (SELECT COUNT(*) FROM documents d WHERE d.proposal_id = p.id) as doc_count
        FROM proposals p ORDER BY p.name
    """).fetchall()

    html = "<h1>Proposals</h1><table><tr><th>Name</th><th>Documents</th><th>Labor Categories</th></tr>"
    for r in rows:
        lcats = []
        if r["requirements"]:
            data = json.loads(r["requirements"])
            lcats = list(data.get("labor_categories", {}).keys())
        lcat_html = "".join(f'<span class="tag">{l}</span>' for l in lcats) or "<em>none entered</em>"
        html += f'<tr><td><a class="name" href="/proposal/{r["id"]}">{r["name"]}</a></td><td>{r["doc_count"]}</td><td>{lcat_html}</td></tr>'
    html += "</table>"
    return render(html)


@app.route("/proposal/<int:proposal_id>/metadata/update", methods=["POST"])
def update_rfp_metadata(proposal_id):
    db = get_db()
    fields = [
        "solicitation_number", "contract_type", "contracting_officer_name",
        "contracting_officer_email", "contracting_officer_phone",
    ]
    metadata = {f: (request.form.get(f, "").strip() or None) for f in fields}
    metadata["_extraction_method"] = "manual"
    metadata["_needs_review"] = False

    db.execute(
        "UPDATE proposals SET rfp_metadata = ?, rfp_metadata_manually_edited = 1, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(metadata, indent=2), proposal_id),
    )
    db.commit()
    return proposal_detail(proposal_id)


@app.route("/proposal/<int:proposal_id>")
def proposal_detail(proposal_id):
    db = get_db()
    p = db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if not p:
        return render("<p>Proposal not found.</p>")

    docs = db.execute(
        "SELECT d.*, pe.full_name FROM documents d LEFT JOIN people pe ON d.person_id = pe.id WHERE d.proposal_id = ? ORDER BY d.doc_type, pe.full_name",
        (proposal_id,),
    ).fetchall()

    html = f'<h1>{p["name"]}</h1>'
    html += f'<p class="snippet">Folder: {p["folder_name"] or "(unknown)"}</p>'

    meta = json.loads(p["rfp_metadata"]) if p["rfp_metadata"] else {}
    meta_fields = [
        ("solicitation_number", "Solicitation / TORFP #"),
        ("contract_type", "Contract Type"),
        ("contracting_officer_name", "Contracting Officer"),
        ("contracting_officer_email", "CO Email"),
        ("contracting_officer_phone", "CO Phone"),
    ]

    html += '<h2>RFP Details</h2>'
    if meta.get("_needs_review", True) and meta.get("_extraction_method") != "manual":
        html += '<p class="warn">Auto-extracted from the RFP text (rule-based, not guaranteed accurate) -- review and correct below if needed.</p>'
    html += '<form method="POST" action="/proposal/' + str(proposal_id) + '/metadata/update"><table>'
    for field_key, label in meta_fields:
        value = meta.get(field_key) or ""
        html += f'<tr><th>{label}</th><td><input type="text" name="{field_key}" value="{_escape(value)}" style="width:100%;"></td></tr>'
    html += '</table><button type="submit">Save RFP Details</button></form>'

    if p["requirements"]:
        data = json.loads(p["requirements"])
        lcats = data.get("labor_categories", {})
        if lcats:
            html += "<h2>Labor Category Requirements</h2>"
            for lcat, req in lcats.items():
                html += f"<h3>{lcat}</h3><table>"
                html += f'<tr><th>Page limit</th><td>{req.get("page_limit","-")}</td></tr>'
                html += f'<tr><th>Min years</th><td>{req.get("minimum_years","-")}</td></tr>'
                html += f'<tr><th>Required sections</th><td>{", ".join(req.get("required_sections") or []) or "-"}</td></tr>'
                html += f'<tr><th>Required certs</th><td>{", ".join(req.get("required_certifications") or []) or "-"}</td></tr>'
                html += f'<tr><th>Priority topics</th><td>{", ".join(req.get("priority_topics") or []) or "-"}</td></tr>'
                html += "</table>"
                html += f'<a href="/match/{proposal_id}/{lcat}">Find matching personnel for this role &rarr;</a><br><br>'
        else:
            html += '<p class="warn">No labor category requirements entered yet. Use set_requirements.py to add them.</p>'
    else:
        html += '<p class="warn">No requirements entered yet. Use set_requirements.py to add them.</p>'

    html += "<h2>Documents</h2><table><tr><th>Type</th><th>Person</th><th>File</th></tr>"
    for d in docs:
        html += f'<tr><td>{d["doc_type"]}</td><td>{d["full_name"] or "-"}</td><td class="snippet">{Path(d["local_cache_path"]).name}</td></tr>'
    html += "</table>"

    return render(html)


@app.route("/people")
def people():
    db = get_db()
    rows = db.execute("""
        SELECT pe.id, pe.full_name, COUNT(DISTINCT d.proposal_id) as proposal_count
        FROM people pe LEFT JOIN documents d ON d.person_id = pe.id
        GROUP BY pe.id ORDER BY pe.full_name
    """).fetchall()
    html = "<h1>Personnel</h1><table><tr><th>Name</th><th>Appears in # proposals</th></tr>"
    for r in rows:
        html += f'<tr><td><a class="name" href="/person/{r["id"]}">{r["full_name"]}</a></td><td>{r["proposal_count"]}</td></tr>'
    html += "</table>"
    return render(html)


FACT_TYPES = ["summary", "education", "certification", "employment", "skills", "years_of_experience", "other"]


@app.route("/fact/<int:fact_id>/update", methods=["POST"])
def update_fact(fact_id):
    db = get_db()
    fact_type = request.form.get("fact_type", "").strip()
    fact_text = request.form.get("fact_text", "").strip()
    person_id = request.form.get("person_id")

    if fact_type not in FACT_TYPES or not fact_text:
        return render('<p class="warn">Invalid update -- fact_type or fact_text was empty/invalid.</p>')

    db.execute(
        "UPDATE resume_facts SET fact_type = ?, fact_text = ?, manually_edited = 1 WHERE id = ?",
        (fact_type, fact_text, fact_id),
    )
    db.commit()
    return person_detail(int(person_id))


@app.route("/fact/<int:fact_id>/delete", methods=["POST"])
def delete_fact(fact_id):
    db = get_db()
    person_id = request.form.get("person_id")
    db.execute("DELETE FROM resume_facts WHERE id = ?", (fact_id,))
    db.commit()
    return person_detail(int(person_id))


@app.route("/person/<int:person_id>")
def person_detail(person_id):
    db = get_db()
    person = db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if not person:
        return render("<p>Person not found.</p>")

    docs = db.execute("""
        SELECT d.*, p.name as proposal_name FROM documents d
        JOIN proposals p ON d.proposal_id = p.id
        WHERE d.person_id = ? ORDER BY p.name, d.doc_type
    """, (person_id,)).fetchall()

    html = f'<h1>{person["full_name"]}</h1><h2>Resumes on file</h2>'

    for d in docs:
        facts = db.execute(
            "SELECT * FROM resume_facts WHERE source_document_id = ? ORDER BY fact_type",
            (d["id"],),
        ).fetchall()
        raw_text = d["raw_text"] or "(no text extracted)"

        html += f'<h3>{d["proposal_name"]} &mdash; {d["doc_type"]}</h3>'
        html += f'<p class="snippet">File: {Path(d["local_cache_path"]).name}</p>'

        html += '<details><summary>Raw extracted text</summary>'
        html += f'<pre style="white-space: pre-wrap; background:#f7f7f7; padding:10px; border-radius:4px; max-height:400px; overflow:auto;">{_escape(raw_text)}</pre>'
        html += '</details>'

        html += f'<details open><summary>Resume facts ({len(facts)}) &mdash; click to edit</summary>'
        html += '<table><tr><th>Type</th><th>Text</th><th>Edited?</th><th></th></tr>'
        for f in facts:
            options_html = "".join(
                f'<option value="{ft}"{" selected" if ft == f["fact_type"] else ""}>{ft}</option>'
                for ft in FACT_TYPES
            )
            edited_badge = '<span class="tag">edited</span>' if f["manually_edited"] else ""
            html += (
                f'<tr>'
                f'<td><form method="POST" action="/fact/{f["id"]}/update" style="display:flex; gap:4px; align-items:flex-start;">'
                f'<input type="hidden" name="person_id" value="{person_id}">'
                f'<select name="fact_type">{options_html}</select>'
                f'</td>'
                f'<td><textarea name="fact_text" rows="2" style="width:100%; font-size:0.88em;">{_escape(f["fact_text"])}</textarea></td>'
                f'<td>{edited_badge}</td>'
                f'<td><button type="submit">Save</button></form>'
                f'<form method="POST" action="/fact/{f["id"]}/delete" style="display:inline;" onsubmit="return confirm(\'Delete this fact?\');">'
                f'<input type="hidden" name="person_id" value="{person_id}">'
                f'<button type="submit">Delete</button></form></td>'
                f'</tr>'
            )
        html += "</table></details><br>"

    return render(html)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    html = "<h1>Search</h1><form><input type=text name=q value='" + q.replace("'", "") + "' placeholder='e.g. AWS migration, PMP, federal outreach'> <button>Search</button></form>"

    if q:
        db = get_db()
        try:
            rows = db.execute("""
                SELECT d.id, d.doc_type, d.local_cache_path, d.proposal_id, pe.full_name, p.name as proposal_name,
                       snippet(documents_fts, 0, '<b>', '</b>', '...', 12) as snip
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                LEFT JOIN people pe ON d.person_id = pe.id
                JOIN proposals p ON d.proposal_id = p.id
                WHERE documents_fts MATCH ?
                LIMIT 40
            """, (q,)).fetchall()
        except sqlite3.OperationalError as e:
            html += f'<p class="warn">Search error: {e}. Try simpler terms (FTS5 has special-character rules).</p>'
            return render(html)

        html += f"<p>{len(rows)} result(s)</p><table><tr><th>Person</th><th>Proposal</th><th>Type</th><th>Match</th></tr>"
        for r in rows:
            html += f'<tr><td>{r["full_name"] or "-"}</td><td><a class="name" href="/proposal/{r["proposal_id"]}">{r["proposal_name"]}</a></td><td>{r["doc_type"]}</td><td class="snippet">{r["snip"]}</td></tr>'
        html += "</table>"

    return render(html)


@app.route("/people/search")
def people_search():
    q = request.args.get("q", "").strip()
    html = "<h1>Search Personnel by Skills/Keywords</h1>"
    html += "<p class=\"snippet\">Search directly across all historical resumes without needing a stored proposal/LCAT first.</p>"
    html += "<form><input type=text name=q value='" + q.replace("'", "") + "' placeholder='e.g. PMP, AWS, stakeholder engagement'> <button>Search</button></form>"

    if q:
        keywords = [kw.strip() for kw in q.split(",") if kw.strip()]
        html += f'<p class="snippet">Matching against: {", ".join(keywords)}</p>'

        db = get_db()
        people_rows = db.execute("SELECT id, full_name FROM people ORDER BY full_name").fetchall()

        scored = []
        for person in people_rows:
            docs = db.execute(
                "SELECT raw_text FROM documents WHERE person_id = ? AND doc_type = 'resume_historical'",
                (person["id"],),
            ).fetchall()
            combined_text = " ".join((d["raw_text"] or "") for d in docs).lower()
            if not combined_text:
                continue
            matched_kw = [kw for kw in keywords if kw.lower() in combined_text]
            if matched_kw:
                scored.append((person, matched_kw))

        scored.sort(key=lambda x: len(x[1]), reverse=True)

        html += '<p class="warn">Keyword-overlap match against historical resume text, not a judgment of fit. Review the actual resume before deciding someone qualifies.</p>'
        html += "<table><tr><th>Person</th><th>Score</th><th>Matched keywords</th></tr>"
        for person, matched_kw in scored:
            html += f'<tr><td><a class="name" href="/person/{person["id"]}">{person["full_name"]}</a></td>'
            html += f'<td class="score">{len(matched_kw)}/{len(keywords)}</td>'
            html += f'<td>{", ".join(matched_kw)}</td></tr>'
        html += "</table>"

        if not scored:
            html += "<p>No personnel matched any of the given keywords.</p>"

    return render(html)


@app.route("/match/<int:proposal_id>/<lcat>")
def match(proposal_id, lcat):
    db = get_db()
    p = db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if not p or not p["requirements"]:
        return render("<p>No requirements found for this proposal.</p>")

    data = json.loads(p["requirements"])
    req = data.get("labor_categories", {}).get(lcat)
    if not req:
        return render(f"<p>Labor category '{lcat}' not found for this proposal.</p>")

    keywords = list(req.get("required_certifications") or []) + list(req.get("priority_topics") or [])

    html = f'<h1>Matches for "{lcat}"</h1><p class="snippet">Proposal: {p["name"]}</p>'
    if not keywords:
        html += '<p class="warn">No certifications or priority topics entered for this role, so nothing to match against. Add them via set_requirements.py.</p>'
        return render(html)

    html += f'<p>Keywords used for matching: {", ".join(keywords)}</p>'
    html += '<p class="warn">This is a keyword-overlap match, not a judgment of fit. Always review the actual resume before deciding someone qualifies.</p>'

    db2 = get_db()
    people_rows = db2.execute("SELECT id, full_name FROM people").fetchall()

    scored = []
    for person in people_rows:
        docs = db2.execute(
            "SELECT raw_text FROM documents WHERE person_id = ? AND doc_type = 'resume_historical'",
            (person["id"],),
        ).fetchall()
        combined_text = " ".join((d["raw_text"] or "") for d in docs).lower()
        if not combined_text:
            continue
        matched_kw = [kw for kw in keywords if kw.lower() in combined_text]
        if matched_kw:
            scored.append((person, matched_kw))

    scored.sort(key=lambda x: len(x[1]), reverse=True)

    html += "<table><tr><th>Person</th><th>Score</th><th>Matched keywords</th></tr>"
    for person, matched_kw in scored:
        html += f'<tr><td><a class="name" href="/person/{person["id"]}">{person["full_name"]}</a></td>'
        html += f'<td class="score">{len(matched_kw)}/{len(keywords)}</td>'
        html += f'<td>{", ".join(matched_kw)}</td></tr>'
    html += "</table>"

    if not scored:
        html += "<p>No personnel matched any of the keywords in their historical resume text.</p>"

    return render(html)


def main():
    global DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="proposals.db")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    DB_PATH = args.db

    if not Path(DB_PATH).exists():
        print(f"Database '{DB_PATH}' not found. Run ingest.py first.")
        return

    app.run(debug=True, port=args.port)


if __name__ == "__main__":
    main()