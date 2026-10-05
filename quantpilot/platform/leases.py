"""Database lease prevents two worker instances from processing the same queue."""

from datetime import timedelta

from sqlalchemy import or_, update

from .models import WorkerLease, utcnow

LEASE_SECONDS = 120


def renew_lease(factory, name, owner):
    now = utcnow()
    with factory.begin() as db:
        result = db.execute(
            update(WorkerLease)
            .where(
                WorkerLease.name == name,
                or_(WorkerLease.owner == owner, WorkerLease.expires_at < now),
            )
            .values(
                owner=owner,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=LEASE_SECONDS),
            )
        )
        return result.rowcount == 1


def release_lease(factory, name, owner):
    with factory.begin() as db:
        db.execute(
            update(WorkerLease)
            .where(WorkerLease.name == name, WorkerLease.owner == owner)
            .values(owner="", expires_at=utcnow())
        )
