<p align="center">
  <img src="./img/logo.jpg" width="480"/>
</p>

<div align="center">

### 🕷️ High-Performance Distributed Telegram Crawler
Real-time scraping • Multi-account scaling • Fault-tolerant pipeline

<p>
  <img src="https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/Telethon-MTProto-blue?style=for-the-badge&logo=telegram" />
  <img src="https://img.shields.io/badge/Redis-Queue-red?style=for-the-badge&logo=redis" />
  <img src="https://img.shields.io/badge/PostgreSQL-Database-336791?style=for-the-badge&logo=postgresql" />
</p>

</div>

---

# YuChann Spider

Production Telegram channel scraper built with Python, Telethon, Redis, and PostgreSQL.

The worker has two independent lanes:

- `bootstrap` workers initialize new channels from JSON input files.
- `update` workers continuously sync existing channels from the Redis update registry.

Redis is the runtime source of truth for update ownership. JSON files are only used as manual input for new/bootstrap channels.

---

## Architecture

![YuChann Spider worker flow](./img/ar.png)

```text
bootstrap_channel*.json
  -> bootstrap queue
  -> bootstrap workers
  -> Telegram validation + historical bootstrap
  -> last_id:<username>
  -> Redis update registry
  -> update cycle snapshots
  -> update workers
  -> PostgreSQL
```

---

## Core Concepts

### Bootstrap JSON Files

Bootstrap files are the Git/manual input source for channels:

```text
worker/data/channels/bootstrap_channel.json
worker/data/channels/bootstrap_channel_001.json
worker/data/channels/bootstrap_channel_002.json
...
```

Each file stores channel objects:

```json
[
  { "username": "example_channel", "is_adults": false }
]
```

When adding new channels, add them to a bootstrap file. Do not manually maintain `update_channel.json` for production update ownership.

### Redis Update Registry

After a channel has a Redis cursor:

```text
last_id:<username>
```

it belongs to the Redis update registry.

Redis keys:

```text
registry:update:shards
registry:update:all
registry:update:update_001:channels
registry:update:update_001:set
registry:update:channel:<username>
```

Purpose:

- `registry:update:shards`: shard names such as `update_001`, `update_002`
- `registry:update:all`: all update usernames for dedupe
- `registry:update:<shard>:channels`: full channel JSON objects for one shard
- `registry:update:<shard>:set`: usernames inside one shard
- `registry:update:channel:<username>`: direct channel JSON lookup

### Runtime Queues

Queues are runtime work state, not ownership:

```text
queue:pending:bootstrap
queue:pending:bootstrap:set
queue:processing:bootstrap

queue:pending:update
queue:pending:update:set
queue:processing:update
```

The update registry says what channels exist. The pending queues say what jobs are waiting in the current cycle.

---

## Worker Lanes

### Bootstrap Lane

Bootstrap workers process new or no-cursor channels.

Flow:

1. Read all `bootstrap_channel*.json` files.
2. Skip failed or retired channels.
3. If the channel already exists in Redis update registry, remove it from bootstrap JSON.
4. If `last_id:<username>` exists, promote it to Redis update registry without fetching messages.
5. If no `last_id` exists, validate the Telegram channel.
6. If the channel is restricted, invalid, private, inaccessible, or permanently broken, mark it failed and remove it from bootstrap JSON.
7. If valid, scan historical messages oldest-to-newest.
8. Save the first real message batch.
9. Store `last_id:<username>`.
10. Promote the channel to Redis update registry.
11. Remove it from bootstrap JSON.

Bootstrap workers sleep when there is no bootstrap work:

```env
BOOTSTRAP_IDLE_SLEEP_SECONDS=60
```

### Update Lane

Update workers process channels that already have `last_id`.

Flow:

1. Select the active Redis update shard, for example `update_001`.
2. Build a stable cycle snapshot into:
   ```text
   queue:update:cycle:usernames
   queue:update:cycle:shard
   ```
3. Push snapshot jobs into `queue:pending:update`.
4. Workers pull jobs into `queue:processing:update`.
5. Each channel imports only messages newer than `last_id`.
6. On completion, remove the username from the cycle snapshot.
7. When pending, processing, and snapshot are empty, move to the next update shard.

Newly bootstrapped channels do not interrupt an active update cycle. They join on the next cycle.

---

## Update Shards

Redis update shards keep update cycles small and predictable.

Example:

```text
update_001 = first 1000 channels
update_002 = next 1000 channels
update_003 = next 1000 channels
```

The size is controlled by:

```env
UPDATE_CHANNEL_FILE_LIMIT=1000
```

Despite the setting name, update ownership is now stored in Redis, not update JSON files.

---

## Channel File Rules

Use bootstrap files for input:

```text
bootstrap_channel.json
bootstrap_channel_001.json
bootstrap_channel_002.json
```

Legacy update files:

```text
update_channel.json
update_channel_001.json
```

These are treated only as one-time legacy seed files on startup. The worker imports valid legacy update channels into Redis update registry, but does not write update ownership back to JSON.

Recommended production rule:

- Add new channels only to `bootstrap_channel*.json`.
- Keep `update_channel.json` empty after migration.
- Keep Redis persistence enabled.

---

## Startup Flow

![YuChann Spider startup flow](./img/startup-flow.png)

On startup:

1. Ensure registry files exist.
2. Import legacy `update_channel*.json` into Redis update registry if needed.
3. Normalize bootstrap files and remove channels already in update registry.
4. Clear stale pending and processing queues.
5. Build a fresh update cycle snapshot from Redis update shards.
6. Sync bootstrap queue from bootstrap JSON files.
7. Start update and bootstrap worker pools.

---

## Environment

```env
# Worker pools
BOOTSTRAP_WORKER=1
UPDATE_WORKER=3
MAX_CONCURRENT_ACCOUNTS=10

# Message import
MAX_ATTEMPTS=2
MAX_MESSAGES_PER_CHANNEL=100
FIRST_IMPORT_SCAN_LIMIT=5000
BOOTSTRAP_NO_REAL_THRESHOLD=2

# Timing
MIN_DELAY_BETWEEN_CHANNELS=2.0
MAX_DELAY_BETWEEN_CHANNELS=5.0
MIN_DELAY_BETWEEN_MESSAGES=2.0
MAX_DELAY_BETWEEN_MESSAGES=3.0
RESOLVE_MIN_GAP=5.0
CYCLE_MIN_SECONDS=30
BOOTSTRAP_IDLE_SLEEP_SECONDS=60
UPDATE_IDLE_SLEEP_SECONDS=60
PROGRESS_REPORT_INTERVAL=30

# Registry files
CHANNELS_DIR=data/channels
BOOTSTRAP_CHANNEL_FILE_LIMIT=50
UPDATE_CHANNEL_FILE_LIMIT=1000

# State backup files
RETIRED_FILE=data/retired/retired.json
SUSPENDED_FILE=data/suspended.json
FAILED_FILE=data/failed_channels.json
ACCOUNTS_FILE=data/accounts.json

# Services
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgres://user:pass@localhost:5432/db
```

---

## Redis State Reference

Update registry:

```text
registry:update:lock
registry:update:shards
registry:update:all
registry:update:<shard>:channels
registry:update:<shard>:set
registry:update:channel:<username>
```

Update cycle:

```text
queue:update:current_shard
queue:update:shard:done
queue:update:cycle:id
queue:update:cycle:shard
queue:update:cycle:usernames
queue:update:idle:since
```

Queues:

```text
queue:pending:update
queue:pending:update:set
queue:processing:update
queue:pending:bootstrap
queue:pending:bootstrap:set
queue:processing:bootstrap
```

Terminal and cursor state:

```text
last_id:<username>
queue:failed
channel:retired
channel:empty_cycles:<username>
channel:bootstrap_no_real:<username>
```

---

## Logs

The worker uses structured multi-line logs.

Example:

```log
2026-05-21 15:51:32 | INFO    | importers.message_importer
  event: message_bootstrap_historical_saved
  msg: Historical bootstrap saved
  first_real_id: 5508
  highest_saved_id: 5649
  saved_count: 10
  scanned_count: 254
  username: TheMovieWorldchannel
```

Useful events:

- `bootstrap_queue_synced`
- `message_bootstrap_historical_saved`
- `bootstrap_channel_permanent_failure`
- `channel_registry_move_to_update`
- `update_cycle_snapshot_built`
- `update_cycle_started`
- `progress_report`

---

## Quick Start

```bash
cd worker
source venv/bin/activate

# first-time account login
python login_acc.py

# run worker
python main.py
```

---

## Operational Notes

- Redis persistence is required in production because update ownership lives in Redis.
- If Redis is flushed, update ownership can only be rebuilt from bootstrap JSON and legacy update JSON seed files.
- `update_channel.json` is no longer the active update source.
- `queue:pending:update` is still required; it is the current cycle work queue, while `registry:update:*` is ownership.
- If a bootstrap channel already has `last_id`, it is promoted to update registry without Telegram message fetching.
- Permanently bad bootstrap channels are marked failed and removed from bootstrap JSON.

---

## 🔒 Source Code
Private. DM [@samsouta](https://t.me/samsouta) on Telegram to request access.

## 🔒 Note
Core production logic is private.
This repository demonstrates system architecture and workflow.

## ⚠️ Disclaimer

Use responsibly. Follow Telegram ToS.

---