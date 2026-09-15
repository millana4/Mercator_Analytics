# app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий базовый класс моделей своей базы (analytics)."""
    pass