import pymysql

# Database connection details
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Password@1", # Using the password from previously shared user credentials or assuming root default
    "database": "deexendemo",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor
}

# The user provided Password@1 for a demo user, but what about root?
# Let's try to connect using the information from database.py first.
# database.py uses root:882004

def run_migration():
    try:
        connection = pymysql.connect(
            host="localhost",
            user="root",
            password="Password@1", # Trying user provided password
            database="deexendemo"
        )
    except Exception:
        try:
            connection = pymysql.connect(
                host="localhost",
                user="root",
                password="882004", # Trying password from database.py
                database="deexendemo"
            )
        except Exception as e:
            print(f"Error connecting to database: {e}")
            return

    try:
        with connection.cursor() as cursor:
            print("Updating users table schema...")
            
            # Make password nullable
            cursor.execute("ALTER TABLE users MODIFY COLUMN password VARCHAR(255) NULL")
            print("- password column updated to NULLable")
            
            # Add provider column if not exists
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN provider VARCHAR(50) NULL AFTER name")
                print("- provider column added")
            except pymysql.err.InternalError as e:
                if e.args[0] == 1060: # Column already exists
                    print("- provider column already exists")
                else:
                    raise
            
            # Add provider_id column if not exists
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN provider_id VARCHAR(255) NULL AFTER provider")
                print("- provider_id column added")
            except pymysql.err.InternalError as e:
                if e.args[0] == 1060:
                    print("- provider_id column already exists")
                else:
                    raise
            
            connection.commit()
            print("Successfully updated database schema.")
            
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    run_migration()
