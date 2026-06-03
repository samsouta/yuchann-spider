import asyncio
import json

from cache.redis import get_redis
from config.job_types import JOB_BOOTSTRAP, JOB_TYPES, JOB_UPDATE
from config.redis_keys import RedisKeys
from config.settings import CYCLE_MIN_SECONDS
from utils.channel_registry import (
    add_bootstrap_channel,
    get_first_update_shard,
    has_update_channel,
    list_update_shards,
    load_bootstrap_channels,
    load_update_shard,
    move_bootstrap_to_update,
    normalize_registry,
    remove_bootstrap_channel,
    remove_update_channel,
    sync_bootstrap_file_limit,
)
from utils.logger import get_logger, log_debug, log_error, log_info, log_warning

logger = get_logger(__name__)

PROCESSING_WAIT_INTERVAL = 2
PROCESSING_WAIT_TIMEOUT = 1800


def _validate_job_type(job_type: str):
    if job_type not in JOB_TYPES:
        raise ValueError(f"Invalid job_type={job_type}")


async def _enqueue_channel(r, channel: dict, job_type: str) -> bool:
    _validate_job_type(job_type)

    username = channel.get("username")
    if not username:
        return False

    item = dict(channel)
    item["job_type"] = job_type

    pending_set = RedisKeys.queue_pending_set(job_type)
    already_queued = await r.sismember(pending_set, username)
    if already_queued:
        return False

    await r.rpush(RedisKeys.queue_pending(job_type), json.dumps(item))
    await r.sadd(pending_set, username)
    return True


async def _should_skip_channel(r, username: str, check_done: bool = True) -> bool:
    if await r.sismember(RedisKeys.QUEUE_FAILED, username):
        return True
    if await r.sismember(RedisKeys.retired_key(), username):
        return True
    if check_done and await r.sismember(RedisKeys.QUEUE_DONE, username):
        return True
    return False


async def _queue_counts(r) -> dict:
    return {
        "pending_update": await r.llen(RedisKeys.QUEUE_PENDING_UPDATE),
        "pending_bootstrap": await r.llen(RedisKeys.QUEUE_PENDING_BOOTSTRAP),
        "processing_update": await r.hlen(RedisKeys.QUEUE_PROCESSING_UPDATE),
        "processing_bootstrap": await r.hlen(RedisKeys.QUEUE_PROCESSING_BOOTSTRAP),
    }


async def _get_current_update_shard(r) -> str:
    available = await list_update_shards(r)
    current = await r.get(RedisKeys.QUEUE_UPDATE_CURRENT_SHARD)

    if current in available:
        return current

    current = await get_first_update_shard(r)
    await r.set(RedisKeys.QUEUE_UPDATE_CURRENT_SHARD, current)
    return current


def _ordered_update_shards(
    shard_names: list[str],
    current_shard: str,
    done_shards: set[str],
    start_after_current: bool,
) -> list[str]:
    if not shard_names:
        return []

    if current_shard in shard_names:
        start_index = shard_names.index(current_shard)
        if start_after_current:
            start_index = (start_index + 1) % len(shard_names)
    else:
        start_index = 0

    ordered = shard_names[start_index:] + shard_names[:start_index]
    return [name for name in ordered if name not in done_shards]


async def _clear_update_runtime_state(r):
    await r.delete(
        RedisKeys.QUEUE_PENDING_UPDATE,
        RedisKeys.QUEUE_PENDING_UPDATE_SET,
        RedisKeys.QUEUE_PROCESSING_UPDATE,
        RedisKeys.QUEUE_DONE,
        RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES,
        RedisKeys.QUEUE_UPDATE_CYCLE_SHARD,
    )


async def _build_update_cycle_snapshot(r, update_shard: str, owner: str) -> int:
    await r.delete(
        RedisKeys.QUEUE_PENDING_UPDATE,
        RedisKeys.QUEUE_PENDING_UPDATE_SET,
        RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES,
    )
    await r.set(RedisKeys.QUEUE_UPDATE_CYCLE_SHARD, update_shard)

    added = 0
    for channel in await load_update_shard(r, update_shard):
        username = channel.get("username")
        if not username or await _should_skip_channel(r, username, check_done=False):
            continue

        last_id = await r.get(RedisKeys.last_id(username))
        if not last_id:
            await remove_update_channel(r, username, owner)
            if not await _should_skip_channel(r, username, check_done=False):
                await add_bootstrap_channel(r, channel, owner)
            log_warning(
                logger,
                "Update channel missing cursor moved to bootstrap",
                "update_channel_missing_cursor_moved",
                username=username,
                update_shard=update_shard,
            )
            continue

        if await _enqueue_channel(r, channel, JOB_UPDATE):
            await r.sadd(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES, username)
            added += 1

    log_info(
        logger,
        "Update cycle snapshot built",
        "update_cycle_snapshot_built",
        update_shard=update_shard,
        eligible_channels=added,
    )
    return added


async def _set_update_idle(r, worker_id: str, reason: str):
    await r.delete(
        RedisKeys.QUEUE_PENDING_UPDATE,
        RedisKeys.QUEUE_PENDING_UPDATE_SET,
        RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES,
        RedisKeys.QUEUE_UPDATE_CYCLE_SHARD,
    )
    idle_since = await r.get(RedisKeys.QUEUE_UPDATE_IDLE_SINCE)
    if not idle_since:
        idle_since = str(asyncio.get_running_loop().time())
        await r.set(RedisKeys.QUEUE_UPDATE_IDLE_SINCE, idle_since)

    log_info(
        logger,
        "No eligible update channels available",
        "update_no_eligible_channels",
        worker_id=worker_id,
        reason=reason,
        idle_since=idle_since,
    )


async def _start_update_cycle(worker_id: str, r, mark_current_file_done: bool, increment_cycle: bool) -> bool:
    current_shard = await _get_current_update_shard(r)
    shard_names = await list_update_shards(r)
    cycle = int(await r.get(RedisKeys.QUEUE_CYCLE) or 1)
    multiple_shards = len(shard_names) > 1

    if multiple_shards:
        done_shards = set(await r.smembers(RedisKeys.QUEUE_UPDATE_SHARD_DONE))
        if mark_current_file_done:
            await r.sadd(RedisKeys.QUEUE_UPDATE_SHARD_DONE, current_shard)
            done_shards.add(current_shard)

        if shard_names and set(shard_names).issubset(done_shards):
            await r.delete(RedisKeys.QUEUE_UPDATE_SHARD_DONE)
            done_shards = set()

        candidate_shards = _ordered_update_shards(
            shard_names,
            current_shard,
            done_shards,
            start_after_current=mark_current_file_done,
        )
    else:
        await r.delete(RedisKeys.QUEUE_UPDATE_SHARD_DONE)
        candidate_shards = shard_names

    for next_shard in candidate_shards:
        await r.set(RedisKeys.QUEUE_UPDATE_CURRENT_SHARD, next_shard)
        await r.delete(RedisKeys.QUEUE_DONE)

        added = await _build_update_cycle_snapshot(r, next_shard, worker_id)
        if added == 0:
            if multiple_shards:
                await r.sadd(RedisKeys.QUEUE_UPDATE_SHARD_DONE, next_shard)
            continue

        cycle_id = await r.incr(RedisKeys.QUEUE_UPDATE_CYCLE_ID)
        new_cycle = cycle + 1 if increment_cycle else cycle
        await r.set(RedisKeys.QUEUE_CYCLE, str(new_cycle))
        await r.set(RedisKeys.QUEUE_CYCLE_START, asyncio.get_running_loop().time())
        await r.delete(RedisKeys.QUEUE_UPDATE_IDLE_SINCE)

        log_info(
            logger,
            "Update cycle started",
            "update_cycle_started",
            worker_id=worker_id,
            previous_update_shard=current_shard,
            current_update_shard=next_shard,
            cycle_id=cycle_id,
            cycle=new_cycle,
            added_update=added,
        )
        return True

    await r.delete(RedisKeys.QUEUE_UPDATE_SHARD_DONE)
    await r.set(RedisKeys.QUEUE_UPDATE_CURRENT_SHARD, await get_first_update_shard(r))
    await _set_update_idle(r, worker_id, "no_eligible_channels")
    return False


async def sync_bootstrap_queue(r, owner: str = "queue") -> int:
    await sync_bootstrap_file_limit(r, owner)

    added = 0
    for channel in load_bootstrap_channels():
        username = channel.get("username")
        if not username or await _should_skip_channel(r, username):
            continue

        if await has_update_channel(r, username):
            await remove_bootstrap_channel(r, channel, owner)
            continue

        last_id = await r.get(RedisKeys.last_id(username))
        if last_id:
            try:
                await move_bootstrap_to_update(r, channel, owner)
            except Exception as e:
                log_error(
                    logger,
                    "Bootstrap registry recovery move failed",
                    "bootstrap_registry_recovery_move_error",
                    username=username,
                    error=str(e),
                )
            continue

        if await _enqueue_channel(r, channel, JOB_BOOTSTRAP):
            added += 1

    if added:
        log_info(
            logger,
            "Bootstrap queue synced from registry",
            "bootstrap_queue_synced",
            added=added,
        )
    return added


# ------------------------ STARTUP ------------------------
async def startup_queue() -> int:
    r = await get_redis()
    await normalize_registry(r, "startup_queue")

    await r.delete(
        RedisKeys.QUEUE_PENDING,
        RedisKeys.QUEUE_PENDING_SET,
        RedisKeys.QUEUE_PENDING_UPDATE,
        RedisKeys.QUEUE_PENDING_UPDATE_SET,
        RedisKeys.QUEUE_PENDING_BOOTSTRAP,
        RedisKeys.QUEUE_PENDING_BOOTSTRAP_SET,
        RedisKeys.QUEUE_RESET_LOCK,
        RedisKeys.QUEUE_UPDATE_CURRENT_FILE,
        RedisKeys.QUEUE_UPDATE_FILE_DONE,
        RedisKeys.QUEUE_UPDATE_SHARD_DONE,
        RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES,
        RedisKeys.QUEUE_UPDATE_CYCLE_FILE,
        RedisKeys.QUEUE_UPDATE_CYCLE_SHARD,
        RedisKeys.QUEUE_UPDATE_IDLE_SINCE,
    )

    stuck_update = await r.hgetall(RedisKeys.QUEUE_PROCESSING_UPDATE)
    stuck_bootstrap = await r.hgetall(RedisKeys.QUEUE_PROCESSING_BOOTSTRAP)
    stuck_legacy = await r.hgetall(RedisKeys.QUEUE_PROCESSING)
    stuck_count = len(stuck_update) + len(stuck_bootstrap) + len(stuck_legacy)
    if stuck_count:
        log_warning(
            logger,
            "Found stuck channels. Clearing processing queues",
            "queue_startup_stuck_processing",
            stuck_count=stuck_count,
        )

    await r.delete(
        RedisKeys.QUEUE_PROCESSING,
        RedisKeys.QUEUE_PROCESSING_UPDATE,
        RedisKeys.QUEUE_PROCESSING_BOOTSTRAP,
    )

    update_shard = await _get_current_update_shard(r)
    await _clear_update_runtime_state(r)
    await r.set(RedisKeys.QUEUE_UPDATE_CURRENT_SHARD, update_shard)
    started_update = await _start_update_cycle(
        "startup_queue",
        r,
        mark_current_file_done=False,
        increment_cycle=False,
    )
    added_update = await r.scard(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES) if started_update else 0
    added_bootstrap = await sync_bootstrap_queue(r, "startup_queue")

    cycle = await r.get(RedisKeys.QUEUE_CYCLE)
    if not cycle:
        cycle = "1"
        await r.set(RedisKeys.QUEUE_CYCLE, cycle)
        await r.set(RedisKeys.QUEUE_CYCLE_START, asyncio.get_running_loop().time())

    log_info(
        logger,
        "Queue initialized",
        "queue_startup",
        cycle=cycle,
        update_shard=update_shard,
        added_update=added_update,
        added_bootstrap=added_bootstrap,
    )

    return added_update + added_bootstrap


# ------------------------ PULL ------------------------
async def pull_channel(worker_id: str, r, job_type: str) -> dict | None:
    _validate_job_type(job_type)

    result = await r.blpop(RedisKeys.queue_pending(job_type), timeout=5)
    if not result:
        return None

    _, raw = result
    channel = json.loads(raw)
    username = channel.get("username")
    channel["job_type"] = job_type

    await r.hset(RedisKeys.queue_processing(job_type), username, worker_id)
    await r.srem(RedisKeys.queue_pending_set(job_type), username)

    return channel


# ------------------------ MARK DONE ------------------------
async def mark_done(worker_id: str, username: str, r, job_type: str):
    _validate_job_type(job_type)

    await r.hdel(RedisKeys.queue_processing(job_type), username)
    if job_type == JOB_UPDATE:
        await r.sadd(RedisKeys.QUEUE_DONE, username)
        await r.srem(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES, username)
    log_debug(
        logger,
        "Channel marked as done",
        "channel_done",
        username=username,
        worker_id=worker_id,
        job_type=job_type,
    )


# ------------------------ MARK FAILED ------------------------
async def mark_failed(worker_id: str, username: str, r, job_type: str):
    _validate_job_type(job_type)

    await r.hdel(RedisKeys.queue_processing(job_type), username)
    await r.sadd(RedisKeys.QUEUE_FAILED, username)
    if job_type == JOB_UPDATE:
        await r.srem(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES, username)
    log_warning(
        logger,
        "Channel marked as failed",
        "channel_failed",
        username=username,
        worker_id=worker_id,
        job_type=job_type,
    )


async def mark_skipped(worker_id: str, username: str, r, job_type: str, reason: str):
    _validate_job_type(job_type)

    await r.hdel(RedisKeys.queue_processing(job_type), username)
    if job_type == JOB_UPDATE:
        await r.srem(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES, username)
    log_info(
        logger,
        "Channel skipped",
        "channel_skipped",
        username=username,
        worker_id=worker_id,
        job_type=job_type,
        reason=reason,
    )


# ------------------------ REQUEUE ------------------------
async def requeue_channel(r, username: str, item: dict):
    job_type = item.get("job_type", JOB_UPDATE)
    _validate_job_type(job_type)

    await r.hdel(RedisKeys.queue_processing(job_type), username)

    already_queued = await r.sismember(RedisKeys.queue_pending_set(job_type), username)
    if already_queued:
        return

    item = dict(item)
    item["job_type"] = job_type
    await r.rpush(RedisKeys.queue_pending(job_type), json.dumps(item))
    await r.sadd(RedisKeys.queue_pending_set(job_type), username)
    log_info(
        logger,
        "Channel requeued after flood cooldown",
        "channel_requeued",
        username=username,
        job_type=job_type,
    )


# ------------------------ CYCLE CHECK ------------------------
async def _wait_for_update_processing_empty(r) -> bool:
    start = asyncio.get_running_loop().time()

    while True:
        processing = await r.hlen(RedisKeys.QUEUE_PROCESSING_UPDATE)
        if processing == 0:
            return True

        elapsed = asyncio.get_running_loop().time() - start
        if elapsed >= PROCESSING_WAIT_TIMEOUT:
            log_warning(
                logger,
                "Update processing queue did not drain before timeout",
                "update_processing_wait_timeout",
                processing=processing,
                timeout_seconds=PROCESSING_WAIT_TIMEOUT,
            )
            return False

        log_info(
            logger,
            "Waiting for active update workers before cycle reset",
            "update_cycle_reset_wait_processing",
            processing=processing,
            elapsed_seconds=round(elapsed, 2),
        )
        await asyncio.sleep(PROCESSING_WAIT_INTERVAL)


async def is_update_cycle_done(r) -> bool:
    if await r.llen(RedisKeys.QUEUE_PENDING_UPDATE) > 0:
        return False

    if await r.hlen(RedisKeys.QUEUE_PROCESSING_UPDATE) > 0:
        drained = await _wait_for_update_processing_empty(r)
        if not drained:
            return False

    await asyncio.sleep(2)

    if await r.llen(RedisKeys.QUEUE_PENDING_UPDATE) > 0:
        return False
    if await r.hlen(RedisKeys.QUEUE_PROCESSING_UPDATE) > 0:
        return False

    return await r.scard(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES) == 0


async def is_update_idle(r) -> bool:
    return bool(await r.get(RedisKeys.QUEUE_UPDATE_IDLE_SINCE))


# ------------------------ RESET CYCLE ------------------------
async def reset_update_cycle(worker_id: str, r) -> bool:
    cycle_start = float(await r.get(RedisKeys.QUEUE_CYCLE_START) or 0)
    elapsed = asyncio.get_running_loop().time() - cycle_start
    if elapsed < CYCLE_MIN_SECONDS:
        log_info(
            logger,
            "Update cycle too young",
            "update_cycle_too_young",
            worker_id=worker_id,
            elapsed_seconds=round(elapsed, 2),
            minimum_seconds=CYCLE_MIN_SECONDS,
        )
        return False

    lock = await r.set(RedisKeys.QUEUE_RESET_LOCK, worker_id, nx=True, ex=300)
    if not lock:
        return False

    try:
        if await r.llen(RedisKeys.QUEUE_PENDING_UPDATE) > 0 or await r.hlen(RedisKeys.QUEUE_PROCESSING_UPDATE) > 0:
            log_info(
                logger,
                "Update cycle reset skipped because queue is active",
                "update_cycle_reset_active_queue",
                worker_id=worker_id,
            )
            return False

        update_idle = await is_update_idle(r)
        return await _start_update_cycle(
            worker_id,
            r,
            mark_current_file_done=not update_idle,
            increment_cycle=True,
        )

    finally:
        owner = await r.get(RedisKeys.QUEUE_RESET_LOCK)
        if owner == worker_id:
            await r.delete(RedisKeys.QUEUE_RESET_LOCK)


# ------------------------ STATS ------------------------
async def queue_stats() -> dict:
    r = await get_redis()
    counts = await _queue_counts(r)
    update_shard = await _get_current_update_shard(r)
    update_shards = await list_update_shards(r)
    done_shards = await r.smembers(RedisKeys.QUEUE_UPDATE_SHARD_DONE)
    cycle_shard = await r.get(RedisKeys.QUEUE_UPDATE_CYCLE_SHARD)
    idle_since = await r.get(RedisKeys.QUEUE_UPDATE_IDLE_SINCE)

    return {
        "pending": counts["pending_update"] + counts["pending_bootstrap"],
        "processing": counts["processing_update"] + counts["processing_bootstrap"],
        "pending_update": counts["pending_update"],
        "pending_bootstrap": counts["pending_bootstrap"],
        "processing_update": counts["processing_update"],
        "processing_bootstrap": counts["processing_bootstrap"],
        "done": await r.scard(RedisKeys.QUEUE_DONE),
        "failed": await r.scard(RedisKeys.QUEUE_FAILED),
        "cycle": int(await r.get(RedisKeys.QUEUE_CYCLE) or 1),
        "current_update_shard": update_shard,
        "current_update_file": update_shard,
        "update_cycle_shard": cycle_shard or update_shard,
        "update_cycle_file": cycle_shard or update_shard,
        "update_cycle_remaining": await r.scard(RedisKeys.QUEUE_UPDATE_CYCLE_USERNAMES),
        "update_idle": bool(idle_since),
        "update_shards": len(update_shards),
        "update_shards_done": len(done_shards),
        "update_files": len(update_shards),
        "update_files_done": len(done_shards),
    }
