import os

import psycopg
from dotenv import load_dotenv


def get_connection():
    load_dotenv()

    return psycopg.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        connect_timeout=10,
    )


def main():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'source'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
            tables = [row[0] for row in cursor.fetchall()]

    print("Database connection successful.")
    print(f"Source tables found: {len(tables)}")

    for table in tables:
        print(f"- {table}")


if __name__ == "__main__":
    main()