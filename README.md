# Chat Online

[MIT License](https://mit-license.org/)

基于 PyQt6 的局域网即时通信工具。一个程序同时提供聊天客户端、可视化服务器控制台和无界面服务器 CLI，适合家庭、教室、实验室与小型团队在可信局域网中快速建立聊天室。

`7.0.0` · `Python 3.10+` · `PyQt6` · `cryptography` · `MIT`

完整版本变化见 [CHANGELOG.md](CHANGELOG.md)。

<p align="center">
  <img src="docs/images/client-light.png" alt="Chat Online 客户端亮色主题" width="50%">
  <img src="docs/images/client-dark.png" alt="Chat Online 客户端暗色主题" width="50%">
</p>

<details>
<summary>查看服务器控制台</summary>

![Chat Online 服务器控制台](docs/images/server-light.png)

</details>

## 功能概览

### 聊天客户端

- 主群、邀请码群组和一对一私聊
- 历史消息、在线成员、角色与禁言状态
- 文件上传、共享列表和下载，单文件最大 8 MiB
- 消息搜索、当前会话导出、输入状态提示
- 洛谷风安全 Markdown：标题、强调、删除线、引用、列表/任务列表、表格、代码高亮、行号/范围高亮、链接、分隔线、公式源码和静态提示容器
- 断线检测、自动重连和内存中的离线消息队列
- 强制 TLS 1.3 链路加密，用户消息和共享文件使用端到端加密
- 窗口非激活时，实时收到他人的消息或文件会请求系统闪烁提醒；历史加载和自己的回显不触发，并带有节流
- 亮色/暗色主题，窗口布局与最近连接信息自动保存
- URL 识别、可点击链接和常用表情
- Markdown 原始 HTML 与危险链接协议不会执行；远程图片/Bilibili 只显示为 HTTPS 链接，不自动加载或播放

### 服务器控制台

- 启动、停止与运行状态监控
- 在线用户、群组、共享文件和活动日志集中查看
- 踢出用户、群组禁言/解禁、群组重命名与删除
- 单 IP 或 CIDR 网段封禁
- 向指定群组发送服务器公告
- 加密附件密文导出与删除、运行时长和资源统计

### 可靠性与数据保护

- UTF-8 JSON Lines 协议，正确处理 TCP 拆包与粘包
- 所有网络线程通过 Qt 信号更新界面，不直接操作 QWidget
- 消息帧、用户名、群名、正文和附件均有明确大小限制
- 历史文件与附件使用哈希文件名，阻止路径穿越
- 附件写入采用原子替换，并通过 SHA-256 校验完整性
- RSA-3072 OAEP/PSS 与 AES-256-GCM 组成混合加密信封，密钥、密文与路由元数据均经过认证或签名校验
- 服务器历史和附件对用户内容只持久化加密信封：用户消息正文、原始文件名与文件内容均为密文
- 数据写入用户数据目录，不依赖程序目录可写权限

## Markdown 消息

输入框编辑 Markdown 源文，发送后在消息气泡中渲染。常用语法包括 `#` 标题、`**粗体**`、`*斜体*`、`~~删除线~~`、反引号代码、`>` 引用、有序/无序/任务列表、GFM 表格、分隔线、链接和自动链接。

围栏代码可使用洛谷参数：

````markdown
```python line-numbers lines=2-3
value = 42
print(value)
return value
```
````

支持 `:::align{center}` / `:::align{right}`、`:::epigraph[署名]` 和 `:::info` / `success` / `warning` / `error[标题]{open}` 容器；容器在桌面客户端中静态展开。`::cute-table{tuack}` 会使用普通安全表格样式。图片和 Bilibili 语法不会发起远程媒体加载，只生成经过协议校验的 HTTPS 链接。

## 30 秒开始使用

### 1. 安装

```powershell
py -m pip install -r requirements.txt
```

`requirements.txt` 会安装 PyQt6、`cryptography` 和 Markdown 渲染依赖。Linux/macOS 可将下文的 `py` 替换为 `python3`。Python 及其 OpenSSL 必须支持 TLS 1.3，否则服务器会拒绝启动，客户端也不会降级到明文连接。

### 2. 打开程序

```powershell
py 聊天.py
```

启动器中选择运行模式：

- 服务器：设置监听端口并打开控制台。
- 客户端：填写服务器局域网 IP、端口和用户名。

客户端连接其他设备时应填写服务器的局域网 IPv4 地址，例如 `192.168.1.10`；`127.0.0.1` 只表示当前设备。

### 3. 直接启动指定模式

服务器控制台：

```powershell
py 聊天.py --mode server --host 0.0.0.0 --port 8888
```

客户端：

```powershell
py 聊天.py --mode client --host 192.168.1.10 --port 8888 --username Alice
```

也可以使用模块入口：

```powershell
py -m chat_online --help
```

## 安装为命令

```powershell
py -m pip install -e .
chat-online --version
```

安装后，`chat-online`、`py -m chat_online` 与 `py 聊天.py` 使用同一套入口。

## 无界面服务器

不需要桌面环境时，可运行 headless 服务器：

```powershell
py 聊天.py --mode server --headless --host 0.0.0.0 --port 8888
```

交互终端命令：

| 命令 | 作用 |
| --- | --- |
| `help [command]` | 显示命令列表或指定命令的帮助 |
| `status` | 查看监听地址、运行时长和资源数量 |
| `users` | 列出在线用户 |
| `rooms` | 列出群组 |
| `files [room]` | 列出全部或指定群组的共享文件 |
| `say <text>` | 向主群发送服务器公告 |
| `kick <username-or-id>` | 断开指定用户 |
| `mute <user> [room]` | 在指定群组禁言用户 |
| `unmute <user> [room]` | 解除群组禁言 |
| `ban <ip-or-cidr>` | 封禁 IP 或网段 |
| `unban <ip-or-cidr>` | 解除 IP 或网段封禁 |
| `stop` | 优雅停止服务器 |

参数支持 shell 引号，例如：

```text
say "Server maintenance in 10 minutes"
```

非交互式运行时不会读取标准输入，可使用 `Ctrl+C`、`SIGINT` 或 `SIGTERM` 优雅退出。

## 启动参数

| 参数 | 说明 |
| --- | --- |
| `--mode server\|client` | 直接进入服务器或客户端模式 |
| `--host HOST` | 客户端服务器地址，或服务器监听地址 |
| `--port PORT` | TCP 端口，默认 `8888`；服务器可用 `0` 自动分配测试端口 |
| `--username NAME` | 客户端用户名；省略时使用系统账户名 |
| `--theme light\|dark` | 指定亮色或暗色主题 |
| `--data-dir PATH` | 覆盖应用数据目录 |
| `--headless` | 无图形界面运行服务器，必须配合 `--mode server` |
| `--no-auto-start` | 打开窗口但不立即监听或连接 |
| `--reset-settings` | 清除保存的主题、窗口与连接设置 |
| `--version` | 输出版本号 |

查看当前版本的完整帮助：

```powershell
py 聊天.py --help
```

## 数据目录

默认数据位置：

| 系统 | 路径 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Chat Online` |
| macOS | `~/Library/Application Support/Chat Online` |
| Linux | `$XDG_DATA_HOME/chat-online` 或 `~/.local/share/chat-online` |

自定义目录：

```powershell
py 聊天.py --mode server --data-dir D:\ChatData
```

也可以设置环境变量：

```powershell
$env:CHAT_ONLINE_DATA_DIR = "D:\ChatData"
```

目录中包含加密的用户聊天历史、共享文件、文件索引、安全材料和 `crash.log` 诊断信息。服务器公告与系统事件不是 E2EE 内容。若从早期明文构建原地升级，旧数据不会被新安全协议中继或下载，但原文件仍可能留在磁盘；确认无需保留后，应先停止服务器并备份，再清理旧数据目录。不要在程序运行期间手动修改这些文件。

`<data-dir>/security/` 中的主要文件：

| 路径 | 作用 |
| --- | --- |
| `tls-cert.pem` | 服务器首次启动时自动生成的自签名 TLS 证书 |
| `tls-key.pem` | 与服务器证书配对的私钥，必须限制访问并备份 |
| `server-cert-pins.json` | 客户端按 `主机:端口` 记录的服务器证书 TOFU 指纹 |
| `identities/<username-sha256>.key` | 客户端的 RSA-3072 长期身份私钥；Windows 上使用当前用户的 DPAPI 保护 |
| `peer-pins.json` | 客户端按用户名记录的对端 RSA 公钥 TOFU 指纹 |

这些客户端文件位于客户端的数据目录，服务器证书和私钥位于服务器的数据目录。使用 `--data-dir` 或 `CHAT_ONLINE_DATA_DIR` 时，`<data-dir>` 表示指定的根目录。删除身份私钥会创建新身份，并使其他客户端的既有 pin 拒绝该用户；不应通过随意删除 pin 来绕过变更警告。

## 加密与安全边界

Chat Online 同时使用传输层和端到端两层保护，两者用途不同：

- **TLS 1.3** 加密客户端与服务器之间的整条链路，并通过专用 ALPN 标识拒绝非 Chat Online 连接。只允许 TLS 1.3，没有明文、低版本 TLS 或“加密失败后继续连接”的回退路径。
- **E2EE 混合加密** 为每条消息和每个文件生成随机 AES-256-GCM 内容密钥，再用每位接收者的 RSA-3072 OAEP/SHA-256 公钥分别包装该密钥。发送者使用 RSA-PSS/SHA-256 签名信封，AES-GCM 认证密文及关联元数据；签名、标签或收件人校验失败时拒绝解密。RSA 仅包装对称密钥，不直接加密消息或大文件。
- **服务器不解密用户内容**，只存储和中继已签名的加密信封。用户消息正文、原始文件名和文件内容以密文落盘；为了路由和管理，服务器仍然可见 IP 地址、用户名、会话成员、发送时间、内容大小和收件人等元数据。服务器自己生成的公告、系统事件和管理记录对服务器也不是秘密。E2EE 不是匿名或元数据隐藏方案。
- **Markdown 在客户端解密后渲染**。服务器保存的是加密后的 Markdown 源文，不接触渲染结果；客户端禁用原始 HTML，并只允许 `http`/`https` 外链。

### 首次信任与指纹变更

服务器首次启动时自动生成自签名证书。客户端首次连接某个 `主机:端口` 时记录证书 SHA-256 指纹，同样在首次看到某个用户名的 RSA 公钥时记录公钥指纹。后续连接中任一指纹变更都会阻断连接或内容处理，不会自动接受新指纹。

TOFU（Trust On First Use）只能检测“首次之后”的替换，不能证明首次连接的证书或公钥就是预期对象。在发送敏感内容前，应通过另一个可信通道向服务器管理员和聊天对象比对指纹。已确认是合法换证书、换设备或重置密钥时，先在可信通道中核对新指纹，再备份并精确移除对应 pin；不要删除整个 `security` 目录后盲目重连。

### 已知限制

- 用户身份仍然是“用户名 + 首次所见 RSA 公钥”，没有密码账户、证书机构或组织目录为用户名背书。攻击者如果控制首次信任过程，仍可能冒用用户名。
- E2EE 使用长期静态 RSA 身份密钥，**不提供前向保密**。如果某个私钥日后泄露，攻击者获得之前捕获的相应加密信封时，可能恢复该身份过去可解密的内容。TLS 1.3 的传输会话属性不能弥补这一 E2EE 密钥模型限制。
- 群组内容密钥只为发送当时的在线成员分别封装。后来加入群组的成员不能解密加入前的历史；成员或密钥发生变化时，发送端必须先同步最新成员快照。
- 洛谷扩展采用桌面安全适配：`info/success/warning/error` 折叠框静态展开，LaTeX 保留并样式化源码，不做公式排版；不支持 `^`/`<` 表格单元格合并、远程图片自动加载或视频内嵌。
- 服务器端点仍应放在受控网络中；不提供互联网中继、流量匿名、运营级抗 DDoS 或安全审计承诺。需跨公网使用时，仍建议配合受控 VPN 和严格防火墙规则，不要直接将监听端口暴露给公网。

服务端默认监听 `0.0.0.0`，可能需要在系统防火墙中允许 Python 或 Chat Online 访问专用网络。

## 协议兼容性

当前 `7.0.0` 安全协议仍使用 UTF-8 JSON Lines 分帧，但连接前必须完成 TLS 1.3/ALPN，应用层则传输签名的 E2EE 信封。它与 5.x 以及已发布的明文 `6.0.0` 不兼容，旧客户端不能连接新服务器，新客户端也不会回退连接旧服务器。请同时升级所有客户端和服务器，不要混用 `7.0.0` 与旧版构建。

主要限制：

- 单条消息正文最多 4000 个字符。
- 用户名最多 24 个字符。
- 群组名称最多 40 个字符。
- 单个附件最多 8 MiB。
- 单个协议帧最多 12 MiB。
- 群组、用户会话和封禁列表在服务器重启后不会恢复。历史与附件文件会保留在磁盘，但旧的非主群不会自动重建。

## 项目结构

```text
Chat-online/
├─ 聊天.py                    # 兼容启动器
├─ chat_online/
│  ├─ app.py                 # QApplication 与参数入口
│  ├─ launcher.py            # 模式选择启动器
│  ├─ client_window.py       # 客户端界面
│  ├─ server_window.py       # 服务器控制台
│  ├─ client.py              # 异步客户端连接层
│  ├─ server.py              # 多线程服务器引擎
│  ├─ protocol.py            # JSON Lines 编解码与帧限制
│  ├─ security.py            # TLS 1.3、RSA/AES 密码原语与 TOFU 存储
│  ├─ secure_protocol.py     # E2EE 信封、身份与路由约束
│  ├─ storage.py             # 历史和附件存储
│  ├─ theme.py               # 亮色/暗色主题与字体回退
│  ├─ widgets.py             # 消息气泡等复用控件
│  └─ cli.py                 # Headless 服务与管理命令
├─ tests/                    # 单元、TCP 集成和 Qt 离屏测试
├─ tools/render_previews.py  # README 界面预览生成器
└─ pyproject.toml
```

## Windows 发布打包

仓库提供可重复执行的 Windows x64 构建脚本：

```powershell
pwsh -NoProfile -File .\packaging\build_windows.ps1
```

脚本默认沿用本机的 pip 配置。网络环境需要明确使用官方 PyPI 时，可临时传入源地址，而不会把该设置写入项目：

```powershell
pwsh -NoProfile -File .\packaging\build_windows.ps1 -PipIndexUrl https://pypi.org/simple
```

脚本会在工作区中创建临时 `.packaging-venv`，安装隔离的构建依赖，然后依次执行测试、Ruff、编译检查、PyInstaller 构建、成品冒烟测试与 SHA-256 校验。无论成功或失败，临时虚拟环境和 `build` 目录都会被删除。

便携包会同时包含 `cryptography` 及其运行库。打包后应使用 GUI 和 CLI 成品分别验证 TLS 1.3 连接，不应将已发布的明文 `6.0.0` 便携包与当前服务器混用。

输出位于 `release/`：

```text
release/
├─ ChatOnline-7.0.0-windows-x64/
│  ├─ ChatOnline.exe          # 无控制台 GUI
│  ├─ ChatOnline-CLI.exe      # CLI 与 headless 服务器
│  ├─ _internal/              # 共享 Python/Qt 运行库
│  ├─ licenses/
│  ├─ source/                 # wheel 与可独立构建的完整 sdist
│  └─ SHA256SUMS.txt
├─ ChatOnline-7.0.0-windows-x64.zip
└─ ChatOnline-7.0.0-windows-x64.zip.sha256
```

`ChatOnline.exe` 仅用于图形界面。`--help`、CLI 命令和 `--headless` 应通过 `ChatOnline-CLI.exe` 执行。构建产物带有图标和版本资源，但默认没有数字签名；对外发布前应使用可信的 Windows 代码签名证书签名。

直接运行打包成品：

```powershell
.\release\ChatOnline-7.0.0-windows-x64\ChatOnline.exe
.\release\ChatOnline-7.0.0-windows-x64\ChatOnline-CLI.exe --mode server --headless --host 0.0.0.0 --port 8888
```

首次启动服务器会自动生成 TLS 证书/私钥，客户端首次使用某个用户名时会自动生成身份私钥；两个过程都可能比后续启动稍慢。

Windows 便携包会捆绑 PyQt6/Qt 运行库。公开再分发前，请阅读包内 `THIRD_PARTY_NOTICES.md` 和 `licenses/`，并确认符合 PyQt6 GPLv3 或商业许可条款。

`source/` 内的 `.tar.gz` 包含 `packaging/`、`tools/`、测试、文档图片和启动入口。解压后即可执行上面的 `build_windows.ps1`；脚本不会永久修改 pip 配置，也不会默认强制使用某个软件源。

## 开发与测试

安装开发依赖：

```powershell
py -m pip install -e ".[dev]"
```

运行检查：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
py -m pytest
py -m ruff check chat_online tests tools 聊天.py
py -m compileall -q chat_online tests tools 聊天.py
```

重新生成 README 截图：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
py -m tools.render_previews docs\images
```

## 常见问题

### 服务器无法启动

端口可能已被占用。更换端口，或使用 `--port 0` 验证系统能否自动分配端口。

### 其他设备无法连接

确认客户端填写的是服务器局域网 IP；检查两台设备是否位于同一网络，并允许应用通过防火墙的专用网络规则。新版只接受 TLS 1.3，也请确认客户端与服务器均已更新。

### 提示服务器证书或用户身份指纹已变更

这是预期的失败关闭行为，可能表示服务器重装、用户换设备，也可能表示冒用或中间人攻击。不要直接删除 pin。先停止连接，通过独立可信通道核对新旧 SHA-256 指纹；只有在确认变更合法后，才备份并移除 `server-cert-pins.json` 或 `peer-pins.json` 中精确对应的记录，然后重新信任。

### 图形界面无法打开

```powershell
py -c "import PyQt6; print(PyQt6.__file__)"
py -m pip install -r requirements.txt
```

### 需要查看崩溃原因

检查应用数据目录中的 `crash.log`，也可以从终端启动程序以查看即时错误输出。

## License

本项目使用 [MIT License](https://mit-license.org/)。
