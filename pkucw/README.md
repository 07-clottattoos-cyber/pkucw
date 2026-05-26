# pkucw

`pkucw` 是一个面向北大教学网（`course.pku.edu.cn`）的命令行工具。  
它用真实浏览器维持登录状态，同时把课程、通知、教学内容、作业、课堂实录等常见操作整理成稳定可复用的 CLI。

项目同时兼顾两类使用者：

- 人类用户：在终端里直接查询和下载
- agent / 脚本：通过稳定子命令和 `--json` 输出调用

## 当前能力

- 主命令：`pkucw`
- 兼容别名：`cw`、`courseweb`
- 终端内登录
- 账号保存到 macOS Keychain
- Blackboard 会话状态本地持久化
- 课程上下文切换：`pkucw use`、`pkucw current`
- 人类可读输出 + `--json`
- `zsh` / `bash` / `fish` 补全
- 通知列表与详情
- 教学内容列表、树形查看、详情和下载
- 作业列表与详情
- 作业说明与附件下载
- 安全默认的作业提交流程
- 课堂实录列表、详情与下载
- 成绩列表与详情
- 长期课程网监控
- MCP agent 信息源
- 本地 HTTP/SSE 实时更新流
- 课程级消息订阅、webhook 和 console 通知

## 安装

```bash
git clone <GitHub 仓库地址> pkucw
cd pkucw
./install.sh
```

安装后验证：

```bash
pkucw --version
./scripts/smoke-test.sh
```

## OpenClaw 安装

如果你希望让 OpenClaw 同时安装 `pkucw` 工具和 `pkucw-cli` skill，可以直接运行：

```bash
git clone <GitHub 仓库地址> courseweb-cli
cd courseweb-cli
./scripts/install-openclaw.sh
```

默认行为：

- 安装 `pkucw` 本体
- 将 skill 安装到 `~/.openclaw/workspace/skills/pkucw-cli`
- 默认用符号链接方式挂载 skill，方便后续跟随仓库更新

常用覆写方式：

```bash
OPENCLAW_HOME=~/.openclaw ./scripts/install-openclaw.sh
OPENCLAW_SKILLS_DIR=./.openclaw-skills ./scripts/install-openclaw.sh
PKUCW_SKILL_INSTALL_MODE=copy ./scripts/install-openclaw.sh
PKUCW_SKIP_TOOL_INSTALL=1 ./scripts/install-openclaw.sh
```

安装后可验证：

```bash
pkucw --version
openclaw skills list | grep pkucw-cli
```

如果你要让 OpenClaw 代理自己完成安装，可以直接给它这段提示词：

```text
请在当前仓库安装 pkucw 和 pkucw-cli skill。
要求：
1. 运行 ./scripts/install-openclaw.sh
2. 确认 pkucw --version 可用
3. 确认 openclaw skills list 中能看到 pkucw-cli
4. 如果失败，优先修复 PATH、Python、skill 目录问题
5. 最后返回安装结果、pkucw 版本、skill 安装路径和验证命令输出摘要
```

## 首次使用

推荐流程：

```bash
pkucw accounts add
pkucw login
pkucw ls --current
```

如果你已经保存过默认账号，也可以直接：

```bash
pkucw login
```

常用账号命令：

```bash
pkucw accounts list
pkucw accounts show
pkucw accounts use <account>
pkucw accounts remove <account>
```

## 常用命令

```bash
pkucw login
pkucw ls --current
pkucw use "有机化学 (一)"
pkucw current
pkucw info
pkucw announcements list
pkucw contents tree
pkucw assignments list
pkucw assignments download "L4作业提交入口" --output ./downloads/assignment
pkucw recordings latest --output ./downloads/latest
pkucw grades list
```

在执行过 `pkucw use <course>` 之后，多数课程内命令都可以省略课程参数。

## 会话恢复

如果本地 Blackboard 会话过期，课程相关命令会先做一次快速探测，再尝试用已保存账号自动恢复。  
如果站点本身状态异常，命令会尽快返回明确错误，而不是长时间无响应。

## 面向 agent / 脚本

- 推荐统一加 `--json`
- 命令名保持稳定，不建议 agent 去猜课程名
- 建议先 `pkucw ls --current --json`，再 `pkucw use "<精确课程名>" --json`
- OpenClaw 可配合 [pkucw skill](skills/pkucw-cli/SKILL.md) 一起使用
- 仓库根目录提供 `./pkucw` 和 `./pkucw-cli` 包装脚本，PATH 不稳定时 agent 可直接调用

通知详情说明：

- `pkucw announcements show "<通知标题片段>" --json` 会返回完整通知详情
- 关键字段包括 `announcement.title`、`announcement.published_at`、`announcement.author`
- `body_text` 是去标签后的正文全文
- `body_html` 是原始 HTML 正文
- `announcement.asset_urls` 会列出通知中的附件或图片链接

## 远程部署

通用 SSH 部署：

```bash
./scripts/deploy-remote.sh user@host
```

如果你有固定目标主机，也可以：

```bash
PKUCW_DEPLOY_HOST=user@host ./scripts/deploy-host.sh
```

## 本地状态目录

默认状态目录：

```text
~/.courseweb
```

重要文件：

- `~/.courseweb/session.json`：当前会话元数据
- `~/.courseweb/storage_state.json`：Playwright 浏览器状态
- `~/.courseweb/accounts.json`：账号元数据
- `~/.courseweb/config.json`：monitor、agent 和订阅配置
- `~/.courseweb/monitor.sqlite3`：课程快照、更新事件、订阅和通知投递状态

密码不会写进这些 JSON 文件；在 macOS 上，账号密码保存在系统 Keychain 中。

## Long-running monitor

监控功能会复用 `pkucw login` 保存的浏览器会话和本地账号配置。第一次扫描默认只建立 baseline，不推送历史资源：

```bash
pkucw monitor scan --json
pkucw monitor run
pkucw monitor status --json
pkucw monitor updates --since 2026-05-26T00:00:00+08:00 --json
```

数据流是：

```text
CourseWeb browser session -> CourseSnapshot -> DiffEngine -> CourseUpdateEvent -> SQLite -> subscriptions -> notifier/SSE/MCP
```

当前抓取覆盖课程、公告、作业、教学内容、课堂实录和成绩。成绩更新默认只通知“成绩已更新”，不在事件推送里暴露具体分数。

## Course-level subscriptions

订阅配置位于 `~/.courseweb/config.json`。课程级配置会覆盖 default 配置；课程 `enabled=false` 时事件仍写入 SQLite，但不主动推送。

```json
{
  "monitor": {
    "enabled": true,
    "interval_seconds": 300,
    "first_scan_notify": false
  },
  "subscriptions": {
    "default": {
      "enabled": true,
      "mode": "realtime",
      "event_types": [
        "grade.updated",
        "assignment.created",
        "assignment.updated",
        "assignment.deadline_changed",
        "content.created",
        "content.updated",
        "recording.created",
        "announcement.created"
      ],
      "channels": ["sse"],
      "include_sensitive_grade_content": false,
      "quiet_hours": {"enabled": false, "start": "23:30", "end": "08:30"}
    },
    "courses": {
      "COURSE_ID_QUANTUM": {
        "enabled": true,
        "display_name": "量子力学",
        "mode": "realtime",
        "event_types": ["grade.updated", "assignment.created", "assignment.deadline_changed"],
        "channels": ["sse", "hermes"],
        "include_sensitive_grade_content": false,
        "keywords": {"include": [], "exclude": []}
      }
    }
  }
}
```

CLI 快捷配置：

```bash
pkucw monitor subscribe-course COURSE_ID_QUANTUM --mode realtime --channel sse --channel hermes
pkucw monitor mute-course COURSE_ID_MUTED
pkucw monitor unmute-course COURSE_ID_MUTED
pkucw monitor test-notify --json
```

## SSE realtime updates

启动本地 agent server：

```bash
pkucw agent token --json
pkucw agent serve --host 127.0.0.1 --port 8765
```

HTTP endpoints：

```bash
curl http://127.0.0.1:8765/health
curl 'http://127.0.0.1:8765/updates?limit=20'
curl -N 'http://127.0.0.1:8765/events?token=<TOKEN>'
```

`/events` 使用 SSE：

```text
event: course_update
id: <event_id>
data: <CourseUpdateEvent JSON>
```

服务支持 `Last-Event-ID`，断线重连后会从 SQLite 补发漏掉的事件。默认监听 `127.0.0.1`；远程访问必须使用 bearer token 或 `token` query 参数。

## Webhook notification

Webhook channel 会 POST 脱敏后的 JSON，并可用 HMAC 签名：

```json
{
  "notifiers": {
    "webhook": {
      "url": "https://example.invalid/courseweb",
      "secret": "change-me"
    }
  },
  "subscriptions": {
    "default": {
      "channels": ["sse", "webhook"],
      "include_sensitive_grade_content": false
    }
  }
}
```

Headers：

```text
X-Courseweb-Event: assignment.deadline_changed
X-Courseweb-Event-Id: <event_id>
X-Courseweb-Signature: sha256=<hmac>
```

## MCP agent integration

启动 MCP stdio server：

```bash
pkucw agent mcp
```

提供的 tools：

- `list_courses`
- `get_course_snapshot`
- `list_recent_updates`
- `get_update_detail`
- `list_subscriptions`
- `update_course_subscription`
- `acknowledge_update`
- `search_course_resources`
- `list_cli_commands`
- `run_cli`
- `cli_<command_path>`：自动从 argparse 命令树生成的 CLI tool，例如 `cli_status`、`cli_courses_list`、`cli_assignments_show`、`cli_grades_list`

默认 MCP 只读。只有 `config.agent.allow_modify_subscriptions=true` 时，agent 才能调用 `update_course_subscription` 修改课程订阅；token 和 webhook secret 不会通过 `list_subscriptions` 暴露。

### MCP access to every CLI command

`pkucw agent mcp` 会把现有 CLI 命令树暴露给 agent：

```json
{
  "name": "cli_assignments_show",
  "arguments": {
    "args": ["--course", "COURSE_ID", "作业标题片段"],
    "json": true
  }
}
```

也可以使用通用入口：

```json
{
  "name": "run_cli",
  "arguments": {
    "argv": ["assignments", "show", "--course", "COURSE_ID", "作业标题片段"]
  }
}
```

安全默认值：

- 只读命令默认可执行，例如 `status`、`courses list`、`announcements list`、`assignments show`、`grades list`。
- 有副作用命令会列出但默认拒绝执行，例如 `login`、`logout`、`use`、`download`、`submit`、`monitor scan`、`monitor subscribe-course`、`agent token`。
- 长运行命令会列出但默认拒绝执行，例如 `monitor run`、`agent serve`、`agent mcp`。
- 要允许副作用命令，需要同时设置 `config.agent.allow_cli_mutations=true` 并在 tool 参数中传 `allow_mutation=true`。
- 要允许长运行命令，需要同时设置 `config.agent.allow_cli_long_running=true` 并在 tool 参数中传 `allow_long_running=true`。
- `--password-stdin`、`--final-submit`、`--confirm-final-submit` 等高风险参数不允许通过 MCP bridge 传入。

## Hermes integration test

Hermes 只是展示和测试目标，核心架构不绑定 Hermes 私有 channel。优先把 `pkucw agent mcp` 作为 MCP server 接入 Hermes，并验证：

```text
list_courses
list_recent_updates
get_course_snapshot
get_update_detail
list_subscriptions
```

如果 Hermes 暂时不能直接接 MCP，则启动 SSE：

```bash
ssh 1cxm1@1cxm1demac-mini.local
git clone https://github.com/07-clottattoos-cyber/courseweb-cli.git pkucw
cd pkucw
python -m pip install -e '.[dev]'
pkucw login
pkucw monitor scan --json
pkucw agent token --json
pkucw agent serve --host 127.0.0.1 --port 8765
curl http://127.0.0.1:8765/health
curl 'http://127.0.0.1:8765/updates?limit=20'
curl -N 'http://127.0.0.1:8765/events?token=<TOKEN>'
```

一个很薄的 Hermes adapter 可以连接 `/events`，收到 `CourseUpdateEvent` 后按课程级订阅规则过滤，再推送到 Hermes 消息流。当前仓库保留 `hermes` notifier channel 的接口；如果本机 Hermes 没有稳定 CLI/API，就使用 MCP 或 SSE 完成集成测试。

## Privacy and grade redaction

- cookie、token、webhook secret 不写入日志。
- HTTP/SSE 默认只监听 `127.0.0.1`。
- 不要把 `pkucw agent serve` 直接暴露到公网。
- 远程访问必须启用 bearer token。
- 成绩默认脱敏，课程级 `include_sensitive_grade_content` 默认是 `false`。
- Webhook payload 默认脱敏成绩。
- `~/.courseweb/config.json` 会尽量写成 `0600` 权限。

## 文档

- [使用说明](docs/overview.md)
- [架构说明](docs/architecture.md)
- [账号管理](docs/account-management.md)
- [技术报告](docs/technical-report.md)
