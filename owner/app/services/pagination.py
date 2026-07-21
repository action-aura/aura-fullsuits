"""Shared pagination helper for list routes (Part V)."""
from __future__ import annotations

from sqlalchemy import Select, func, select

from app.extensions import db_session

DEFAULT_PAGE_SIZE = 25


def paginate(stmt: Select, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    page = max(page, 1)
    total = db_session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db_session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    total_pages = max((total + page_size - 1) // page_size, 1)
    return {
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }
