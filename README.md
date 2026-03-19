<p align="center">
  <img src="./img/logo.jpg" width="480"/>
</p>

<div align="center">

### 🚀 Distributed Telegram Crawler at Scale
Multi-account | Fault-tolerant | Production-ready pipeline

<p>
  <img src="https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/Telethon-MTProto-blue?style=for-the-badge&logo=telegram" />
  <img src="https://img.shields.io/badge/Redis-Queue-red?style=for-the-badge&logo=redis" />
  <img src="https://img.shields.io/badge/PostgreSQL-Database-336791?style=for-the-badge&logo=postgresql" />
</p>

</div>

---

## 💡 Use Cases
- Data mining Telegram channels
- Building analytics pipelines
- Content aggregation systems

## 🎯 Overview

Distributed Telegram crawler built for **real production use**:

* multi-account workers (parallel)
* Redis queue (cycle-based)
* crash recovery
* floodwait handling
* incremental message sync

---

## ✨ Core Features

* 🔀 Multi-account parallel workers
* 🔁 Redis queue (pending / processing / done / failed)
* 🛡️ FloodWait handling (auto retry / cooldown)
* 💾 Incremental fetch (`last_message_id`)
* 🪦 Dead channel auto-retire
* 🔄 Crash recovery (requeue stuck jobs)
* 📊 Live progress logs

---

## 🖼️ Architecture

<p align="center">
  <img src="https://ik.imagekit.io/deceuior6/Me/photo_6183954858826731123_y.jpg" width="800" />
</p>

---

## ⚡ Example Output (Real Logs)

```log
2026-03-12 18:51:20,450 [INFO] __main__: 🚀 Telegram Importer Starting...
2026-03-12 18:51:20,682 [INFO] __main__: 📋 Total channels loaded: 736
2026-03-12 18:53:47,552 [INFO] __main__: 📥 Added to queue: 736
2026-03-12 18:53:47,672 [INFO] __main__: 📊 Queue — Pending: 736 | Done: 0
2026-03-12 18:53:47,672 [INFO] workers.worker_manager: 🏁 Starting 3 workers in parallel...
2026-03-12 18:53:47,673 [INFO] workers.worker: 🚀 Worker worker_1 starting...
2026-03-12 18:53:47,675 [INFO] workers.worker: 🚀 Worker worker_2 starting...
2026-03-12 18:53:47,676 [INFO] workers.worker: 🚀 Worker worker_3 starting...
2026-03-12 18:53:52,912 [INFO] workers.worker: ✅ Worker worker_1 connected to Telegram
2026-03-12 18:53:52,942 [INFO] workers.worker: ✅ Worker worker_3 connected to Telegram
2026-03-12 18:53:52,952 [INFO] workers.worker: [worker_1] 📡 Processing @nexchannelonepieceliveactionmm (is_adults=False)
2026-03-12 18:53:53,169 [INFO] workers.worker: [worker_3] 📡 Processing @BarmetomMovieSearchGp (is_adults=False)
2026-03-12 18:53:53,354 [INFO] workers.worker: ✅ Worker worker_2 connected to Telegram
2026-03-12 18:53:53,395 [INFO] workers.worker: [worker_2] 📡 Processing @AnimeOngoing_MM (is_adults=False)
2026-03-12 18:53:55,184 [INFO] importers.message_importer: ⏳ Waited 1.3s before fetching messages for @nexchannelonepieceliveactionmm
2026-03-12 18:53:55,434 [INFO] importers.message_importer: 🆕 @nexchannelonepieceliveactionmm — first time fetch
2026-03-12 18:53:57,743 [INFO] importers.message_importer: 📌 @nexchannelonepieceliveactionmm — first REAL message id=4, starting from there
2026-03-12 18:53:58,715 [INFO] importers.message_importer: ⏳ Waited 2.9s before fetching messages for @BarmetomMovieSearchGp
2026-03-12 18:53:58,794 [INFO] importers.message_importer: 🆕 @BarmetomMovieSearchGp — first time fetch
2026-03-12 18:53:59,770 [INFO] importers.message_importer: 📌 @BarmetomMovieSearchGp — first REAL message id=3, starting from there
2026-03-12 18:54:00,556 [INFO] importers.message_importer: ⏳ Waited 2.7s before fetching messages for @AnimeOngoing_MM
2026-03-12 18:54:00,635 [INFO] importers.message_importer: 🆕 @AnimeOngoing_MM — first time fetch
2026-03-12 18:54:06,033 [INFO] importers.message_importer: 📌 @AnimeOngoing_MM — first REAL message id=2919, starting from there
2026-03-12 18:54:10,067 [INFO] importers.message_importer: 💾 @BarmetomMovieSearchGp — saved last_id=106 to Redis
2026-03-12 18:54:10,067 [INFO] importers.message_importer: 💬 Saved 33 new messages from @BarmetomMovieSearchGp
2026-03-12 18:54:12,927 [INFO] importers.message_importer: 💾 @nexchannelonepieceliveactionmm — saved last_id=57 to Redis
2026-03-12 18:54:12,927 [INFO] importers.message_importer: 💬 Saved 49 new messages from @nexchannelonepieceliveactionmm

```

---

## ⚙️ .env Example

```env
# ----------------+ APP CONFIG +------------------
FILE_NUMBER=001
ADMIN_ID=xxxxx
BOT_TOKEN=xxxxxx
MAX_CONCURRENT_ACCOUNTS=5
LOG_LEVEL=INFO


# Performance Settings - !( LEAVE default unless necessary ) 
MAX_ATTEMPTS=2
MAX_MESSAGES_PER_CHANNEL=10
MIN_DELAY_BETWEEN_CHANNELS=5.0  
MAX_DELAY_BETWEEN_CHANNELS=10.0 
MIN_DELAY_BETWEEN_MESSAGES=1.0 
MAX_DELAY_BETWEEN_MESSAGES=2.0
PROGRESS_REPORT_INTERVAL=60
RESOLVE_MIN_GAP=5.0
EMPTY_CYCLE_THRESHOLD=10
CYCLE_MIN_SECONDS=30

# File Settings
CHANNELS_DIR=data/channels
RETIRED_FILE=data/retired/retired.json
SUSPENDED_FILE=data/suspended.json
FAILED_FILE=data/failed_channels.json
ACCOUNTS_FILE=data/accounts.json


#---------------- DATABASE -----------------
REDIS_URL="rediss://dxxxxxx"
DATABASE_URL="postgres://axxxxxx"

# ---------------- worker file path ----------------
WORKER_PATH=/home/yuchann/yuChannBot/dev/worker
VENV_PATH=/home/yuchann/yuChannBot/dev/worker/venv
```

---

## 📦 accounts.json Example

```json
[
  {
    "name": "acc1",
    "api_id": 12345,
    "api_hash": "xxx",
    "phone": "+60123...",
    "session": "sessions/acc1.session",
    "enable": true
  }
]
```

---

## 📂 channels (batch)

```json
[
  { "username": "channelA", "is_adults": false },
  { "username": "channelB", "is_adults": false }
]
```

---

## ⚡ Quick Start

```bash
git clone https://github.com/samsouta/yuchann-spider
cd yuchann-spider

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
# install
pip install -r requirements.txt

# first time (login sessions)
python login_acc.py

# run crawler
python main.py
```

---

## 🔄 How System Works

```text
load channels
  ↓
push → redis queue
  ↓
workers consume
  ↓
fetch messages
  ↓
save to postgres
  ↓
cycle complete → reset
```

---

## 📊 Output Data (Example)

**Channel**

| id | username | members |
| -- | -------- | ------- |
| 1  | anime_mm | 120K    |

**Message**

| id  | type | content |
| --- | ---- | ------- |
| 101 | TEXT | hello   |

---

## 🧠 Engineering Notes

* avoids duplicate fetch using `last_id`
* distributes load across accounts
* handles long floodwait safely
* auto removes useless channels

---

## 🖥️ Preview

<p align="center">
  <img src="https://github.com/user-attachments/assets/f8fe8f01-4325-4313-a4d6-c12139e99e8b" width="800" />
</p>

---

## 🔒 Source Code
Private. DM [@samsouta](https://t.me/samsouta) on Telegram to request access.
Core logic is private. This repo shows system design and usage.

---

## ⚠️ Disclaimer

Use responsibly. Follow Telegram ToS.

---

<div align="center">

⭐ star if useful

</div>
