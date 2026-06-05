---
name: pkucw-cli
description: 使用本地 pkucw CLI 安全操作北大教学网。覆盖账号管理、登录、会话恢复、课程解析、通知、教学内容、作业、下载、课堂实录，以及受保护的作业写操作。
version: 0.4.0b2
metadata:
  tags: [pku, blackboard, cli, pkucw, course, announcements, contents, assignments, recordings, downloads]
---

# pkucw CLI Skill

这份 skill 是给 agent 的执行手册，不是给人看的简介。
目标只有一个：让 agent 在使用 `pkucw` 时尽量不猜命令、不猜参数、不乱切浏览器、不把中间话术当完成结果。

## 0. 完整命令地图

`pkucw` 当前稳定暴露的顶层命令是：

```bash
pkucw completion
pkucw auth
pkucw accounts
pkucw login
pkucw logout
pkucw status
pkucw courses
pkucw ls
pkucw use
pkucw current
pkucw doctor
pkucw course
pkucw info
pkucw announcements
pkucw contents
pkucw assignments
pkucw recordings
```

顶层隐藏兼容命令也真实存在：

```bash
pkucw download-content
pkucw download-recording
pkucw download-latest-recording
pkucw latest-recording
pkucw download-assignment
pkucw submit-assignment
pkucw list-courses
pkucw __complete --
```

重要约束：

- canonical commands 永远优先于隐藏兼容命令
- agent 只有在用户历史流程、外部集成或旧 prompt 明确依赖时，才退回隐藏兼容命令
- 如果 canonical command 能表达任务，就不要改用隐藏命令

## 1. 适用范围

当任务属于以下任一类时，必须优先使用 `pkucw`：

- 北大教学网登录与会话检查
- 课程列表、活动课程、课程元数据
- 课程通知查看
- 教学内容查看与下载
- 作业查看、作业附件/说明下载
- Blackboard 站内作业草稿/最终提交
- 课堂实录查看与下载

以下情况不要绕开 `pkucw`：

- 不要用浏览器重新点击 Blackboard 页面，除非 `pkucw` 明确不支持该任务
- 不要用 `curl`、`wget` 直接抓 Blackboard 文件
- 不要自己拼 `/bbcswebdav/...` URL
- 不要自己猜 `.m3u8`、分片地址、登录 cookie

## 2. 执行入口

优先级从高到低：

1. `pkucw`
2. `./pkucw`
3. `cd <repo-root> && ./pkucw`
4. `~/.openclaw/extensions/pkucw-cli/bin/pkucw`

如果当前就在仓库根目录，最稳的是：

```bash
./pkucw ...
```

不要先尝试这些猜测形式：

- `pkucw-cli ...`
- `python -m pkucw ...`
- `python3 -m pkucw ...`
- `pkucw course list ...`
- `pkucw course <course> ...`
- `pkucw download-recording ...`
- `pkucw download-content ...`
- `pkucw download-assignment ...`

### 2.1 顶层别名和资源别名

这些别名是真实存在的，可以用，但不应由 agent 自己发明新的同义词：

- `accounts` 也可写成 `account`
- `announcements` 也可写成：
  `notice` `notices` `announcement` `notification` `notifications`
- `contents` 也可写成：
  `content` `material` `materials`
- `recordings` 在 agent 纠偏层里兼容：
  `recording` `video` `videos`

强制规则：

- 可以使用这些已知别名
- 不要再造 skill 里没写的别名
- 对用户可读回复，统一仍称“通知 / 教学内容 / 作业 / 课堂实录”

## 3. 总规则

- 读操作优先加 `--json`
- 人类请求一句话就能映射到短流程时，不要展开长篇探索
- 不要猜课程名；先解析，再执行
- 不要猜命令形态；先按本 skill 的命令参考执行
- 不要把“我现在开始下载”“让我查看一下”这种过程话术当成完成结果
- 不要读取待上传文件的正文内容；提交作业时直接把文件路径传给 `pkucw assignments submit --file`
- 下载任务必须满足：
  1. `pkucw ... --json` 返回成功
  2. 从 JSON 中取出落盘路径
  3. 用 `ls -lh` 或 `file` 验证该路径存在
- 如果中途出现无关系统消息，应继续未完成的 `pkucw` 流程，而不是直接结束
- 如果命令失败是因为命令名猜错、参数顺序猜错、课程名没解析准，应立刻纠正，不要跳到浏览器
- 如果 `pkucw` 已经返回了权威业务结论，例如“只读复查状态”“仅支持 Blackboard 站内原生作业”“最终提交受保护”，应直接汇报并停止，不要再尝试旁路入口

### 3.1 明确禁止

以下做法一律视为错误路线：

- `cd ~/pkucw && ./cli.sh ...`
- `pkucw notice ...` 之外自己再造 `notice --course` 语法变体
- `pkucw announcements --course ...` 后立刻结束，不去看返回结果
- 为了提交作业先 `read` 本地 txt / pdf 内容
- 用 `find /...` 全盘扫文件路径
- 下载课堂回放时，看到 `Command still running` 就直接停止
- 在思考文本里写伪 `<tool_call>`，却不真正调用工具
- 在 `pkucw` 已经明确告诉你“当前不可提交/不支持提交”后，继续换别的入口硬试

### 3.2 路径规则

- 用户说“桌面”，默认就是 `~/Desktop`
- 用户说“桌面的 xxx 文件夹”，默认就是 `~/Desktop/xxx`
- 若用户给的是相对路径，先相对当前工作目录解释；若明显是桌面语境，优先转成 `~/Desktop/...`
- 提交作业时，`--file` 优先传绝对路径

### 3.2.1 课程参数规则

很多资源命令同时支持：

- 位置参数 `COURSE`
- 显式旗标 `--course`

真实规则是：

- 二者二选一，优先只用一种
- agent 为了可读性，默认优先用位置参数
- 当一句命令里已有其它位置参数，或为了减少歧义时，可以改用 `--course`
- 不要同时写 `pkucw contents list "<course>" --course "<course>"`

推荐顺序：

- 已经 `pkucw use` 过：省略课程参数
- 需要显式课程但命令后面还有多个位置参数：优先 `--course`
- 简短查询：优先位置参数

### 3.3 长任务规则

如果 `exec` 返回：

- `Command still running (session <ID> ...)`

则必须继续走 `process` 工具：

1. `process log` 或 `process poll`
2. 直到状态变成 `completed` / `failed`
3. 只有拿到最终退出码后，才能下结论

对课堂回放下载尤其如此。不要在看到进度后就结束。

### 3.4 一句命令场景的固定模板

以下场景不要自由发挥，优先用这组模板。

下载某课程最新课件：

```bash
pkucw use "<课程名>" --json
pkucw contents list --json
pkucw contents download "<精确课件标题或内容 ID>" --output-dir ~/Desktop/<目标目录> --json
```

下载某课程最新课堂回放：

```bash
pkucw download-latest-recording --course "<课程名>" --output-dir ~/Desktop/<目标目录> --json
```

查看最新课程通知：

```bash
pkucw use "<课程名>" --json
pkucw announcements ls --json
pkucw announcements show "<精确通知标题>" --json
```

询问课程里有多少条作业、多少条课堂实录：

```bash
pkucw use "<课程名>" --json
pkucw assignments list --json
pkucw recordings list --json
```

提交作业：

```bash
pkucw assignments submit "<课程名>" "<作业标题>" --file /absolute/path/to/file --final-submit --confirm-final-submit "<作业标题或作业ID>" --json
```

如果只是安全探测，不写站点：

```bash
pkucw assignments submit "<课程名>" "<作业标题>" --file /absolute/path/to/file --json
```

## 4. 安全边界

安全读操作：

- `completion`
- `auth status`
- `accounts list/show`
- `courses list/show/current`
- `ls`
- `use`
- `current`
- `doctor`
- `info`
- `announcements list/show`
- `contents list/tree/show/download`
- `assignments list/show/download`
- `recordings list/show/download/latest`

危险写操作：

- `assignments submit --save-draft`
- `assignments submit --final-submit`

只有用户明确授权时，才允许执行危险写操作。

特别注意：

- `assignments submit` 不带 `--save-draft` / `--final-submit` 时默认是 dry-run，可用于安全探测
- 有些作业会进入 `复查提交历史记录` 页面，此时没有表单、没有上传控件、没有重交入口
- 遇到这种只读作业，不要盲目重试，应报告“当前不可重新提交”

## 5. 课程上下文规则

很多命令支持“省略课程参数”，前提是当前活动课程已经设置好。

推荐模式：

```bash
pkucw use "<课程名>" --json
pkucw announcements list --json
pkucw contents list --json
pkucw assignments list --json
pkucw recordings list --json
```

如果未设置活动课程，等价命令是：

```bash
pkucw announcements list "<课程名>" --json
pkucw contents list "<课程名>" --json
pkucw assignments list "<课程名>" --json
pkucw recordings list "<课程名>" --json
```

解析课程时的规则：

- 先试 `pkucw use "<课程片段>" --json`
- 如果失败，再试 `pkucw ls --current --json`
- 仍有歧义时，使用 `pkucw __complete -- use <前缀>`
- 不可靠时只返回候选，不要继续猜

## 6. 命令总表

下面列的是 agent 应该知道的全部稳定命令。

### 6.1 Shell 补全

```bash
pkucw completion <bash|zsh|fish>
```

作用：

- 输出 shell completion 脚本

辅助内部命令：

```bash
pkucw __complete -- <当前命令词序列>
```

用途：

- 给 agent 扩展候选
- 不应向普通用户解释为主命令

补充规则：

- `__complete` 只用于补全和纠偏，不用于真实业务查询
- 课程名有歧义时，先 `use`，再 `ls --current`，最后才 `__complete`

### 6.2 登录与会话

```bash
pkucw auth login [--account ACCOUNT] [--username USERNAME] [--label LABEL] [--password-stdin] [--no-save-account] [--login-url URL] [--show-browser] [--timeout-seconds N] [--json]
pkucw auth logout [--json]
pkucw auth status [--json]
```

快捷入口：

```bash
pkucw login ...
pkucw logout
pkucw status
```

参数规则：

- `--account`：使用已保存账号
- `--username`：临时指定账号
- `--label`：保存账号时写入备注
- `--password-stdin`：从 stdin 读密码
- `--no-save-account`：登录成功后不保存账号
- `--login-url`：覆盖登录入口
- `--show-browser`：显示浏览器窗口
- `--timeout-seconds`：每个登录步骤超时

禁止组合：

- `--account` 和 `--username` 不能同时使用
- `--account` 和 `--password-stdin` 不能一起使用

执行策略：

- 优先 `pkucw status --json`
- 若未认证，再 `pkucw login --json`
- 不要在已有有效会话时重复登录

### 6.3 账号管理

```bash
pkucw accounts list [--json]
pkucw accounts show [ACCOUNT] [--json]
pkucw accounts add [--username USERNAME] [--label LABEL] [--password-stdin] [--default] [--json]
pkucw accounts use <ACCOUNT> [--json]
pkucw accounts remove <ACCOUNT> [--json]
```

别名：

- `account`
- `accounts ls`
- `accounts get`
- `accounts rm`
- `accounts delete`

语义：

- `list`：列出已保存账号
- `show`：查看单个账号，省略参数时查看默认账号
- `add`：添加或更新账号
- `use`：设置默认账号
- `remove`：删除账号和 macOS 钥匙串密码

推荐首次初始化：

```bash
pkucw accounts add
pkucw login
pkucw ls --current
```

### 6.4 课程列表与上下文

```bash
pkucw courses list [--current] [--archived] [--search TEXT] [query] [--json]
pkucw courses show <COURSE> [--json]
pkucw courses current [--json]
pkucw ls [--current] [--archived] [--search TEXT] [query] [--json]
pkucw use <COURSE> [--json]
pkucw current [--json]
pkucw doctor [--json]
```

语义：

- `courses`：省略子命令时等价于 `courses list`
- `courses list` / `ls`：列课
- `courses show`：查看匹配结果，不是课程详情页抓取
- `courses current` / `current`：看当前活动课程
- `use`：设置活动课程
- `doctor`：检查安装、会话、上下文

隐藏兼容：

```bash
pkucw list-courses [--current] [--archived] [--search TEXT] [query]
pkucw course list [--current] [--archived] [--search TEXT] [query]
pkucw course ls [--current] [--archived] [--search TEXT] [query]
```

这些都真实存在，但不作为首选。

筛选参数：

- `--current`：只看当前学期
- `--archived`：只看历史课程
- `--search TEXT`：按课程名、课程 ID、学期筛选
- `query`：隐藏兼容参数，可视作 `--search`

### 6.5 课程元数据

```bash
pkucw info [COURSE] [--json]
pkucw course info [COURSE] [--json]
```

语义：

- 抓课程菜单、当前课程页、菜单入口、课程页元信息
- 若省略 `COURSE`，默认使用当前活动课程

注意：

- `pkucw course info`
- `pkucw info`

这两种是等价入口。

### 6.6 通知

可用入口除了 `announcements` 外，还有这些真实别名：

```bash
pkucw announcements ...
pkucw notice ...
pkucw notices ...
pkucw announcement ...
pkucw notification ...
pkucw notifications ...
```

顶层推荐写法：

```bash
pkucw announcements list [COURSE] [--json]
pkucw announcements show [COURSE] <ANNOUNCEMENT> [--json]
```

嵌套写法同样有效：

```bash
pkucw course announcements list [COURSE] [--json]
pkucw course announcements show [COURSE] <ANNOUNCEMENT> [--json]
```

语义：

- `announcements`：省略子命令时等价于 `announcements list`
- `list`：列出课程通知
- `show`：获取单条通知正文、发布时间、发帖者、资源链接

参数：

- `COURSE` 可省略，省略时使用活动课程
- 也可以写 `--course COURSE`
- `ANNOUNCEMENT` 是通知标题片段或 ID
- `--limit N` 只返回前 N 条通知；顶层 `announcements` 和 `announcements list` 都支持

示例：

```bash
pkucw announcements --course "<课程名>" --limit 3 --json
pkucw announcements list "<课程名>" --limit 1 --json
pkucw announcements show --course "<课程名>" "<通知标题片段>" --json
```

`show` 的关键返回字段：

- `announcement.title`：通知标题
- `announcement.published_at`：发布时间
- `announcement.author`：发帖者
- `announcement.asset_urls`：通知中的附件或图片链接
- `body_text`：去标签后的通知正文全文
- `body_html`：原始 HTML 正文，适合 agent 后续富文本处理

### 6.7 教学内容

可用入口除了 `contents` 外，还有这些真实别名：

```bash
pkucw contents ...
pkucw content ...
pkucw material ...
pkucw materials ...
```

顶层推荐写法：

```bash
pkucw contents list [COURSE] [--json]
pkucw contents tree [COURSE] [--json]
pkucw contents show [COURSE] <CONTENT> [--json]
pkucw contents download [COURSE] <CONTENT> [--output PATH] [--output-dir DIR] [--json]
```

嵌套写法同样有效：

```bash
pkucw course contents list [COURSE] [--json]
pkucw course contents tree [COURSE] [--json]
pkucw course contents show [COURSE] <CONTENT> [--json]
pkucw course contents download [COURSE] <CONTENT> [--output PATH] [--output-dir DIR] [--json]
```

语义：

- `contents`：省略子命令时等价于 `contents list`
- `list`：顶层教学内容
- `tree`：递归树
- `show`：单个内容详情
- `download`：下载文件或文件夹

参数语义：

- `COURSE` 可省略，也可以写 `--course COURSE`
- `CONTENT`：内容 ID 或标题片段
- `--output PATH`：
  - 文件场景下可直接指定目标文件或目标前缀
  - 文件夹场景下可指定目标目录
- `--output-dir DIR`：
  - 只指定目录
  - CLI 会自动以内容标题生成文件名

成功判定：

- 不能只说“已开始下载”
- 必须从 JSON 里读取 `payload.download.output_path` 或 `downloaded_files`
- 然后用 `ls -lh` / `file` 验证

重要细节：

- CLI 会自动补后缀
- 旧式 Office 文件也会尽量补 `.ppt/.doc/.xls`
- 不要自己给课件乱补 `.pptx`

隐藏兼容：

```bash
pkucw download-content [COURSE] <CONTENT> [--output PATH] [--output-dir DIR] [--dest DIR]
```

说明：

- `--dest` 是 `--output-dir` 的兼容别名
- `download-content` 的 handler 与 `contents download` 相同

### 6.8 作业

顶层推荐写法：

```bash
pkucw assignments list [COURSE] [--json]
pkucw assignments show [COURSE] <ASSIGNMENT> [--json]
pkucw assignments download [COURSE] <ASSIGNMENT> [--output PATH] [--output-dir DIR] [--json]
pkucw assignments submit [COURSE] <ASSIGNMENT> [--file FILE ...] [--replace-files] [--clear-files] [--text TEXT] [--clear-text] [--comment TEXT] [--clear-comment] [--save-draft] [--final-submit --confirm-final-submit EXACT] [--json]
```

嵌套写法同样有效：

```bash
pkucw course assignments list ...
pkucw course assignments show ...
pkucw course assignments download ...
pkucw course assignments submit ...
```

语义：

- `assignments`：省略子命令时等价于 `assignments list`
- `list`：列出作业条目
- `show`：作业详情、截止时间、分值、能力、说明、附件、已交附件
- `download`：
  - Blackboard 原生作业会导出 `作业说明.md`
  - 若作业有附件，也会下载附件
  - 附件型作业条目会直接下载文件
- `submit`：
  - 默认 dry-run
  - `--save-draft` 真实保存草稿
  - `--final-submit` 真实最终提交

提交参数细节：

- `COURSE` 可省略，也可以写 `--course COURSE`
- `--file FILE`：可重复，多次上传多个文件
- `--replace-files`：先删现有草稿附件，再传新文件
- `--clear-files`：删现有草稿附件，不上传新文件
- `--text TEXT`：文本提交内容
- `--clear-text`：清空草稿正文
- `--comment TEXT`：备注
- `--clear-comment`：清空备注
- `--save-draft`：真实草稿写入
- `--final-submit`：真实最终提交
- `--confirm-final-submit EXACT`：必须与作业标题或作业 ID 完全一致

强制规则：

- 没有明确授权，不要加 `--save-draft`
- 没有明确授权，不要加 `--final-submit`
- 查看作业内容时优先 `show` 或 `download`
- 不要把 `submit` 当成“查看作业内容”的入口

只读作业规则：

- 若 `show` 或提交页进入 `复查提交历史记录`
- 且没有上传表单、提交按钮、保存草稿按钮
- 则作业当前不可重交
- 直接报告只读状态，不要盲目重试

隐藏兼容：

```bash
pkucw download-assignment [COURSE] <ASSIGNMENT> [--output PATH] [--output-dir DIR] [--dest DIR]
pkucw submit-assignment [COURSE] <ASSIGNMENT> [--file FILE ...] [--replace-files] [--clear-files] [--text TEXT] [--clear-text] [--comment TEXT] [--clear-comment] [--save-draft] [--final-submit --confirm-final-submit EXACT]
```

说明：

- `download-assignment` 等价于 `assignments download`
- `submit-assignment` 等价于 `assignments submit`
- `--dest` 是下载目录参数的兼容别名

### 6.9 课堂实录

除了 `recordings` 外，agent 纠偏层还兼容这些词：

```bash
pkucw recordings ...
pkucw recording ...
pkucw video ...
pkucw videos ...
```

顶层推荐写法：

```bash
pkucw recordings list [COURSE] [--json]
pkucw recordings show [COURSE] <RECORDING> [--json]
pkucw recordings download [COURSE] <RECORDING> [--output PATH] [--output-dir DIR] [--no-remux] [--no-progress] [--json]
pkucw recordings latest [COURSE] [--output PATH] [--output-dir DIR] [--no-remux] [--no-progress] [--json]
pkucw recordings download-latest [COURSE] [--output PATH] [--output-dir DIR] [--no-remux] [--no-progress] [--json]
```

嵌套写法同样有效：

```bash
pkucw course recordings list ...
pkucw course recordings show ...
pkucw course recordings download ...
pkucw course recordings latest ...
```

语义：

- `recordings`：省略子命令时等价于 `recordings list`
- `list`：列课堂实录
- `show`：解析播放器、playlist、时长、分片数
- `download`：下载指定回放
- `latest` / `download-latest`：下载最新一条

参数：

- `COURSE` 可省略，也可以写 `--course COURSE`
- `--output PATH`：输出文件或前缀
- `--output-dir DIR`：只指定目录
- `--no-remux`：保留 `.ts`，跳过转 `.mp4`
- `--no-progress`：不显示分片下载进度

成功判定：

- 读取 JSON 中的 `output_path`、`ts_output_path`、`mp4_output_path`
- 至少验证最终输出文件存在
- 下载大文件前最好先 `show`

隐藏兼容：

```bash
pkucw download-recording [COURSE] [RECORDING] [--latest] [--output PATH] [--output-dir DIR] [--dest DIR] [--no-remux] [--no-progress]
pkucw download-latest-recording [COURSE] [--course COURSE] [--output PATH] [--output-dir DIR] [--dest DIR] [--no-remux] [--no-progress]
pkucw latest-recording [COURSE] [--course COURSE] [--output PATH] [--output-dir DIR] [--dest DIR] [--no-remux] [--no-progress]
```

重要默认值：

- `download-recording --latest` 会转到“下载最新一条”
- `download-latest-recording` / `latest-recording` 默认已经带：
  - `no_remux=True`
  - `no_progress=True`
- 所以 agent 若直接走 `download-latest-recording`，即使不显式再写这两个参数，也会是更稳的模式
- `--dest` 是 `--output-dir` 的兼容别名

纠偏规则：

- 若模型错误写成 `contents ... --type recording`
- CLI 会尽量改写到 `recordings ...`
- 但 skill 仍要求直接使用 `recordings` 或 `download-latest-recording`

### 6.10 兼容快捷命令

这些命令存在，但不是首选；只有在外部集成或旧流程依赖时再用。

```bash
pkucw download-content [COURSE] <CONTENT> [--output PATH] [--output-dir DIR]
pkucw download-recording [COURSE] [RECORDING] [--latest] [--output PATH] [--output-dir DIR] [--no-remux] [--no-progress]
pkucw download-assignment [COURSE] <ASSIGNMENT> [--output PATH] [--output-dir DIR]
pkucw submit-assignment [COURSE] <ASSIGNMENT> [--file FILE ...] [--replace-files] [--clear-files] [--text TEXT] [--clear-text] [--comment TEXT] [--clear-comment] [--save-draft] [--final-submit --confirm-final-submit EXACT]
pkucw list-courses [--current] [--archived] [--search TEXT] [query]
```

规则：

- 知道它们存在即可
- 新工作流优先 canonical commands，不要主动发明这些变体

### 6.11 省略子命令时的默认行为

这些顶层资源命令如果不写子命令，会直接执行列表：

```bash
pkucw announcements           == pkucw announcements list
pkucw contents                == pkucw contents list
pkucw assignments             == pkucw assignments list
pkucw recordings              == pkucw recordings list
pkucw courses                 == pkucw courses list
```

补充说明：

- `pkucw course` 本身不能直接接课程标题；它是命名空间，不是资源对象
- 所以不要写 `pkucw course "<课程名>" info`
- 正确写法是：
  - `pkucw course info "<课程名>"`
  - `pkucw info "<课程名>"`
  - 或先 `pkucw use "<课程名>"`

## 7. 一句话任务映射

如果用户是自然语言一句话，优先映射为最短工作流。

### 7.1 下载某门课最新课件

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw contents tree --json
pkucw contents download "<exact latest content title>" --output-dir <target-dir> --json
ls -lh "<saved-path>"
file "<saved-path>"
```

### 7.2 下载某门课最新回放

```bash
pkucw status --json
pkucw download-latest-recording --course "<course>" --output-dir <target-dir> --json
```

更稳的 agent 版本：

```bash
pkucw download-latest-recording --course "<course>" --output-dir <target-dir> --no-remux --no-progress --json
```

如果工具层返回 `Command still running`，不要结束；继续：

```bash
process poll <session-id>
process log <session-id>
```

直到拿到最终结果，再做本地核验：

```bash
ls -lh "<saved-path>"
file "<saved-path>"
```

### 7.3 问某门课最近通知

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw announcements list --json
```

若用户要通知正文：

```bash
pkucw announcements show "<title>" --json
```

### 7.4 查看作业要求

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw assignments list --json
pkucw assignments show "<assignment>" --json
```

若用户要导出说明与附件：

```bash
pkucw assignments download "<assignment>" --output-dir <target-dir> --json
```

### 7.5 受控提交作业

仅当用户明确授权时：

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw assignments submit "<assignment>" --file ./answer.txt --save-draft --json
```

最终提交：

```bash
pkucw assignments submit "<assignment>" --file ./answer.txt --final-submit --confirm-final-submit "<exact id or title>" --json
```

## 8. 输出与总结规则

对 agent 可跟进流程：

- 优先 `--json`

对用户最终回复：

- 课程精确标题
- 实际执行的命令类型
- 结果状态
- 若是列表：数量或关键条目
- 若是下载：落盘路径
- 若是作业：说明是查看、下载、dry-run、草稿写入还是最终提交

不要：

- 把过程性自言自语当结果
- 只说“开始下载了”
- 不验证文件是否存在就说“已完成”

## 9. 故障处理

### 9.1 登录/会话

- 若 `status` 显示已认证，优先直接用现有会话
- 若课程命令失败并提示会话问题，重试一次 `pkucw login --json`
- 若站点超时，明确说明是教学网站不稳定

### 9.2 课程解析

- 先 `use "<course>" --json`
- 失败再 `ls --current --json`
- 仍失败用 `__complete`
- 仍无法可靠解析时返回候选并停止

### 9.3 内容/作业/回放匹配失败

- 不要立刻换浏览器
- 先 `list` 或 `tree` 获取精确标题
- 再用精确标题或 ID 重试

### 9.4 下载后文件异常

- 先 `file "<saved-path>"`
- 若是 HTML 或超小文本页，说明没有通过 `pkucw` 正确认证下载
- 应回到 `pkucw ... download` 重试，而不是继续用裸 HTTP 工具

### 9.5 作业提交失败

- 若是只读复查页，直接报告不可重交
- 若缺确认串，不要猜，必须要求与作业 ID 或标题完全一致

## 10. OpenClaw 专项规则

- 对 OpenClaw，首选 `pkucw ...`；如果当前目录就是仓库根目录，也可以用 `./pkucw ...`
- 对微信/长会话环境，尽量使用短路径、短流程、少解释
- 若工具结果已经明确，不要再做额外探索
- 若模型开始写伪 tool call 文本而不是真正发起工具，应立即回到上一个真实结果并继续 canonical command
- 若任务是下载，必须做到：
  `pkucw ... --json` 成功 -> 提取保存路径 -> `ls -lh` -> `file`

## 11. 最小成功模板

下载型任务的最小成功模板：

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw <resource-command> --json
pkucw <download-command> --output-dir <dir> --json
ls -lh "<saved-path>"
file "<saved-path>"
```

查询型任务的最小成功模板：

```bash
pkucw status --json
pkucw use "<course>" --json
pkucw <query-command> --json
```

如果按这个模板执行，通常不需要浏览器、不需要 `curl`、也不需要额外工具。  
这就是本 skill 的目标状态。
