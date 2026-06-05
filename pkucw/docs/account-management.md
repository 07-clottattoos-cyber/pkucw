# 账号管理

## 目标

账号管理层的目标是替代不规范的本地凭据文件，提供更像正式 CLI 的体验：

- 在终端输入账号密码
- 保存可复用账号
- 不把密码写进项目目录
- 同时兼容人类用户和 agent

## 存储方式

### 非敏感元数据

保存在：

```text
~/.courseweb/accounts.json
```

包括：

- 用户名
- 可选标签
- 是否默认账号
- 创建/更新时间
- 最近使用时间
- 最近登录时间

### 密码

密码保存在 macOS Keychain 中，服务名是：

```text
pkucw
```

因此密码不会写进：

- 仓库文件
- README
- `.env`
- session JSON

## 常用命令

### 添加或更新账号

```bash
pkucw accounts add
pkucw accounts add --username <pku-id>
printf '%s' "$PASSWORD" | pkucw accounts add --username <pku-id> --password-stdin
```

### 查看账号

```bash
pkucw accounts list
pkucw accounts show
pkucw accounts show <account>
```

### 设置默认账号

```bash
pkucw accounts use <account>
```

### 删除账号

```bash
pkucw accounts remove <account>
```

## 登录优先级

`pkucw login` 会按这个顺序寻找凭据：

1. `--account` 指定的已保存账号
2. `--username` + 终端输入 / `--password-stdin`
3. 当前默认账号
4. 终端交互输入

## 自动恢复

如果后续命令发现 Blackboard session 已失效，运行时会优先尝试复用默认账号自动恢复登录状态。

## 安全说明

- `accounts.json` 本身不含密码
- `pkucw logout` 只清 Blackboard 会话，不删除账号
- `pkucw accounts remove` 会同时删除账号元数据和 Keychain 项
- 会话和账号是两套独立状态
