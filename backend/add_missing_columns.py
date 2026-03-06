import os
import sys
from dotenv import load_dotenv
import psycopg2
from urllib.parse import urlparse

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

database_url = os.getenv("DATABASE_URL")
if not database_url:
    print("DATABASE_URL not found in .env file")
    sys.exit(1)

# Ensure it works with psycopg2
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if database_url.startswith("postgresql+psycopg2://"):
    database_url = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)

try:
    print(f"Connecting to database...")
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Check if column exists
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='files' and column_name='created_at';
    """)
    
    exists = cursor.fetchone()
    if exists:
        print("Column 'created_at' already exists in the 'files' table.")
    else:
        print("Adding 'created_at' column to 'files' table...")
        cursor.execute("""
            ALTER TABLE files 
            ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc');
        """)
        print("Successfully added 'created_at' column!")
        
    # Also check for updated_at
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='files' and column_name='updated_at';
    """)
    
    updated_exists = cursor.fetchone()
    if updated_exists:
        print("Column 'updated_at' already exists in the 'files' table.")
    else:
        print("Adding 'updated_at' column to 'files' table...")
        cursor.execute("""
            ALTER TABLE files 
            ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc');
        """)
        print("Successfully added 'updated_at' column!")
        
    cursor.close()
    conn.close()
    print("Database schema update complete.")
    
except Exception as e:
    print(f"Error updating database schema: {e}")
    sys.exit(1)
