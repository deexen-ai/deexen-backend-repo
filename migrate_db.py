import mysql.connector
from mysql.connector import Error

try:
    # Connect to MySQL
    connection = mysql.connector.connect(
        host='localhost',
        user='root',
        password='882004',
        database='deexendemo'
    )

    if connection.is_connected():
        cursor = connection.cursor()
        
        # Check if column exists before adding
        cursor.execute("SHOW COLUMNS FROM users LIKE 'is_active'")
        if not cursor.fetchone():
            print("Adding is_active column...")
            cursor.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            print("✓ is_active column added")
        else:
            print("is_active column already exists")
        
        cursor.execute("SHOW COLUMNS FROM users LIKE 'updated_at'")
        if not cursor.fetchone():
            print("Adding updated_at column...")
            cursor.execute("ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
            print("✓ updated_at column added")
        else:
            print("updated_at column already exists")
        
        connection.commit()
        print("\n✓ Database migration completed successfully!")

except Error as e:
    print(f"Error while connecting to MySQL or executing query: {e}")

finally:
    if connection.is_connected():
        cursor.close()
        connection.close()
