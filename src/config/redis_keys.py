# app/config/redis_keys.py


class RedisKeys:
    # - PREFIX -
    CHANNEL_PREFIX = "channel"
    QUEUE_PREFIX = "queue"
    TRACKER_PREFIX = "tracker"
    WORKER_PREFIX = "worker"
    LAST_ID_PREFIX = "last_id"
    LAST_RESOLVE_TIME_PREFIX = "last_resolve_time"
    WORKER_COOLDOWN_PREFIX = "worker_cooldown:"
    MESSAGE_LAST_ID_PREFIX = f"{LAST_ID_PREFIX}:"

    # - CHANNEL EMPTY CYCLE -
    @staticmethod
    def empty_cycle(username):
        return f"{RedisKeys.CHANNEL_PREFIX}:empty_cycles:{username}"

    @staticmethod
    def bootstrap_no_real(username):
        return f"{RedisKeys.CHANNEL_PREFIX}:bootstrap_no_real:{username}"

    # - CHANNEL RETIRED -
    CHANNEL_RETIRED = f"{CHANNEL_PREFIX}:retired"

    @staticmethod
    def retired_key():
        return RedisKeys.CHANNEL_RETIRED

    # - QUEUE -
    QUEUE_PENDING = f"{QUEUE_PREFIX}:pending"
    QUEUE_PENDING_SET = f"{QUEUE_PREFIX}:pending:set"
    QUEUE_PROCESSING = f"{QUEUE_PREFIX}:processing"
    QUEUE_PENDING_UPDATE = f"{QUEUE_PREFIX}:pending:update"
    QUEUE_PENDING_UPDATE_SET = f"{QUEUE_PREFIX}:pending:update:set"
    QUEUE_PROCESSING_UPDATE = f"{QUEUE_PREFIX}:processing:update"
    QUEUE_PENDING_BOOTSTRAP = f"{QUEUE_PREFIX}:pending:bootstrap"
    QUEUE_PENDING_BOOTSTRAP_SET = f"{QUEUE_PREFIX}:pending:bootstrap:set"
    QUEUE_PROCESSING_BOOTSTRAP = f"{QUEUE_PREFIX}:processing:bootstrap"
    QUEUE_DONE = f"{QUEUE_PREFIX}:done"
    QUEUE_FAILED = f"{QUEUE_PREFIX}:failed"
    QUEUE_CYCLE = f"{QUEUE_PREFIX}:cycle"
    QUEUE_CYCLE_START = f"{QUEUE_PREFIX}:cycle:start"
    QUEUE_RESET_LOCK = f"{QUEUE_PREFIX}:reset:lock"
    QUEUE_CURRENT_BATCH = f"{QUEUE_PREFIX}:current_batch"
    QUEUE_UPDATE_CURRENT_SHARD = f"{QUEUE_PREFIX}:update:current_shard"
    QUEUE_UPDATE_SHARD_DONE = f"{QUEUE_PREFIX}:update:shard:done"
    QUEUE_UPDATE_CYCLE_ID = f"{QUEUE_PREFIX}:update:cycle:id"
    QUEUE_UPDATE_CYCLE_SHARD = f"{QUEUE_PREFIX}:update:cycle:shard"
    QUEUE_UPDATE_CYCLE_USERNAMES = f"{QUEUE_PREFIX}:update:cycle:usernames"
    QUEUE_UPDATE_IDLE_SINCE = f"{QUEUE_PREFIX}:update:idle:since"
    CHANNEL_REGISTRY_LOCK = "channel_registry:lock"
    REGISTRY_UPDATE_LOCK = "registry:update:lock"
    REGISTRY_UPDATE_SHARDS = "registry:update:shards"
    REGISTRY_UPDATE_ALL = "registry:update:all"

    # Legacy aliases kept so old Redis state can be cleaned during startup.
    QUEUE_UPDATE_CURRENT_FILE = f"{QUEUE_PREFIX}:update:current_file"
    QUEUE_UPDATE_FILE_DONE = f"{QUEUE_PREFIX}:update:file:done"
    QUEUE_UPDATE_CYCLE_FILE = f"{QUEUE_PREFIX}:update:cycle:file"

    @staticmethod
    def registry_update_channels(shard: str):
        return f"registry:update:{shard}:channels"

    @staticmethod
    def registry_update_set(shard: str):
        return f"registry:update:{shard}:set"

    @staticmethod
    def registry_update_channel(username: str):
        return f"registry:update:channel:{username}"

    @staticmethod
    def queue_pending(role: str):
        return {
            "update": RedisKeys.QUEUE_PENDING_UPDATE,
            "bootstrap": RedisKeys.QUEUE_PENDING_BOOTSTRAP,
        }[role]

    @staticmethod
    def queue_pending_set(role: str):
        return {
            "update": RedisKeys.QUEUE_PENDING_UPDATE_SET,
            "bootstrap": RedisKeys.QUEUE_PENDING_BOOTSTRAP_SET,
        }[role]

    @staticmethod
    def queue_processing(role: str):
        return {
            "update": RedisKeys.QUEUE_PROCESSING_UPDATE,
            "bootstrap": RedisKeys.QUEUE_PROCESSING_BOOTSTRAP,
        }[role]

    # - TRACKER -
    TRACKER_SUSPENDED = f"{TRACKER_PREFIX}:suspended"

    @staticmethod
    def worker_cooldown(worker_id):
        return f"{RedisKeys.WORKER_COOLDOWN_PREFIX}{worker_id}"

    # - MESSAGE LAST ID -
    @staticmethod
    def last_id(channel_username):
        return f"{RedisKeys.MESSAGE_LAST_ID_PREFIX}{channel_username}"

    # - PEER RESOLVE THROTTLE -
    PEER_LAST_RESOLVE_TIME_PREFIX = f"{LAST_RESOLVE_TIME_PREFIX}:"

    @staticmethod
    def last_resolve_time(account_id):
        return f"{RedisKeys.PEER_LAST_RESOLVE_TIME_PREFIX}{account_id}"
