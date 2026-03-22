from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
import os

# Load environment variables from apps/backend/.env file
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', '.env')
load_dotenv(dotenv_path=env_path)

from app.routes import auth, profile, projects
from app.routes.plugins_router import router as plugins_router
from app.database import Base, engine
from app.models.user import User
from app.models.project import Project
from app.models.file import File

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Deexen Backend API", version="1.0.0", redirect_slashes=False)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Include routers
from app.routes import auth, profile, projects, ai, terminal
# ... existing code ...
app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(profile.router, prefix="/profile", tags=["Profile"])
app.include_router(projects.router, prefix="/projects", tags=["Projects"])
app.include_router(ai.router, prefix="/ai", tags=["AI"])
app.include_router(terminal.router, prefix="/terminal", tags=["Terminal"])
app.include_router(plugins_router, prefix="/plugins", tags=["Plugins"])
