import asyncio
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from config.redis_keys import RedisKeys
from config.settings import (
    BOOTSTRAP_CHANNEL_FILE_LIMIT,
    CHANNELS_DIR,
    UPDATE_CHANNEL_FILE_LIMIT,
)
from utils.logger import get_logger, log_info

logger = get_logger(__name__)

BOOTSTRAP_FILE_NAME = "bootstrap_channel.json"
UPDATE_BASE_FILE_NAME = "update_channel.json"
UPDATE_SHARD_PATTERN = re.compile(r"^update_channel_(\d{3})\.json$")
UPDATE_REDIS_SHARD_PREFIX = "update"
UPDATE_REDIS_BASE_SHARD = "update_001"
LOCK_TTL_SECONDS = 30
LOCK_RETRY_SECONDS = 0.2


@dataclass(frozen=True)
class UpdateShard:
    name: str
    path: Path
    channels: list[dict]


@dataclass(frozen=True)
class BootstrapShard:
    name: str
    path: Path
    channels: list[dict]


def _channels_dir() -> Path:
    return Path(CHANNELS_DIR)


def bootstrap_file_path() -> Path:
    return _channels_dir() / BOOTSTRAP_FILE_NAME


def _base_update_file_path() -> Path:
    return _channels_dir() / UPDATE_BASE_FILE_NAME


def _base_bootstrap_file_path() -> Path:
    return _channels_dir() / BOOTSTRAP_FILE_NAME


def _normalize_username(username: str | None) -> str:
    return (username or "").strip().lstrip("@").lower()


def _channel_key(channel: dict) -> str:
    return _normalize_username(channel.get("username"))


def _read_channels(path: Path) -> list[dict]:
    if not path.exists():
        return []

    with path.open("r") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Channel registry file must contain a list: {path}")

    return [ch for ch in data if isinstance(ch, dict) and _channel_key(ch)]


def _write_channels(path: Path, channels: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(channels, f, indent=2)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _dedupe_channels(channels: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for channel in channels:
        key = _channel_key(channel)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(channel)
    return unique


def ensure_registry_files():
    _channels_dir().mkdir(parents=True, exist_ok=True)
    for path in (_base_bootstrap_file_path(), _base_update_file_path()):
        if not path.exists():
            _write_channels(path, [])


def load_bootstrap_channels() -> list[dict]:
    return load_all_bootstrap_channels()


def list_bootstrap_shards() -> list[BootstrapShard]:
    ensure_registry_files()
    directory = _channels_dir()
    paths = [directory / BOOTSTRAP_FILE_NAME]

    numbered_paths = []
    for path in directory.glob("bootstrap_channel_*.json"):
        match = re.match(r"^bootstrap_channel_(\d{3})\.json$", path.name)
        if match:
            numbered_paths.append((int(match.group(1)), path))

    paths.extend(path for _, path in sorted(numbered_paths, key=lambda item: item[0]))
    return [
        BootstrapShard(name=path.name, path=path, channels=_dedupe_channels(_read_channels(path)))
        for path in paths
    ]


def load_all_bootstrap_channels() -> list[dict]:
    channels = []
    for shard in list_bootstrap_shards():
        channels.extend(shard.channels)
    return _dedupe_channels(channels)


def list_legacy_update_shards() -> list[UpdateShard]:
    ensure_registry_files()
    directory = _channels_dir()
    paths = [directory / UPDATE_BASE_FILE_NAME]

    numbered_paths = []
    for path in directory.glob("update_channel_*.json"):
        match = UPDATE_SHARD_PATTERN.match(path.name)
        if match:
            numbered_paths.append((int(match.group(1)), path))

    paths.extend(path for _, path in sorted(numbered_paths, key=lambda item: item[0]))
    return [
        UpdateShard(name=path.name, path=path, channels=_dedupe_channels(_read_channels(path)))
        for path in paths
    ]


def load_update_channels(shard_name: str) -> list[dict]:
    for shard in list_legacy_update_shards():
        if shard.name == shard_name:
            return shard.channels
    return []


def get_legacy_update_shard_names() -> list[str]:
    return [shard.name for shard in list_legacy_update_shards()]


def get_first_update_shard_name() -> str:
    names = get_legacy_update_shard_names()
    return names[0] if names else UPDATE_BASE_FILE_NAME


def get_next_update_shard_name(current_name: str, done_names: set[str]) -> str:
    names = get_legacy_update_shard_names()
    if not names:
        return UPDATE_BASE_FILE_NAME

    candidates = [name for name in names if name not in done_names]
    if not candidates:
        return names[0]

    if current_name in names:
        start_index = (names.index(current_name) + 1) % len(names)
    else:
        start_index = 0

    ordered = names[start_index:] + names[:start_index]
    for name in ordered:
        if name in candidates:
            return name
    return candidates[0]


def has_multiple_update_shards() -> bool:
    return len(get_legacy_update_shard_names()) > 1


def _all_update_usernames() -> set[str]:
    usernames = set()
    for shard in list_legacy_update_shards():
        usernames.update(_channel_key(channel) for channel in shard.channels)
    return usernames


def _all_bootstrap_usernames() -> set[str]:
    usernames = set()
    for shard in list_bootstrap_shards():
        usernames.update(_channel_key(channel) for channel in shard.channels)
    return usernames


def _update_path_for_index(index: int) -> Path:
    if index == 0:
        return _base_update_file_path()
    return _channels_dir() / f"update_channel_{index:03d}.json"


def _bootstrap_path_for_index(index: int) -> Path:
    if index == 0:
        return _base_bootstrap_file_path()
    return _channels_dir() / f"bootstrap_channel_{index:03d}.json"


def _next_update_shard_path() -> Path:
    max_index = 0
    for shard in list_legacy_update_shards():
        match = UPDATE_SHARD_PATTERN.match(shard.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return _channels_dir() / f"update_channel_{max_index + 1:03d}.json"


def _next_bootstrap_shard_path() -> Path:
    max_index = 0
    for shard in list_bootstrap_shards():
        match = re.match(r"^bootstrap_channel_(\d{3})\.json$", shard.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return _channels_dir() / f"bootstrap_channel_{max_index + 1:03d}.json"


def _append_to_update_shard(channel: dict) -> str:
    for shard in list_legacy_update_shards():
        if len(shard.channels) < UPDATE_CHANNEL_FILE_LIMIT:
            channels = _dedupe_channels([*shard.channels, channel])
            _write_channels(shard.path, channels)
            return shard.name

    path = _next_update_shard_path()
    _write_channels(path, [channel])
    return path.name


def _append_to_bootstrap_shard(channel: dict) -> str:
    for shard in list_bootstrap_shards():
        if len(shard.channels) < BOOTSTRAP_CHANNEL_FILE_LIMIT:
            channels = _dedupe_channels([*shard.channels, channel])
            _write_channels(shard.path, channels)
            return shard.name

    path = _next_bootstrap_shard_path()
    _write_channels(path, [channel])
    return path.name


def _remove_from_update_shards(username_key: str):
    for shard in list_legacy_update_shards():
        channels = [
            channel for channel in shard.channels
            if _channel_key(channel) != username_key
        ]
        if len(channels) != len(shard.channels):
            _write_channels(shard.path, channels)


def _remove_from_bootstrap_shards(username_key: str):
    for shard in list_bootstrap_shards():
        channels = [
            channel for channel in shard.channels
            if _channel_key(channel) != username_key
        ]
        if len(channels) != len(shard.channels):
            _write_channels(shard.path, channels)


@asynccontextmanager
async def registry_lock(r, owner: str):
    while True:
        acquired = await r.set(RedisKeys.CHANNEL_REGISTRY_LOCK, owner, nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            break
        await asyncio.sleep(LOCK_RETRY_SECONDS)

    try:
        yield
    finally:
        lock_owner = await r.get(RedisKeys.CHANNEL_REGISTRY_LOCK)
        if lock_owner == owner:
            await r.delete(RedisKeys.CHANNEL_REGISTRY_LOCK)


@asynccontextmanager
async def update_registry_lock(r, owner: str):
    while True:
        acquired = await r.set(RedisKeys.REGISTRY_UPDATE_LOCK, owner, nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            break
        await asyncio.sleep(LOCK_RETRY_SECONDS)

    try:
        yield
    finally:
        lock_owner = await r.get(RedisKeys.REGISTRY_UPDATE_LOCK)
        if lock_owner == owner:
            await r.delete(RedisKeys.REGISTRY_UPDATE_LOCK)


def _redis_shard_sort_key(shard_name: str) -> tuple[int, str]:
    match = re.match(rf"^{UPDATE_REDIS_SHARD_PREFIX}_(\d{{3}})$", shard_name)
    if match:
        return int(match.group(1)), shard_name
    return 999999, shard_name


def _next_redis_update_shard_name(shards: list[str]) -> str:
    max_index = 0
    for shard in shards:
        match = re.match(rf"^{UPDATE_REDIS_SHARD_PREFIX}_(\d{{3}})$", shard)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"{UPDATE_REDIS_SHARD_PREFIX}_{max_index + 1:03d}"


async def list_update_shards(r) -> list[str]:
    shards = await r.smembers(RedisKeys.REGISTRY_UPDATE_SHARDS)
    return sorted(shards, key=_redis_shard_sort_key)


async def has_update_channel(r, username: str) -> bool:
    username_key = _normalize_username(username)
    if not username_key:
        return False
    return bool(await r.sismember(RedisKeys.REGISTRY_UPDATE_ALL, username_key))


async def get_first_update_shard(r) -> str:
    shards = await list_update_shards(r)
    return shards[0] if shards else UPDATE_REDIS_BASE_SHARD


async def get_next_update_shard(r, current_name: str, done_names: set[str]) -> str:
    shards = await list_update_shards(r)
    if not shards:
        return UPDATE_REDIS_BASE_SHARD

    candidates = [name for name in shards if name not in done_names]
    if not candidates:
        return shards[0]

    if current_name in shards:
        start_index = (shards.index(current_name) + 1) % len(shards)
    else:
        start_index = 0

    ordered = shards[start_index:] + shards[:start_index]
    for name in ordered:
        if name in candidates:
            return name
    return candidates[0]


async def load_update_shard(r, shard_name: str) -> list[dict]:
    raw_channels = await r.lrange(RedisKeys.registry_update_channels(shard_name), 0, -1)
    channels = []
    seen = set()
    for raw in raw_channels:
        try:
            channel = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        username_key = _channel_key(channel)
        if not username_key or username_key in seen:
            continue
        seen.add(username_key)
        channels.append(channel)
    return channels


async def _find_update_shard_for_username(r, username_key: str) -> str | None:
    for shard in await list_update_shards(r):
        if await r.sismember(RedisKeys.registry_update_set(shard), username_key):
            return shard
    return None


async def _rewrite_update_shard(r, shard_name: str, channels: list[dict]):
    await r.delete(
        RedisKeys.registry_update_channels(shard_name),
        RedisKeys.registry_update_set(shard_name),
    )
    if not channels:
        await r.srem(RedisKeys.REGISTRY_UPDATE_SHARDS, shard_name)
        return

    await r.sadd(RedisKeys.REGISTRY_UPDATE_SHARDS, shard_name)
    for channel in channels:
        username_key = _channel_key(channel)
        if not username_key:
            continue
        await r.rpush(RedisKeys.registry_update_channels(shard_name), json.dumps(channel))
        await r.sadd(RedisKeys.registry_update_set(shard_name), username_key)


async def add_update_channel(r, channel: dict, owner: str) -> str | None:
    username_key = _channel_key(channel)
    if not username_key:
        return None

    async with update_registry_lock(r, owner):
        existing_shard = await _find_update_shard_for_username(r, username_key)
        await r.set(RedisKeys.registry_update_channel(username_key), json.dumps(channel))
        await r.sadd(RedisKeys.REGISTRY_UPDATE_ALL, username_key)

        if existing_shard:
            channels = await load_update_shard(r, existing_shard)
            updated_channels = list(channels)
            replaced = False
            for index, existing_channel in enumerate(updated_channels):
                if _channel_key(existing_channel) == username_key:
                    updated_channels[index] = channel
                    replaced = True
                    break
            if not replaced:
                updated_channels.append(channel)
            await _rewrite_update_shard(r, existing_shard, updated_channels)

            log_info(
                logger,
                "Update channel already exists in Redis registry",
                "update_registry_duplicate",
                username=channel.get("username"),
                update_shard=existing_shard,
            )
            return existing_shard

        shards = await list_update_shards(r)
        target_shard = None
        for shard in shards:
            count = await r.llen(RedisKeys.registry_update_channels(shard))
            if count < UPDATE_CHANNEL_FILE_LIMIT:
                target_shard = shard
                break

        if target_shard is None:
            target_shard = _next_redis_update_shard_name(shards)

        await r.sadd(RedisKeys.REGISTRY_UPDATE_SHARDS, target_shard)
        await r.rpush(RedisKeys.registry_update_channels(target_shard), json.dumps(channel))
        await r.sadd(RedisKeys.registry_update_set(target_shard), username_key)

        log_info(
            logger,
            "Channel added to Redis update registry",
            "update_registry_channel_added",
            username=channel.get("username"),
            update_shard=target_shard,
        )
        return target_shard


async def remove_update_channel(r, username: str, owner: str) -> bool:
    username_key = _normalize_username(username)
    if not username_key:
        return False

    async with update_registry_lock(r, owner):
        removed = False
        for shard in await list_update_shards(r):
            channels = await load_update_shard(r, shard)
            remaining = [
                channel for channel in channels
                if _channel_key(channel) != username_key
            ]
            if len(remaining) != len(channels):
                await _rewrite_update_shard(r, shard, remaining)
                removed = True

        await r.srem(RedisKeys.REGISTRY_UPDATE_ALL, username_key)
        await r.delete(RedisKeys.registry_update_channel(username_key))

        if removed:
            log_info(
                logger,
                "Channel removed from Redis update registry",
                "update_registry_channel_removed",
                username=username,
            )

        return removed


async def add_bootstrap_channel(r, channel: dict, owner: str) -> str | None:
    username_key = _channel_key(channel)
    if not username_key:
        return None

    async with registry_lock(r, owner):
        ensure_registry_files()
        if username_key in _all_bootstrap_usernames():
            return None

        shard_name = _append_to_bootstrap_shard(channel)
        log_info(
            logger,
            "Channel added to bootstrap registry",
            "channel_registry_added_to_bootstrap",
            username=channel.get("username"),
            bootstrap_file=shard_name,
        )
        return shard_name


async def move_bootstrap_to_update(r, channel: dict, owner: str) -> str | None:
    username_key = _channel_key(channel)
    if not username_key:
        return None

    async with registry_lock(r, owner):
        ensure_registry_files()
        _remove_from_bootstrap_shards(username_key)

        shard_name = await add_update_channel(r, channel, owner)
        log_info(
            logger,
            "Bootstrap channel moved to Redis update registry",
            "channel_registry_move_to_update",
            username=channel.get("username"),
            update_shard=shard_name,
        )
        return shard_name


async def move_update_to_bootstrap(r, channel: dict, owner: str) -> str | None:
    username_key = _channel_key(channel)
    if not username_key:
        return None

    async with registry_lock(r, owner):
        ensure_registry_files()

        await remove_update_channel(r, channel.get("username"), owner)

        if username_key in _all_bootstrap_usernames():
            log_info(
                logger,
                "Update channel already exists in bootstrap registry",
                "channel_registry_move_to_bootstrap_duplicate",
                username=channel.get("username"),
            )
            return None

        shard_name = _append_to_bootstrap_shard(channel)
        log_info(
            logger,
            "Update channel moved to bootstrap registry",
            "channel_registry_move_to_bootstrap",
            username=channel.get("username"),
            bootstrap_file=shard_name,
        )
        return shard_name


async def remove_bootstrap_channel(r, channel_or_username: dict | str, owner: str) -> bool:
    if isinstance(channel_or_username, dict):
        username_key = _channel_key(channel_or_username)
        username = channel_or_username.get("username")
    else:
        username_key = _normalize_username(channel_or_username)
        username = channel_or_username

    if not username_key:
        return False

    async with registry_lock(r, owner):
        ensure_registry_files()

        removed = False
        for shard in list_bootstrap_shards():
            channels = [
                channel for channel in shard.channels
                if _channel_key(channel) != username_key
            ]
            if len(channels) != len(shard.channels):
                _write_channels(shard.path, channels)
                removed = True

        if removed:
            log_info(
                logger,
                "Bootstrap channel removed from registry",
                "channel_registry_removed_from_bootstrap",
                username=username,
            )

        return removed


async def normalize_registry(r, owner: str):
    await sync_update_registry_from_legacy_files(r, owner)

    async with registry_lock(r, owner):
        ensure_registry_files()

        bootstrap_channels = [
            channel for channel in load_all_bootstrap_channels()
            if not await has_update_channel(r, channel.get("username"))
        ]
        bootstrap_channels = _dedupe_channels(bootstrap_channels)

        bootstrap_chunks = [
            bootstrap_channels[index:index + BOOTSTRAP_CHANNEL_FILE_LIMIT]
            for index in range(0, len(bootstrap_channels), BOOTSTRAP_CHANNEL_FILE_LIMIT)
        ] or [[]]

        for index, chunk in enumerate(bootstrap_chunks):
            _write_channels(_bootstrap_path_for_index(index), chunk)

        valid_bootstrap_paths = {
            _bootstrap_path_for_index(index)
            for index in range(len(bootstrap_chunks))
        }
        for shard in list_bootstrap_shards():
            if shard.path not in valid_bootstrap_paths and shard.path.exists():
                shard.path.unlink()


async def sync_update_registry_from_legacy_files(r, owner: str) -> dict:
    imported = 0
    moved_to_bootstrap = 0

    for shard in list_legacy_update_shards():
        for channel in shard.channels:
            username = channel.get("username")
            if not username:
                continue
            if await r.sismember(RedisKeys.QUEUE_FAILED, username):
                continue
            if await r.sismember(RedisKeys.retired_key(), username):
                continue

            if await has_update_channel(r, username):
                continue

            last_id = await r.get(RedisKeys.last_id(username))
            if last_id:
                await add_update_channel(r, channel, owner)
                imported += 1
            else:
                added = await add_bootstrap_channel(r, channel, owner)
                if added:
                    moved_to_bootstrap += 1

    if imported or moved_to_bootstrap:
        log_info(
            logger,
            "Legacy update files synced into Redis registry",
            "legacy_update_registry_synced",
            imported=imported,
            moved_to_bootstrap=moved_to_bootstrap,
        )

    return {
        "imported": imported,
        "moved_to_bootstrap": moved_to_bootstrap,
    }


async def sync_bootstrap_file_limit(r, owner: str):
    async with registry_lock(r, owner):
        channels = [
            channel for channel in load_all_bootstrap_channels()
            if not await has_update_channel(r, channel.get("username"))
        ]
        chunks = [
            channels[index:index + BOOTSTRAP_CHANNEL_FILE_LIMIT]
            for index in range(0, len(channels), BOOTSTRAP_CHANNEL_FILE_LIMIT)
        ] or [[]]

        for index, chunk in enumerate(chunks):
            _write_channels(_bootstrap_path_for_index(index), chunk)

        valid_paths = {_bootstrap_path_for_index(index) for index in range(len(chunks))}
        for shard in list_bootstrap_shards():
            if shard.path not in valid_paths and shard.path.exists():
                shard.path.unlink()
