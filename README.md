# Deexen Backend API

This is the backend server for Deexen, an AI-powered IDE. It is built with **FastAPI**, **SQLAlchemy**, and **Authlib** for secure authentication and project management.

## 🚀 Features

- **Authentication**: JWT-based login/registration with password hashing (Bcrypt).
- **OAuth Integration**: Support for **GitHub** and **Google** social logins.
- **Projects & Files**: Full CRUD operations for project workspaces and file trees.
- **Database Support**: Configurable for **SQLite** (dev) or **MySQL** (production).
- **Security**: CORS protection and session-based OAuth callbacks.

## 🛠️ Tech Stack

- **Framework**: FastAPI
- **ORM**: SQLAlchemy
- **Authentication**: python-jose, passlib[bcrypt]
- **OAuth**: Authlib
- **Database Drivers**: `mysql-connector-python` (for MySQL usage)

## 📋 Prerequisites

- Python 3.9+
- A running MySQL instance (optional, default is SQLite)

## 🏗️ Installation & Setup

1. **Navigate to the backend directory**:
   ```bash
   cd backend/backend
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**:
   Create a `.env` file based on `.env.example`:
   ```env
   # OAuth
   GITHUB_CLIENT_ID=...
   GITHUB_CLIENT_SECRET=...
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...

   # Database
   DATABASE_URL=sqlite:///./deexen_demo.db
   # For MySQL: DATABASE_URL=mysql+mysqlconnector://user:pass@localhost/db
   ```

## 🏃 Running the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Swagger Docs**: `http://localhost:8000/docs`

## 📁 Project Structure

- `app/models/`: Database entities (User, Project, File)
- `app/routes/`: API endpoint definitions (Auth, OAuth, Projects)
- `app/schemas/`: Pydantic validation models
- `update_schema.py`: Utility for database migrations/schema updates
