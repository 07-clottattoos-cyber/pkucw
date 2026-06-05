# 使用说明

## `pkucw` 是做什么的

`pkucw` 把北大教学网里常见但重复的操作整理成命令行接口，核心能力包括：

- 登录并保存会话
- 获取课程列表
- 切换当前课程
- 查看通知正文
- 查看和下载教学内容
- 查看作业详情、导出作业说明并下载附件
- 下载课堂实录

## 安装

```bash
git clone <GitHub 仓库地址> courseweb-cli
cd courseweb-cli
./install.sh
```

安装脚本会自动：

- 选择或安装合适的 Python
- 创建虚拟环境
- 安装项目依赖
- 安装 Playwright Chromium
- 配置 `pkucw`、`cw`、`courseweb`
- 尝试写入 shell completion

## 登录与账号

推荐先保存账号：

```bash
pkucw accounts add
pkucw login
```

常用命令：

```bash
pkucw accounts list
pkucw accounts show
pkucw accounts use <account>
pkucw accounts remove <account>
pkucw status
```

如果教学网会话失效，课程相关命令会优先尝试自动恢复登录状态。

## 典型工作流

### 查看课程

```bash
pkucw ls --current
pkucw ls --archived
pkucw use "课程名"
pkucw current
```

### 查看课程信息

```bash
pkucw info
pkucw announcements list
pkucw announcements show "公告标题片段"
```

`pkucw announcements show` 不只是返回预览，它会返回：

- 通知标题、发布时间、发帖者
- `body_text`：完整正文文本
- `body_html`：完整 HTML 正文
- `asset_urls`：通知里的附件或图片链接

### 查看教学内容

```bash
pkucw contents list
pkucw contents tree
pkucw contents show "内容标题片段"
pkucw contents download "内容标题片段" --output ./downloads/item
```

### 安全查看作业

```bash
pkucw assignments list
pkucw assignments show "作业标题片段"
pkucw assignments download "作业标题片段" --output ./downloads/assignment
pkucw assignments submit "作业标题片段" --comment "probe" --json
```

默认是 dry-run，不会真的写入，除非显式加上 `--save-draft` 或 `--final-submit`。

### 下载课堂实录

```bash
pkucw recordings list
pkucw recordings show "录播标题片段"
pkucw recordings latest --output ./downloads/latest
```

## 输出模式

- 默认输出适合人直接阅读
- `--json` 适合 agent 和脚本
- `--color auto|always|never` 控制彩色输出

## 命令补全

```bash
pkucw completion zsh
pkucw completion bash
pkucw completion fish
```

安装脚本通常会自动把补全写入常见 shell 配置。
