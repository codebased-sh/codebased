import sqlite3

def create_table(db: sqlite3.Connection):
    db.execute("""
    CREATE TABLE IF NOT EXISTS abc (
        id INTEGER PRIMARY KEY,
        uq TEXT UNIQUE,
        data TEXT
    )
    """)

def insert_or_ignore(db: sqlite3.Connection, uq: str, data: str) -> tuple[int, bool]:
    cursor = db.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO abc (uq, data) VALUES (?, ?)",
        (uq, data)
    )
    row_id = cursor.lastrowid
    was_inserted = cursor.rowcount == 1
    return row_id, was_inserted

# Test the functionality
def main():
    with sqlite3.connect(":memory:") as db:
        create_table(db)
        
        # Test with a new row
        id1, inserted1 = insert_or_ignore(db, "unique1", "data1")
        print(f"Row 1: id={id1}, inserted={inserted1}")
        
        # Try to insert a row with the same unique value
        id2, inserted2 = insert_or_ignore(db, "unique1", "data2")
        print(f"Row 2 (duplicate uq): id={id2}, inserted={inserted2}")
        
        # Insert another new row
        id3, inserted3 = insert_or_ignore(db, "unique2", "data3")
        print(f"Row 3: id={id3}, inserted={inserted3}")

if __name__ == "__main__":
    main()
