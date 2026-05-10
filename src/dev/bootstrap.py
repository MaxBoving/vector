"""Development bootstrap utilities.

Run:  python -m src.dev.bootstrap

Seeds the persistent world (Vela / Jordan Kessler) for all users.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def seed_all_users() -> None:
    """Seed the persistent world for every user in the database."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    import seed_world as _seed_world
    from sqlmodel import Session, select
    from src.core.database import engine
    from src.core.models import User

    with Session(engine) as session:
        users = session.exec(select(User)).all()

    for user in users:
        try:
            _seed_world.seed_world(ceo_id=user.ceo_id, username=user.username)
            logger.info("World seeded for ceo_id=%s", user.ceo_id)
        except Exception:
            logger.exception("Seed failed for ceo_id=%s — skipping", user.ceo_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_all_users()
