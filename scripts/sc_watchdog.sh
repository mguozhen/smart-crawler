#!/bin/sh
# smart-crawler 自愈 watchdog · NAS host cron 每 5 min 跑一次
#
# 1. 数 smart-crawler 容器里 app.worker 进程数, 少于 TARGET 就补
# 2. 数 vidaxl pending, 少于 MIN_PENDING 就追加新 jobs
# 3. 日志写 /tmp/sc_watchdog.log
#
# 部署: scp 到 NAS · `chmod +x` · 加 cron `*/5 * * * * /volume1/docker/smart-crawler/sc_watchdog.sh`
#
# 退出码:
#   0 = OK / 已修复
#   1 = container 没起 (需人工)

set -u
LOG=/tmp/sc_watchdog.log
TARGET_WORKERS=4
MIN_PENDING=30
TS=$(date -Iseconds)

log() {
  echo "[$TS] $1" >> "$LOG"
}

# 1. container 在不在
if ! docker ps --format '{{.Names}}' | grep -q '^smart-crawler$'; then
  log "container smart-crawler not running · ABORT"
  exit 1
fi

# 2. 数 worker 进程
WORKER_COUNT=$(docker exec smart-crawler sh -c "for p in /proc/[0-9]*; do cat \$p/cmdline 2>/dev/null | tr '\0' ' ' | grep -c 'app.worker'; done" 2>/dev/null | grep -c '^1$')

NEED=$((TARGET_WORKERS - WORKER_COUNT))
if [ "$NEED" -gt 0 ]; then
  log "worker=$WORKER_COUNT < target=$TARGET_WORKERS · spawning $NEED"
  i=0
  while [ "$i" -lt "$NEED" ]; do
    docker exec -d smart-crawler python -m app.worker
    i=$((i + 1))
  done
else
  log "worker=$WORKER_COUNT OK"
fi

# 3. queue 深度 · pending vidaxl < MIN_PENDING 就追加
PENDING=$(docker exec smart-crawler-pg psql -U smart_crawler -d smart_crawler -t -A -c "SELECT count(*) FROM crawl_jobs WHERE site LIKE 'vidaxl%' AND status='pending'" 2>/dev/null || echo 0)

if [ "$PENDING" -lt "$MIN_PENDING" ]; then
  log "pending=$PENDING < min=$MIN_PENDING · enqueueing 50 (5 × 10 sites)"
  docker exec smart-crawler-pg psql -U smart_crawler -d smart_crawler -c "
    INSERT INTO crawl_jobs (site, status, trigger, created_at)
    SELECT site, 'pending', 'watchdog-refill', NOW()
    FROM (VALUES
      ('vidaxl_nl'), ('vidaxl_de'), ('vidaxl_ie'), ('vidaxl_it'), ('vidaxl_es'),
      ('vidaxl_pt'), ('vidaxl_pl'), ('vidaxl_uk'), ('vidaxl_ro'), ('vidaxl_fr')
    ) AS s(site), generate_series(1, 5)
  " >/dev/null 2>&1
else
  log "pending=$PENDING OK"
fi

# 4. 顺便清掉 worker=NULL 的 orphan (worker 死了但 DB 还显示 running)
docker exec smart-crawler-pg psql -U smart_crawler -d smart_crawler -c "
  UPDATE crawl_jobs SET status='failed', error='watchdog-orphan-cleanup',
         finished_at=NOW(), duration_sec=EXTRACT(EPOCH FROM (NOW()-started_at))
  WHERE status='running' AND worker IS NULL AND started_at < NOW() - INTERVAL '15 minutes'
" >/dev/null 2>&1

# 5. 清真 stale (>90 min · 远超 vidaxl 单 run 最大耗时 25min · 留 65 min 兜底)
# 之前 30min 太激进 · 把健康 17-25min 的 run 也杀了 · 改 90min 安全得多
docker exec smart-crawler-pg psql -U smart_crawler -d smart_crawler -c "
  UPDATE crawl_jobs SET status='failed', error='watchdog-stale-90min',
         finished_at=NOW(), duration_sec=EXTRACT(EPOCH FROM (NOW()-started_at))
  WHERE status='running' AND started_at < NOW() - INTERVAL '90 minutes'
" >/dev/null 2>&1

log "tick done"
