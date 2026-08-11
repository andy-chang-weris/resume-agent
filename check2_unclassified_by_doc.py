import sqlite3

conn = sqlite3.connect("proposals.db")
cur = conn.cursor()
cur.execute("""
    SELECT d.id, COUNT(*) FROM resume_facts rf
    JOIN documents d ON rf.source_document_id = d.id
    WHERE rf.fact_type = 'unclassified'
    GROUP BY d.id ORDER BY COUNT(*) DESC
""")
for row in cur.fetchall():
    print(row)