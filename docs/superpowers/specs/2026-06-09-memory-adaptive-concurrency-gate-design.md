# 内存自适应并发闸 (Memory-Adaptive Concurrency Gate)

- 日期: 2026-06-09
- 状态: 设计已批准,待实现
- 相关: [[2026-06-08-ondemand-batch-retry-design]]、worker.py、main.py

## 背景与动机

生产 NAS 是 **4 核 Intel N100 / 16GB**,且是跑了 20+ 容器的共享机器(实测内存余 ~10GB、负载 ~1.1)。

抓取并发现状(已核实):
- **按需抓取(on-demand)**:`app/ondemand/queue.py` 单 worker 线程,严格串行,同时只 1 个浏览器。本设计**不动**它。
- **定时采集(scheduled)**:`app/worker.py::run_loop`,由 `WORKER_THREADS`(默认 **1**)决定起几个线程,每线程领一条 job 跑一个真浏览器(scrapling StealthyFetcher / Camoufox)。

真浏览器单实例瞬时吃 300–800MB。用户希望把 `WORKER_THREADS` 调高(3–4)让定时采集更快,但担心多个浏览器在这台小机器上把内存吃爆(OOM kill 或拖慢导致抓取超时)。

容器**未设 per-container 内存限制**(`docker stats` 显示所有容器 LIMIT = 主机总内存 15.41GiB),因此 OOM 风险是主机级的,应以**主机级可用内存**为信号。

参考 crawl4ai 的 `MemoryAdaptiveDispatcher`(`memory_threshold_percent` 超阈暂停),但其面向 `arun_many` 批量提交语义,不套用其类结构——只取"内存超阈暂停领新"这一核心思想,适配现有"线程领 job"模型。

## 目标

- 让用户可安全地调高 `WORKER_THREADS`:当主机已用内存超过阈值时,worker **暂停领取新 job、不起新浏览器**,内存回落后自动恢复。
- 零新增第三方依赖(不引入 psutil)。
- 完全向后兼容:默认行为温和,可一键关闭。

## 非目标

- 不改 on-demand 单 worker 串行路径(无并发压力)。
- 不做完整 dispatcher / 批量提交框架(YAGNI)。
- 不做静态信号量上限(用户明确要感知实时内存、能反映邻居容器波动)。
- 不中断已在执行的 job(闸只挡"领新",不杀运行中的浏览器)。

## 架构

### 组件 1:`app/memory_gate.py`(新增)

单一职责:读主机内存 + 提供"等到内存 OK"的阻塞原语。零依赖,读 Linux `/proc/meminfo`。

```python
def available_percent() -> float:
    """可用内存百分比 = MemAvailable / MemTotal * 100。
    读不到(非 Linux / 无 /proc/meminfo / 解析失败)时返回 100.0 —— fail-open,
    永不阻塞抓取。"""

def used_percent() -> float:
    """已用内存百分比 = 100 - available_percent()。"""

def wait_until_ok(threshold_pct: float, *,
                  check_interval: float = 2.0,
                  max_wait: float = 300.0,
                  should_continue=None) -> bool:
    """阻塞直到 used_percent() < threshold_pct。
    - threshold_pct <= 0 或 >= 100:视为禁用,立即返回 True(关闸)。
    - 每 check_interval 秒查一次;累计等待达 max_wait 仍超阈 → 返回 False。
    - should_continue() 提供且变为假 → 提前返回 False(worker 停机时不卡)。
    - 内存 OK → 返回 True。
    返回值语义:True = 可以继续领 job;False = 本轮别领,回上层循环重判。"""
```

`/proc/meminfo` 解析:取 `MemTotal:` 与 `MemAvailable:`(单位 kB)。两者缺一 → fail-open 返回 100.0。

**测试缝(testability seam)**:`available_percent(meminfo_path="/proc/meminfo")` 接受可选路径参数,测试时传入临时文件;`wait_until_ok` 内部用模块级 `time.sleep`(测试 monkeypatch 加速)并通过调用 `used_percent()`(测试 monkeypatch 其返回值模拟内存高/低)。`should_continue=None` 时默认视为恒真(`lambda: True`),与现有 `run_loop` 一致。

### 组件 2:`app/worker.py::run_loop` 接入

在 `claim_job` **之前**插入内存闸。伪代码:

```python
while should_continue():
    if MEM_THRESHOLD and not memory_gate.wait_until_ok(
            MEM_THRESHOLD, check_interval=MEM_CHECK_INTERVAL,
            max_wait=MEM_MAX_WAIT, should_continue=should_continue):
        continue            # 没等到内存回落(超时/停机)→ 回循环重判,本轮不领 job
    job_id = claim_job(WORKER_ID)
    ...                     # 其余逻辑不变
```

效果:内存吃紧时所有 worker 线程停在闸前 sleep,不领 job、不起浏览器;内存回落自动恢复。未领的 job 仍是 DB 里的 `pending`,不丢失。

## 配置(环境变量)

| 变量 | 默认 | 含义 |
|---|---|---|
| `MEM_GATE_THRESHOLD` | `80` | 已用内存 ≥ 此百分比则暂停领新 job。设 `0` 或 `100` 关闸。默认偏保守(余 ~3.2GB),邻居容器波动时给浏览器收尾留头部空间。 |
| `MEM_GATE_CHECK_INTERVAL` | `2` | 闸内轮询间隔(秒) |
| `MEM_GATE_MAX_WAIT` | `300` | 单轮最多等待(秒);超时回 run_loop 重判,避免永久 hang |

在 `worker.py` 模块级读取(与现有 `POLL_INTERVAL` / `JOB_TIMEOUT` 同款 `os.environ.get`)。

## 数据流

```
run_loop 每轮:
  memory_gate.wait_until_ok(MEM_THRESHOLD)
    ├─ MEM_THRESHOLD 关闭(0/100)         → 立即 True
    ├─ used% < 阈值                        → 立即 True → claim_job → execute_job(起浏览器)
    └─ used% ≥ 阈值                        → sleep(check_interval) 重查
            ├─ 回落到 < 阈值               → True → 继续领 job
            ├─ 累计等待 ≥ max_wait         → False → continue(本轮不领,回循环)
            └─ should_continue() 变假      → False → 循环条件随即结束,优雅停机
```

## 错误处理与边界

- **fail-open**:读 `/proc/meminfo` 失败/非 Linux → `available_percent()` 返回 100.0(used 0%),闸永不阻塞。本地 Mac 跑测试、非容器环境均不受影响。
- 闸只挡领新,**不影响已在执行的 job**,不会中途杀浏览器。
- in-process 模式(`main.py` 单机起的 worker 线程)与独立 worker 容器**都生效**,因为都走 `run_loop`。`should_continue` 在两种模式下均已存在,沿用。
- on-demand 路径(`ondemand/queue.py`)不接入,保持串行现状。
- 阈值默认 80% 留有头部空间(余 ~3.2GB / 16GB),足够一个浏览器实例收尾,降低 OOM 概率。
- **安全优先**:超时(`max_wait`)后**回循环重判、绝不超时硬领**;只要内存仍高位,worker 就一直等,永不在内存紧张时强起浏览器。

## 测试计划(TDD)

`tests/test_memory_gate.py`:
1. `available_percent` 正确解析注入的 `/proc/meminfo` 文本(给定 MemTotal/MemAvailable → 期望百分比)。
2. 解析失败 / 文件缺字段 → 返回 100.0(fail-open)。
3. `wait_until_ok`:内存 OK(used < 阈值)→ 立即返回 True,不 sleep。
4. `wait_until_ok`:持续超阈 → 在 `max_wait` 内返回 False(用很小的 max_wait + monkeypatch used_percent 恒高 + mock sleep 加速)。
5. `wait_until_ok`:`should_continue()` 返回假 → 提前返回 False。
6. `wait_until_ok`:阈值 0 或 100 → 立即 True(关闸)。

`tests/test_worker_memory_gate.py`(或并入现有 worker 测试):
7. 闸返回 False 时,本轮**不调用** `claim_job`(monkeypatch `memory_gate.wait_until_ok` → False,断言 `claim_job` 未被调用);闸 True 时正常领取。

所有内存读取在测试中通过 monkeypatch 注入,不依赖真实主机内存,保证确定性。

## 部署与兼容

- 默认 `MEM_GATE_THRESHOLD=80` 即生效;在内存充裕(当前余 10GB,used ~33%)时闸永远放行,无行为变化。
- 调高并发时:在 worker 容器 / compose env 设 `WORKER_THREADS=3` + 保留默认闸即可。
- 回滚:设 `MEM_GATE_THRESHOLD=0` 关闸,或还原 worker.py。
- 更新 `.env.example` 注明三个新变量。
