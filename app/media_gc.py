"""Inspect or remove expired, unreferenced media objects."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.database import create_database
from app.services.media_gc import list_media_gc_candidates, run_media_gc
from app.services.media_storage import MediaStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回收未被当前文档引用的云端图片")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只列出候选资源，不执行删除")
    mode.add_argument("--execute", action="store_true", help="执行 OSS 与数据库删除")
    parser.add_argument("--ready-days", type=int, default=7, help="ready 资源宽限天数")
    parser.add_argument("--pending-hours", type=int, default=24, help="pending 资源宽限小时")
    parser.add_argument("--limit", type=int, default=100, help="单次最多处理数量")
    args = parser.parse_args()
    if args.ready_days < 1 or args.pending_hours < 1 or not 1 <= args.limit <= 1000:
        parser.error("宽限时间必须为正数，limit 必须在 1 到 1000 之间")
    return args


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    engine, sessions = create_database(settings)
    now = datetime.now(UTC)
    ready_before = now - timedelta(days=args.ready_days)
    pending_before = now - timedelta(hours=args.pending_hours)
    try:
        if args.dry_run:
            async with sessions() as session:
                candidates = await list_media_gc_candidates(
                    session,
                    ready_before,
                    pending_before,
                    args.limit,
                )
            total_bytes = sum(candidate.size_bytes for candidate in candidates)
            print(f"候选资源：{len(candidates)} 个，共 {total_bytes} 字节")
            for candidate in candidates:
                print(
                    f"{candidate.status}/{candidate.gc_state} "
                    f"{candidate.workspace_id}/{candidate.asset_id} {candidate.object_key}"
                )
            return 0

        result = await run_media_gc(
            sessions,
            MediaStorage(settings),
            ready_before,
            pending_before,
            args.limit,
        )
        print(
            f"候选 {result.selected}，已删除 {result.deleted}，"
            f"失败 {result.failed}，跳过 {result.skipped}"
        )
        return 1 if result.failed else 0
    finally:
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
