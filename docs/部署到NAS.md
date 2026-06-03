# smart-crawler 部署到 NAS + 绑定 smartcrawler.io

> 目标：把 smart-crawler 跑在内网 NAS（192.168.1.80）上，通过 Cloudflare Tunnel
> 用 `smartcrawler.io` 对外访问。域名已注册（Cloudflare）。

---

## 0. 网络现状（实测 2026-05-16）

| 主机 | 地址 | 端口 | 说明 |
|------|------|------|------|
| 采集机（本机） | 108.95.61.129（公网静态） | — | AT&T 静态块；**公网 IP，受 UGOS SSH 白名单 DROP，无法直连 NAS SSH** |
| NAS | 192.168.1.80 | :22 关 / :23 telnet 开 / :80 / :443 / :5000 管理面板 | UGREEN UGOS |
| iMac | 192.168.1.87 | :22 SSH 开 / :5900 VNC 开 | RFC1918，**可达 NAS** |

> ⚠ UGOS 的 SSH 仅允许 RFC1918 源地址，本采集机是公网 IP 会被静默 DROP。
> 部署操作须从一台内网机（iMac 192.168.1.87）发起，或直接用 NAS :5000 管理面板。

---

## 1. 部署方式 A —— NAS Docker 面板（推荐，零命令行）

1. 浏览器打开 `http://192.168.1.80:5000` 登录 UGOS
2. 进入「Docker」应用 →「项目 / Compose」→ 新建项目
3. 上传本仓库（或 `git clone`），选择 `docker-compose.yml`
4. 启动 → 容器 `smart-crawler` 跑在 NAS 的 `:8077`
5. 验证：`http://192.168.1.80:8077` 出现登录页，用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录

> 生产环境上线前仍需执行第 5 节的 preflight / backup / verify；Docker 面板只是启动方式，
> 不替代数据库备份和迁移验收。

## 2. 部署方式 B —— 命令行（从 iMac 操作）

```bash
# 在 iMac（192.168.1.87）上：
ssh <imac-user>@192.168.1.87
git clone git@github.com:mguozhen/smart-crawler.git
cd smart-crawler
docker compose up -d --build          # NAS 若装了 Docker，也可 scp 过去再起
# → http://<host>:8077
```

镜像不含 Playwright 浏览器（采集主力 curl_cffi）；如后续要采强反爬站，
在容器内 `playwright install chromium` 即可。

---

## 3. 绑定 smartcrawler.io（Cloudflare Tunnel）

域名在 Cloudflare，用 **Cloudflare Tunnel** 把内网服务暴露出去，无需公网端口映射。

1. Cloudflare Zero Trust 控制台 → Networks → Tunnels → Create tunnel
2. 命名 `smart-crawler`，复制 **Tunnel Token**
3. 把 token 写入仓库根目录 `.env`：
   ```
   TUNNEL_TOKEN=eyJh...（粘贴）
   SC_SECRET=<改一个强随机串>
   ```
4. 启用 compose 里的 tunnel 段：
   ```bash
   docker compose --profile tunnel up -d
   ```
5. 在 Tunnel 的 Public Hostname 配置：
   - Hostname: `smartcrawler.io`（及 `www.smartcrawler.io`）
   - Service: `http://smart-crawler:8077`
6. Cloudflare 自动加好 DNS CNAME → 访问 `https://smartcrawler.io` 即看板登录页

> 复用现有 flatkey Cloudflare Tunnel 基础设施亦可：在现有 tunnel 加一条
> Public Hostname `smartcrawler.io → http://192.168.1.80:8077` 即可。

---

## 4. 管理员账号

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ADMIN_USERNAME` | `admin` | 初始管理员用户名 |
| `ADMIN_EMAIL` | `admin@local.smartcrawler` | 初始管理员邮箱 |
| `ADMIN_PASSWORD` | 无 | 初始管理员密码；未设置时首次启动随机生成并打印到日志 |

首次启动 `init_db()` 自动创建管理员。生产部署请显式设置 `ADMIN_PASSWORD`
和强随机 `SC_SECRET`，不要依赖随机日志密码。

改密码（在容器内）：
```bash
docker exec -it smart-crawler python -c "
from app.db import session_scope; from app.models import User
from app.auth import hash_password
with session_scope() as s:
    u=s.query(User).filter(User.username=='admin').first()
    u.password_hash=hash_password('新密码')
"
```

---

## 5. 安全部署流程（推荐）

在 NAS 项目目录执行。目标是：先确认可迁移、再备份、再启动、最后验收；任何一步失败都停止。

### 5.1 配置生产密钥

根目录 `.env` 至少要显式设置：

```bash
POSTGRES_PASSWORD=<强随机>
SC_SECRET=<强随机>
ADMIN_USERNAME=<管理员用户名>
ADMIN_PASSWORD=<强随机>
SMARTCRAWLER_ADMIN_USERNAME=$ADMIN_USERNAME
SMARTCRAWLER_ADMIN_PASSWORD=$ADMIN_PASSWORD
SMARTCRAWLER_API_KEY=<一个已有或预创建的 sck_ API key>
SMARTCRAWLER_BASE_URL=http://127.0.0.1:8077
```

不要使用 `change-me` / `changeme` / 短密码。`SMARTCRAWLER_API_KEY` 用于部署后验证
REST v2 和 MCP，不会写进仓库。

如果要启用 LLM 相关功能（Reddit playbook、VOC/NLP 分析、AI 摘要），再加：

```bash
ANTHROPIC_API_KEY=<flatkey 或 Anthropic 兼容 key>
# 或 OPENAI_API_KEY=<flatkey 兼容 key>
LLM_BASE_URL=https://api.flatkey.ai
LLM_MODEL=claude-haiku-4-5
```

没有配置 LLM key 时，看板、登录、workspace、普通爬虫、warehouse、MCP 主工具仍可用；
只是 LLM-only 功能会返回“未配置 key”。如果本次部署必须包含 LLM 能力，执行：

```bash
REQUIRE_LLM=1 scripts/deploy/preflight.sh
```

`REQUIRE_LLM=1` 会做一次极小的真实网关调用；如果 key 已禁用、额度不足或模型不可用，
会在部署前失败。

### 5.2 部署前检查

```bash
scripts/deploy/preflight.sh
```

它会检查：

- git 工作区是否干净
- 生产密钥是否为强随机值
- 仓库中是否混入真实 API key / proxy 密码
- workspace/auth 迁移 dry-run 会新增哪些表和字段
- Python 编译与后端测试是否通过

本地临时验证可用：

```bash
ALLOW_WEAK_SECRETS=1 RUN_TESTS=0 scripts/deploy/preflight.sh
```

### 5.3 备份

```bash
scripts/deploy/backup.sh
```

脚本会在 `backups/deploy/<timestamp>/` 下保存：

- 数据库备份：SQLite `.db` 或 PostgreSQL `pg_dump`
- `.env`、compose、`sites.yaml`、代理模板等部署配置
- 当前 git commit / git status
- 迁移前数据快照：关键表行数、缺失字段、待 backfill 数量

### 5.4 一键受保护部署

```bash
scripts/deploy/guarded_deploy.sh
```

可选参数：

```bash
DEPLOY_BRANCH=codex/auth-registration-login scripts/deploy/guarded_deploy.sh
COMPOSE_FILE=docker-compose.service.yml APP_SERVICE=web scripts/deploy/guarded_deploy.sh
```

流程：

1. 跑 preflight
2. 备份
3. 可选切换/拉取指定分支
4. `docker compose up -d --build`
5. 容器内执行 `workspace_deploy_guard.py apply`
6. 调 `/health`、登录、workspace、sites、keys、v2、MCP tools 做部署后验收

### 5.5 单独验收

如果已经手动启动容器，可以单独跑：

```bash
scripts/deploy/post_deploy_verify.sh
```

必须看到所有项目都是 `[OK]` 才算上线完成。

### 5.6 回滚

`guarded_deploy.sh` 结束时会打印备份目录。需要回滚时：

```bash
CONFIRM_RESTORE=YES scripts/deploy/restore.sh backups/deploy/<timestamp>
docker compose up -d --build
```

PostgreSQL 会用 `pg_restore --clean --if-exists` 恢复；SQLite 会恢复 `.db` 文件并清理 WAL/SHM。

---

## 6. 上线后人工检查清单

- [ ] `https://smartcrawler.io` 出现登录页，HTTPS 证书正常（Cloudflare 自动签）
- [ ] 管理员登录成功，`/api/me` 返回 `workspaces` 和 `current_workspace_id`
- [ ] `Internal Workspace` 可见，原有站点清单已进入 workspace site list
- [ ] 老 API Key 仍能调用 `/api/v2/sources`
- [ ] MCP `tools/list` 包含 `query_warehouse`、`scrape_url`、`crawl_site`
- [ ] `/api/sites`、报告、导出按当前 workspace 过滤
- [ ] 商品、促销、评论等 warehouse 数据行数与备份快照一致或只增不减
- [ ] 已设置强随机 `ADMIN_PASSWORD` 和 `SC_SECRET`
- [ ] `data/` 或 PostgreSQL 卷已挂载，容器重建不丢数据
- [ ] 定时调度生效（容器内 APScheduler 自动起）
- [ ] 如需采 Vidaxl：配置 `backend/proxies.txt` 住宅代理（见风控评估报告）
