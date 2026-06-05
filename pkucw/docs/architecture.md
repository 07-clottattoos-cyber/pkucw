# 架构说明

## 总体结构

`pkucw` 主要由以下几层组成：

- CLI 层：参数解析、命令分发、输出渲染
- 状态层：会话、账号、当前课程上下文
- 认证层：Playwright 驱动的登录流程
- 抓取层：课程、通知、教学内容、作业、录播
- 媒体层：录播下载、解密、拼接、转封装、探测

## 主要模块

### CLI 层

`src/courseweb/cli.py`

负责：

- 构建命令树
- 解析参数
- 执行具体 handler
- 统一返回 `CommandResult`
- 输出人类可读结果或 JSON

### 认证层

`src/courseweb/auth.py`

负责：

- 打开教学网登录入口
- 进入北大统一认证页
- 填写用户名密码
- 保存 Playwright storage state

### 账号管理

`src/courseweb/accounts.py`

负责：

- 保存账号元数据
- 读取默认账号
- 从 macOS Keychain 读取密码
- 设置默认账号

### 本地状态

`src/courseweb/state.py`
`src/courseweb/models.py`

负责：

- `session.json`
- `storage_state.json`
- `accounts.json`
- 当前课程上下文

### 资源抓取模块

- `courses.py`
- `announcements.py`
- `contents.py`
- `assignments.py`
- `recordings.py`

这些模块都复用已保存的 Blackboard 会话状态，再进入具体页面解析结构化结果。

## 执行流程

### 登录

1. 用户选择已保存账号或在终端输入账号密码
2. Playwright 打开登录入口
3. 完成统一认证
4. 落回 Blackboard 门户
5. 保存 `storage_state.json`
6. 更新 `session.json`

### 会话恢复

1. 课程相关命令先检查本地会话
2. 用较快的方式探测当前 session 是否仍可用
3. 如果不可用，尝试用默认账号自动恢复
4. 如果恢复成功，继续执行命令
5. 如果恢复失败，尽快返回明确错误

### 课程资源读取

1. 读取本地 session
2. 确定目标课程
3. 进入 Blackboard 对应页面
4. 解析页面结构
5. 返回结构化结果给 CLI 层

### 课堂实录下载

1. 解析课堂实录列表页
2. 打开播放页
3. 找到真实播放器和 `.m3u8`
4. 拉取分片
5. 如有加密则解密
6. 先写 `.ts`
7. 再尝试转 `.mp4`
8. 返回校验和探测结果

## 关键设计选择

### 为什么使用 Playwright

因为教学网登录和部分受保护资源明显依赖真实浏览器行为，纯 `requests` 很脆弱。

### 为什么账号和会话要分开

项目把：

- 账号信息
- Blackboard 会话信息

拆开管理，这样既能保留可复用账号，又不会把密码写进项目文件里。

### 为什么默认强调只读和 dry-run

作业相关是高风险操作，默认保持安全行为可以减少误提交和误写入。

## 当前限制

- Blackboard DOM 变化会影响抓取
- 账号安全存储目前主要面向 macOS Keychain
- 站点网络状态会直接影响端到端稳定性
