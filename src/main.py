# main.py
import asyncio
from queues.channel_queue import startup_queue, queue_stats
from workers.worker_manager import start_all_workers
from tracker.progress_tracker import start_progress_reporter
from db.connection import close_pool
from utils.logger import get_logger, log_info
import version
from utils.channel_registry import ensure_registry_files, load_bootstrap_channels

logger = get_logger(__name__)


async def main():
    log_info(logger, "YuChann Spider starting", "app_start")
    print(f"📦 Version: {version.__version__}")

    # 1. initialize channel registry files
    ensure_registry_files()
    bootstrap_channels = load_bootstrap_channels()
    log_info(
        logger,
        "Channel registry loaded",
        "channel_registry_loaded",
        bootstrap_channels=len(bootstrap_channels),
    )

    # 2. initialize queue from registry
    added = await startup_queue()
    log_info(
        logger,
        "Queue initialized",
        "queue_initialized",
        added=added,
    )

    # 3. stats
    stats = await queue_stats()
    log_info(
        logger,
        "Queue stats snapshot",
        "queue_stats",
        cycle=stats["cycle"],
        pending=stats["pending"],
        pending_update=stats["pending_update"],
        pending_bootstrap=stats["pending_bootstrap"],
        processing_update=stats["processing_update"],
        processing_bootstrap=stats["processing_bootstrap"],
        current_update_shard=stats["current_update_shard"],
        current_update_file=stats["current_update_file"],
        update_cycle_shard=stats["update_cycle_shard"],
        update_cycle_file=stats["update_cycle_file"],
        update_cycle_remaining=stats["update_cycle_remaining"],
        update_idle=stats["update_idle"],
        update_shards=stats["update_shards"],
        update_shards_done=stats["update_shards_done"],
        update_files=stats["update_files"],
        update_files_done=stats["update_files_done"],
        done=stats["done"],
        failed=stats["failed"],
    )


    # 4. progress reporter
    reporter_task = asyncio.create_task(start_progress_reporter())

    try:
        # 5. start workers 
        await start_all_workers()

    finally:
        reporter_task.cancel()

        log_info(
            logger,
            "Shutting down resources",
            "shutdown_start",
        )

        await close_pool()

        log_info(
            logger,
            "Application shutdown complete",
            "shutdown_complete",
        )



if __name__ == "__main__":
    asyncio.run(main())
