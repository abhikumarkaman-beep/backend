import sqlite3

from config import Config
from database import init_db, migrate_db
from import_data import import_districts_and_crops, import_disease_product_mapping
from import_syngenta import import_syngenta


def table_count(table):
    conn = sqlite3.connect(Config.DATABASE_PATH)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def main():
    print("[SETUP] Preparing SQLite database")
    init_db()
    migrate_db()

    if table_count("districts") == 0:
        import_districts_and_crops()
    else:
        print("[SETUP] District data already present")

    if table_count("disease_product_map") == 0:
        import_disease_product_mapping()
    else:
        print("[SETUP] Disease mapping already present")

    if table_count("syngenta_retailers") == 0:
        import_syngenta()
    else:
        print("[SETUP] Syngenta data already present")

    print("[SETUP] Database ready:", Config.DATABASE_PATH)


if __name__ == "__main__":
    main()
