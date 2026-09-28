import psycopg
from psycopg.rows import dict_row
import os

def get_connection():
    db_url = os.getenv("DATABASE_URL", "postgresql://iris_user:iris_password@localhost:5432/iris_pilot")
    return psycopg.connect(db_url, row_factory=dict_row)

def execute_sql_file(conn, filepath):
    with open(filepath, 'r') as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
