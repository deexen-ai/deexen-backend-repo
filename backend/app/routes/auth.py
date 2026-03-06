from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from starlette.requests import Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
import os
from app.database import SessionLocal
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, UserResponse, TokenResponse, LogoutResponse

router = APIRouter()
security = HTTPBearer()

# Security
SECRET_KEY = os.getenv("SECRET_KEY", "deexen-secret-key-change-in-production-env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    
    # Ensure 'sub' is a string as required by many JWT libraries
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
        
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

import os
from dotenv import load_dotenv

load_dotenv()

from supabase import create_client, Client

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wjdvbodcmsfnpuyuhgxi.supabase.co")
supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndqZHZib2RjbXNmbnB1eXVoZ3hpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE4OTYzNzAsImV4cCI6MjA4NzQ3MjM3MH0.kyKn9OnIRD7vyLyYDCOQ00RRmgJ-AXC55zHiMaK2lpw")
supabase: Client = create_client(SUPABASE_URL, supabase_anon_key)

def verify_token(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing Authorization header")
    
    token = auth_header.split(" ")[1]
    
    # Try 1: Decode as locally-issued JWT (HS256 with SECRET_KEY)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == int(user_id)).first()
                if user:
                    payload["email"] = user.email
                    payload["sub"] = str(user.id)
                    print(f"DEBUG: Token decoded as LOCAL JWT for user {user.email}")
                    return payload
            finally:
                db.close()
    except Exception:
        pass  # Not a local token
    
    # Try 2: Verify with Supabase Client (calls Supabase Auth API)
    try:
        user_response = supabase.auth.get_user(token)
        if user_response and user_response.user:
            user = user_response.user
            print(f"DEBUG: Token verified by Supabase API for {user.email}")
            return {
                "sub": user.id,
                "email": user.email,
                "user_metadata": user.user_metadata
            }
    except Exception as e:
        print(f"DEBUG: Supabase API verify failed: {str(e)}")
    
    raise HTTPException(status_code=401, detail="Invalid token signature or expired")

def get_current_user(token_data: dict = Depends(verify_token), db: Session = Depends(get_db)) -> User:
    email = token_data.get("email", "").lower()
    
    # 1. Look up user in our local database by their Supabase Email
    user = db.query(User).filter(User.email == email).first()
    
    # 2. If this is their first time hitting the API after signing up on Supabase Frontend, sync them locally
    if not user:
        name_from_meta = token_data.get("user_metadata", {}).get("name", email.split('@')[0])
        user = User(
            email=email,
            name=name_from_meta,
            provider="supabase",
            provider_id=token_data.get("sub"), # Store their Supabase UUID
            is_active=True
        )
        db.add(user)
        try:
            db.commit()
            db.refresh(user)
        except IntegrityError:
            db.rollback()
            user = db.query(User).filter(User.email == email).first()
            
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
        
    # Return the mapped local user (which has the Integer ID expected by the Project model)
    return user

@router.post("/register", response_model=TokenResponse)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user"""
    try:
        print(f"Register request: email={data.email}, name={data.name}")
        
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == data.email.lower()).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Create new user with hashed password
        user = User(
            email=data.email.lower(),
            password=hash_password(data.password),
            name=data.name,
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
        print(f"User created: id={user.id}, email={user.email}")
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.id}, expires_delta=access_token_expires
        )
        
        return TokenResponse(
            access_token=access_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                name=user.name,
                is_active=user.is_active,
                created_at=user.created_at.isoformat()
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Registration error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")

@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Login with email and password"""
    user = db.query(User).filter(User.email == data.email.lower()).first()
    
    if not user or not user.password or not verify_password(data.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.id}, expires_delta=access_token_expires
    )
    
    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            is_active=user.is_active,
            created_at=user.created_at.isoformat()
        )
    )

@router.post("/logout", response_model=LogoutResponse)
def logout(current_user: User = Depends(get_current_user)):
    """Logout user (token invalidation handled by client)"""
    return LogoutResponse(
        success=True,
        message="Successfully logged out"
    )

@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )
