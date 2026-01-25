import mysql.connector
connection = mysql.connector.connect(
    host="localhost",
    user="root",
    password="882004",
    database="deexendemo")

if connection.is_connected():
    print("Successfully connected to the database")
connection.close()