import argparse, sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("--db", default="proposals.db")
args = parser.parse_args()

conn = sqlite3.connect(args.db)
cur = conn.cursor()
cur.execute("""
    SELECT source_document_id, COUNT(*) as cnt
    FROM resume_facts
    WHERE fact_type = 'education'
    GROUP BY source_document_id
    ORDER BY cnt DESC
""")
print(f"{'DocumentID':<12} {'EducationFactCount'}")
for row in cur.fetchall():
    print(f"{row[0]:<12} {row[1]}")
conn.close()