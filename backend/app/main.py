from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

from app.routes import auth, profile, projects, oauth
from app.database import Base, engine
from app.models.user import User
from app.models.project import Project
from app.models.file import File

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Deexen Backend API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add SessionMiddleware for OAuth
app.add_middleware(SessionMiddleware, secret_key="deexen-session-secret-key-change-in-production")

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Include routers
from app.routes import auth, profile, projects, oauth, ai
# ... existing code ...
app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(profile.router, prefix="/profile", tags=["Profile"])
app.include_router(projects.router, prefix="/projects", tags=["Projects"])
app.include_router(oauth.router, prefix="/oauth", tags=["OAuth"])
app.include_router(ai.router, prefix="/ai", tags=["AI"])
