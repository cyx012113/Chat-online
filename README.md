# Chat-online Python 局域网聊天室

[![CC BY-NC-SA 4.0](https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt)

## 建议 Advice

各个版本功能、通信协议不同，为了保证功能完整性，请勿使用不同版本的应用进行通信。

本程序的某些功能仅在 Windows 系统上被支持，在其他操作系统使用无法保证功能完整性。

Each version has different functions and communication protocols, in order to ensure the integrity of the function, please do not use different versions of the application to communicate.

Certain features of this program are only supported on Windows systems. Functionality cannot be guaranteed when used on other operating systems.

## 更新日志 Update Log

### 局域网聊天 5.1.0

- 将客户端与服务器端代码合并为单一程序，可通过启动参数或界面选择运行模式；
- 优化群组列表解析逻辑，修复了因服务器返回额外信息（如 PermissionInfo）导致客户端群组列表显示异常的问题；
- 修复调整窗口透明度后主题颜色未能及时刷新的问题；
- 移除部分未使用的代码，提升程序稳定性与可维护性。

### 局域网聊天 5.0.0

- 增加了窗口闪动；
- 增加了私聊、群聊，分为主群（所有人）和小群（部分人）、私聊（两个人）；
- 增加了发送、下载文件功能；
- 增加了发送表情功能；
- 增加了发送链接功能；
- 设置了服主、群主、管理员，服主可以管理群聊，群主、管理员可以管理群内成员；
- 增加了中英文切换功能；
- 增加了亮暗主题切换功能；
- 增加了封禁 IP（段）的功能；
- 支持了群聊切换；
- 兼容了除 Windows 外的操作系统；
- 更新了界面，优化操作方式。

### 局域网聊天 4.2.6

- 更新了字体；
- 修复无法关闭窗口的问题。

### 局域网聊天 4.2.3

- 更新了服务器的界面，添加了按钮；
- 更新了语言，增加英语。

### 局域网聊天 4.2.0

- 更新了服务器端的踢出、禁言、解禁功能。

### 局域网聊天 4.0.0

- 第二代 GUI，更改界面样式；
- 从这个版本开始，开源了 Python 代码。

### 局域网聊天 2.0.0-3.0.0（未发布）

- 第一代 GUI，Bug 层出不穷。

### 局域网聊天 1.0.0（未发布）

- 控制台版本。

### Chat-online 5.1.0

- Merged client and server code into a single program, allowing selection of running mode via command-line arguments or interface;
- Optimized group list parsing logic, fixed the issue where the client displayed incorrect group entries due to extra information (e.g., PermissionInfo) from the server;
- Fixed the issue where theme colors did not update immediately after adjusting window transparency;
- Removed some unused code, improving stability and maintainability.

### Chat-online 5.0.0

- Added window flashing;
- Added private chat and group chat, categorized into main groups (all members), sub-groups (select members), and private chats (two participants);
- Added file sending and downloading functionality;
- Added emoji sending functionality;
- Added link sending functionality;
- Implemented server owners, group owners, and administrators: Server owners manage group chats; group owners and administrators manage group members;
- Added language switching between Chinese and English;
- Added light/dark theme switching;
- Added IP (range) blocking functionality;
- Supported switching between group chats;
- Compatible with operating systems other than Windows;
- Updated interface and optimized operation methods.

### Chat-online 4.2.6

- Updated fonts.
- Fix the problem of not being able to close the window.

### Chat-online 4.2.3

- Updated the server interface and added buttons;
- Updated the language to include English.

### Chat-online 4.2.0

- Updated the kick, ban, and unban functions on the server side.

### Chat-online 4.0.0

- 2nd generation GUI, changed interface style.
- Starting with this release, the Python code was open-sourced.

### Chat-online 2.0.0-3.0.0 (unreleased)

- The first generation of GUIs with a lot of bugs.

### Chat-online 1.0.0 (unreleased)

- Console version.
