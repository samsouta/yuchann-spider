# workers/worker_manager.py
import asyncio
from config.settings import ACCOUNTS, BOOTSTRAP_WORKER, UPDATE_WORKER
from workers.worker import run_worker
from utils.logger import get_logger, log_info, log_warning
from utils.worker_mana_helper import preflight_check
from cache.redis import get_redis ,close_redis
from config.redis_keys import RedisKeys
from config.job_types import JOB_BOOTSTRAP, JOB_UPDATE

logger = get_logger(__name__)

# -------------- Helper --------------
async def _is_worker_cooling_down(r, worker_id: str) -> bool:
    val = await r.get(RedisKeys.worker_cooldown(worker_id))
    return val is not None


async def _start_worker(account: dict, r, worker_role: str):
    worker_id = f"worker_{account.get('id')}"

    if await _is_worker_cooling_down(r, worker_id):
        cooldown_val = await r.get(RedisKeys.worker_cooldown(worker_id))
        remaining = float(cooldown_val) - asyncio.get_running_loop().time()
        log_warning(
            logger,
            "Worker in cooldown. Skipping execution",
            "worker_cooldown_skip",
            worker_id=worker_id,
            worker_role=worker_role,
            remaining_seconds=round(remaining, 2) if remaining else None,
            remaining_hours=round(remaining / 3600, 2) if remaining else None,
        )
        return

    await run_worker(account, r, worker_role)
    

# --------------- Main Manager -------------
async def start_all_workers():
    requested_workers = UPDATE_WORKER + BOOTSTRAP_WORKER
    active_accounts = ACCOUNTS[:requested_workers]

    # ---- 1. verify every account BEFORE touching the queue ----
    ready_accounts = await preflight_check(active_accounts)

    if len(ready_accounts) < len(active_accounts):
        log_warning(
            logger,
            "Accounts skipped during preflight",
            "worker_accounts_skipped",
            skipped_accounts=len(active_accounts) - len(ready_accounts),
            ready_accounts=len(ready_accounts),
        )

    bootstrap_count = min(BOOTSTRAP_WORKER, len(ready_accounts))
    update_count = min(UPDATE_WORKER, max(len(ready_accounts) - bootstrap_count, 0))

    bootstrap_accounts = ready_accounts[:bootstrap_count]
    update_accounts = ready_accounts[bootstrap_count:bootstrap_count + update_count]

    if bootstrap_count < BOOTSTRAP_WORKER or update_count < UPDATE_WORKER:
        log_warning(
            logger,
            "Worker pool under-provisioned",
            "worker_pool_under_provisioned",
            ready_accounts=len(ready_accounts),
            requested_bootstrap=BOOTSTRAP_WORKER,
            requested_update=UPDATE_WORKER,
            assigned_bootstrap=bootstrap_count,
            assigned_update=update_count,
        )

    # ---- 2. start only the healthy workers ----
    r = await get_redis()
    try:
        log_info(
            logger,
            "Starting workers in parallel",
            "workers_start",
            worker_count=bootstrap_count + update_count,
            bootstrap_workers=bootstrap_count,
            update_workers=update_count,
        )

        tasks = [
            *[
                asyncio.create_task(_start_worker(account, r, JOB_BOOTSTRAP))
                for account in bootstrap_accounts
            ],
            *[
                asyncio.create_task(_start_worker(account, r, JOB_UPDATE))
                for account in update_accounts
            ],
        ]

        await asyncio.gather(*tasks)
        log_info(logger, "All workers finished", "workers_finished")

    finally:
        await close_redis()
        log_info(logger, "Redis connection closed", "redis_connection_closed")
