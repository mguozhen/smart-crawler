"""独立调度容器入口 —— 启动 APScheduler，把采集任务投递到队列。

服务化部署时作为 sc-scheduler 容器运行： python -m app.scheduler_main
"""
from __future__ import annotations

import logging
import time

from .db import init_db
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [scheduler] %(message)s")


def main() -> None:
    init_db()
    start_scheduler()
    logging.getLogger("smart-crawler").info("调度容器已启动，进入常驻")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
