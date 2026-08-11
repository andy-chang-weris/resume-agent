import sqlite3

conn = sqlite3.connect("proposals.db")
cur = conn.cursor()
cur.execute("SELECT fact_text FROM resume_facts WHERE fact_type='unclassified' LIMIT 5")
for row in cur.fetchall():
    print(row[0][:80])