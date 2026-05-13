"""Auth routes and shared authentication infrastructure.

get_current_user is exported from here and imported by all other route modules
that need to scope requests to the authenticated CEO.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.exc import UnknownHashError
from jose import JWTError, jwt
from passlib.context import CryptContext

from src.core.database import get_user, save_object
from src.core.models import User

SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY",
    "09d6928759c59426da1074c346162070302fd36ef20542df922137085672500c",
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Auth helpers — imported by other route modules
# ---------------------------------------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except UnknownHashError:
        legacy_hash = hashlib.sha256(plain_password.encode()).hexdigest()
        return legacy_hash == hashed_password


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user_from_token(token: str) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = get_user(username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    return get_user_from_token(token)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/auth/register")
def register(username: str, password: str, ceo_id: str, company_name: str):
    if get_user(username):
        raise HTTPException(status_code=400, detail="Username already registered")

    new_user = User(
        username=username,
        hashed_password=get_password_hash(password),
        ceo_id=ceo_id,
        company_name=company_name,
    )
    save_object(new_user)
    return {"msg": "User created successfully"}


@router.post("/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user(form_data.username)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    try:
        password_ok = pwd_context.verify(form_data.password, user.hashed_password)
    except UnknownHashError:
        password_ok = hashlib.sha256(form_data.password.encode()).hexdigest() == user.hashed_password
        if password_ok:
            user.hashed_password = get_password_hash(form_data.password)
            save_object(user)
    if not password_ok:
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}
