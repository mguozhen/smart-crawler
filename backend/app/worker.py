"""采集 worker —— 轮询任务队列，执行采集任务。

两种用法：
  · 独立容器： python -m app.worker     （服务化部署，可起多副本）
  · 进程内线程：main.py 在单机模式下起一个 run_loop 守护线程
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import time

from .analytics import recompute
from .runner import claim_job, execute_job

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [worker] %(message)s")
logger = logging.getLogger("smart-crawler.worker")

WORKER_ID = os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
POLL_INTERVAL = int(os.environ.get("WORKER_POLL", "10"))
_running = True


def run_loop(should_continue=None) -> None:
    """领取并执行队列任务，直到 should_continue() 为假。"""
    should_continue = should_continue or (lambda: _running)
    logger.info("worker %s 启动，轮询间隔 %ds", WORKER_ID, POLL_INTERVAL)
    while should_continue():
        try:
            job_id = claim_job(WORKER_ID)
        except Exception as exc:
            logger.error("领取任务失败: %s", exc)
            time.sleep(POLL_INTERVAL)
            continue
        if job_id is None:
            time.sleep(POLL_INTERVAL)
            continue
        try:
            result = execute_job(job_id)
            if result["status"] == "success":
                recompute(result["site"])
            logger.info("job %s %s -> %s", job_id, result["site"],
                        result["status"])
        except Exception as exc:
            logger.error("job %s 执行异常: %s", job_id, exc)
    logger.info("worker %s 退出", WORKER_ID)


def _stop(*_):
    global _running
    _running = False


def main() -> None:
    from .db import init_db
    init_db()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop()


if __name__ == "__main__":
    main()
