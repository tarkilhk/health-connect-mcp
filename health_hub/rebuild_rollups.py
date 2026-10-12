"""Rebuild the optional daily summary cache from stored events without changing them."""
import logging

from health_hub.aggregate.timewindow import local_date_of
from health_hub.config import Settings
from health_hub.db.store import Database
from health_hub.rollup_service import recalc_rollups_for_dates

log = logging.getLogger(__name__)


async def rebuild_rollups(settings: Settings) -> int:
    database = await Database.open(settings)
    try:
        async with database.write() as db:
            async with db.execute(
                "SELECT DISTINCT CASE WHEN data_type = 'sleep' THEN end_utc ELSE start_utc END FROM events"
            ) as cursor:
                dates = sorted({local_date_of(row[0], settings.timezone, settings.day_boundary_hour)
                                for row in await cursor.fetchall()})
            await db.execute("DELETE FROM daily_rollups")
            await recalc_rollups_for_dates(db, dates, settings.timezone, settings.day_boundary_hour)
            await db.commit()
            log.info("Rebuilt %d daily rollups", len(dates))
            return len(dates)
    finally:
        await database.close()
