"""
Migration to add missing columns to users table
This adds is_active and updated_at columns to support new user model
"""

# Run this manually in your MySQL database:
# ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
# ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
