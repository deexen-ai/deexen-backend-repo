from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.user import User
from app.routes.auth import create_access_token, ALGORITHM, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES
from datetime import timedelta
import os

router = APIRouter()
oauth = OAuth()

# OAuth Configuration (Placeholders)
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "placeholder")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "placeholder")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "placeholder")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "placeholder")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

oauth.register(
    name='github',
    client_id=GITHUB_CLIENT_ID,
    client_secret=GITHUB_CLIENT_SECRET,
    access_token_url='https://github.com/login/oauth/access_token',
    access_token_params=None,
    authorize_url='https://github.com/login/oauth/authorize',
    authorize_params=None,
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email', 'timeout': 20.0},
)

oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile', 'timeout': 20.0},
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get('/login/{provider}')
async def login(provider: str, request: Request):
    redirect_uri = request.url_for('auth_callback', provider=provider)
    if provider == 'github':
        return await oauth.github.authorize_redirect(request, redirect_uri)
    elif provider == 'google':
        return await oauth.google.authorize_redirect(request, redirect_uri)
    raise HTTPException(status_code=400, detail="Invalid provider")

@router.get('/callback/{provider}', name='auth_callback')
async def auth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    try:
        if provider == 'github':
            token = await oauth.github.authorize_access_token(request)
            resp = await oauth.github.get('user', token=token)
            user_info = resp.json()
            
            # GitHub might hide email, need to fetch explicitly if not present
            email = user_info.get('email')
            if not email:
                emails_resp = await oauth.github.get('user/emails', token=token)
                emails = emails_resp.json()
                email = next((e['email'] for e in emails if e['primary']), emails[0]['email'])
                
            provider_id = str(user_info['id'])
            name = user_info.get('name') or user_info.get('login')
            
        elif provider == 'google':
            token = await oauth.google.authorize_access_token(request)
            user_info = token.get('userinfo')
            email = user_info['email']
            provider_id = user_info['sub']
            name = user_info.get('name', email.split('@')[0])
        else:
            raise HTTPException(status_code=400, detail="Invalid provider")

        # Sync User with DB
        user = db.query(User).filter(User.email == email.lower()).first()
        if not user:
            user = User(
                email=email.lower(),
                name=name,
                provider=provider,
                provider_id=provider_id,
                is_active=True
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            # Update provider info if not set
            if not user.provider:
                user.provider = provider
                user.provider_id = provider_id
                db.commit()

        # Create Access Token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.id}, expires_delta=access_token_expires
        )

        # Redirect to Frontend with Token
        return RedirectResponse(url=f"{FRONTEND_URL}/login?token={access_token}")
        
    except Exception as e:
        print(f"OAuth Callback Error: {e}")
        # Return a friendly error to the frontend if possible, or just 400
        # If possible, redirect to login with error param
        return RedirectResponse(url=f"{FRONTEND_URL}/login?error=OAuth Failed. Please try again.")
