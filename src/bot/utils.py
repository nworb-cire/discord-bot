from datetime import datetime, timezone


def utcnow() -> datetime:
    """
    Returns the current UTC time as a datetime object.
    """
    return datetime.now(timezone.utc)