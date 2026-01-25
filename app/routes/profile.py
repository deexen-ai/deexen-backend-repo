from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.user import User
from app.routes.auth import get_current_user
from app.schemas.auth import UserResponse

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=UserResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )

@router.put("/", response_model=UserResponse)
def update_profile(
    name: str = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user profile"""
    if name:
        current_user.name = name
        db.commit()
        db.refresh(current_user)
    
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )
