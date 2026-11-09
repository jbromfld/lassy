"""Manually prune old unrated query history: python -m core.metrics.prune --dry-run."""
import argparse
import asyncio
import json
import asyncpg
from config import get_settings
from core.metrics.tracker import MetricsTracker


async def prune(args):
    settings = get_settings()
    pool = await asyncpg.create_pool(host=settings.postgres_host, port=settings.postgres_port,
        database=settings.postgres_db, user=settings.postgres_user, password=settings.postgres_password,
        min_size=1, max_size=1, command_timeout=30)
    try:
        return await MetricsTracker(pool).prune_history(args.days, args.limit, args.dry_run)
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.days <= 36500 or not 1 <= args.limit <= 10000:
        parser.error('days must be 1–36500 and limit 1–10000')
    print(json.dumps(asyncio.run(prune(args))))


if __name__ == '__main__':
    main()
