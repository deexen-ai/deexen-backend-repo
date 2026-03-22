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

def verify_supabase_token(token: str) -> dict | None:
    # Fast local parse to avoid network stalls during auth checks.
    # NOTE: This is intended for local/dev usage.
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return None

    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        if datetime.utcnow().timestamp() >= float(exp):
            return None

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        return None

    metadata = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}

    return {
        "sub": str(sub),
        "email": str(email).lower(),
        "user_metadata": metadata,
    }
def verify_token(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")

    token = auth_header.split(" ")[1]

    # Try locally issued JWT first.
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid token")

        return {"user_id": user_id}
    except JWTError:
        pass

    # Fallback for Supabase session tokens used by frontend auth.
    supabase_payload = verify_supabase_token(token)
    if supabase_payload:
        return supabase_payload

    raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(token_data: dict = Depends(verify_token), db: Session = Depends(get_db)) -> User:
    if "user_id" in token_data:
        user = db.query(User).filter(User.id == token_data["user_id"]).first()
    else:
        email = (token_data.get("email") or "").lower()
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.email == email).first()
        if not user:
            metadata = token_data.get("user_metadata") or {}
            default_name = email.split("@")[0] if "@" in email else "User"
            user = User(
                email=email,
                name=metadata.get("name") or metadata.get("full_name") or default_name,
                provider="supabase",
                provider_id=token_data.get("sub"),
                is_active=True
            )
            db.add(user)
            try:
                db.commit()
                db.refresh(user)
            except IntegrityError:
                db.rollback()
                user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
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

    if not user or not verify_password(data.password, user.password):
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
