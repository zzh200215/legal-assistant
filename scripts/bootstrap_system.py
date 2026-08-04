import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.core.migration import run_migrations
from app.core.config import get_settings
from app.models.user import User
from app.services.prompt_service import prompt_service


def ensure_admin_user() -> bool:
    settings = get_settings()
    username = (getattr(settings, "ADMIN_USERNAME", "") or "").strip()
    email = (getattr(settings, "ADMIN_EMAIL", "") or "").strip()
    password = getattr(settings, "ADMIN_PASSWORD", "") or ""

    if not username or not email or not password:
        return False

    db = SessionLocal()
    try:
        existing = db.query(User).filter((User.username == username) | (User.email == email)).first()
        if existing:
            return False
        admin = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            full_name="System Admin",
        )
        db.add(admin)
        db.commit()
        return True
    finally:
        db.close()


def seed_prompts() -> int:
    db = SessionLocal()
    try:
        return prompt_service.seed_defaults(db)
    finally:
        db.close()


def main() -> None:
    run_migrations()
    created = ensure_admin_user()
    seeded = seed_prompts()
    print(f"Prompt templates seeded: {seeded}")
    print("Admin user created." if created else "Admin user bootstrap skipped.")


if __name__ == "__main__":
    main()
