from __future__ import annotations

import bcrypt
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


def register_user(db: Session, email: str, password: str, display_name: str | None = None) -> User:
    email_norm = email.strip().lower()
    if get_user_by_email(db, email_norm):
        raise ValueError("Email already registered")
    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=display_name.strip() if display_name else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.utcnow()
    db.add(user)
    db.commit()
    return user