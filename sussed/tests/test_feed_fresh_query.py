from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from sussed.review.service import (
    get_price_histories_for_listings,
    get_recent_scored_listings,
)


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class EmptyScalars:
    def all(self) -> list[Any]:
        return []


class EmptyResult:
    def scalars(self) -> EmptyScalars:
        return EmptyScalars()


class RecordingSession:
    statement: Any | None = None

    async def execute(self, statement: Any) -> EmptyResult:
        self.statement = statement
        return EmptyResult()


def compile_sql(statement: Any) -> str:
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    return " ".join(compiled.split())


@pytest.mark.asyncio
async def test_recent_scored_listings_returns_empty_for_nonpositive_limit() -> None:
    session = RecordingSession()

    result = await get_recent_scored_listings(session, max_age_days=7, limit=0)  # type: ignore[arg-type]

    assert result == []
    assert session.statement is None


@pytest.mark.asyncio
async def test_recent_scored_listings_builds_effective_score_query() -> None:
    session = RecordingSession()

    result = await get_recent_scored_listings(session, max_age_days=7, limit=10)  # type: ignore[arg-type]

    assert result == []
    assert session.statement is not None
    sql = compile_sql(session.statement)

    # Active + within the freshness window (portal date w/ first_seen fallback).
    assert "listings.status = 'ACTIVE'" in sql
    assert "listings.updated_at_source >=" in sql
    assert "listings.first_seen_at >=" in sql

    # Effective score = ai_score ?? safe-cast of ai_analysis->>'score'.
    assert "coalesce" in sql.lower()
    assert "listings.ai_analysis ->> 'score'" in sql
    assert ") ~ '^-?" in sql
    # Bounded to 1-4 digits so an over-range string can't overflow the int cast.
    assert "{1,4}" in sql
    assert "AS INTEGER" in sql

    # Never-scored listings excluded; ordered by effective score first.
    assert "IS NOT NULL" in sql
    assert "ORDER BY" in sql
    assert "DESC" in sql


@pytest.mark.asyncio
async def test_recent_scored_listings_applies_optional_filters() -> None:
    session = RecordingSession()

    await get_recent_scored_listings(
        session,  # type: ignore[arg-type]
        max_age_days=30,
        limit=5,
        district="Pole",
        min_score=600,
        property_type="apartment",
    )

    assert session.statement is not None
    sql = compile_sql(session.statement)

    assert "listings.district ILIKE" in sql
    assert "Pole" in sql
    assert ">= 600" in sql
    assert "listings.property_category = 'APARTMENT'" in sql


@pytest.mark.asyncio
async def test_recent_scored_listings_rejects_unknown_property_type() -> None:
    session = RecordingSession()

    with pytest.raises(ValueError, match="Unknown property_type"):
        await get_recent_scored_listings(
            session,  # type: ignore[arg-type]
            max_age_days=7,
            limit=5,
            property_type="castle",
        )


@pytest.mark.asyncio
async def test_price_histories_batch_returns_empty_without_ids() -> None:
    session = RecordingSession()

    result = await get_price_histories_for_listings(session, [])  # type: ignore[arg-type]

    assert result == {}
    assert session.statement is None


@pytest.mark.asyncio
async def test_price_histories_batch_builds_in_query() -> None:
    session = RecordingSession()
    listing_ids = [uuid4(), uuid4()]

    result = await get_price_histories_for_listings(session, listing_ids)  # type: ignore[arg-type]

    assert result == {}
    assert session.statement is not None
    sql = compile_sql(session.statement)
    assert "price_history.listing_id IN" in sql
    assert "ORDER BY price_history.listing_id, price_history.recorded_at DESC" in sql
