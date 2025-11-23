import socket
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter import font as tkFont
import os
import sys
import time
import base64
import json
import re
import webbrowser
from PIL import Image, ImageTk
import io
import platform

# 检测操作系统
IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'
IS_MACOS = platform.system() == 'Darwin'

# 尝试导入全局热键监控库
try:
    from pynput import keyboard, mouse
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    if not IS_WINDOWS:
        print("提示: pynput库在某些系统上可能需要额外权限。可以使用 'pip install pynput' 安装。")
    else:
        print("警告: 未安装pynput库，全局热键功能将不可用。可以使用 'pip install pynput' 安装。")

class ChatClient:
    def __init__(self, master):
        self.master = master
        self.master.title("聊天客户端 Chat Client")
        # 透明度设置
        self.window_alpha = 1.0  # 默认100%不透明
        self.language = "zh"  # "zh" 或 "en"
        self.theme = "light"  # "light" 或 "dark"
        self.translations = self.load_translations()
        self.setup_widgets()
        self.sock = None
        self.muted = False
        self.window_flashing = False
        self.flash_count = 0
        self.max_flash_count = 10
        self.chat_mode = "group"
        self.private_target = None
        self.username = ""
        self.current_group_id = "main"
        self.current_group_name = "主群 Main Group"
        self.my_groups = {}
        self.is_group_owner = False
        self.is_group_admin = False
        self.is_server_owner = False
        self.file_list = []
        # 全局热键监控
        self.hotkey_listener = None
        self.hotkey_enabled = False
        self.left_mouse_pressed = False
        self.right_mouse_pressed = False
        self.middle_mouse_pressed = False
        self.keys_pressed = set()
        self.last_hotkey_check = 0
        # 新增：用于热键进程控制
        self.was_kill_triggered = True  # 初始与C++逻辑一致

    def setup_widgets(self):
        if IS_WINDOWS:
            font_family = "华文楷体"
        elif IS_MACOS:
            font_family = "PingFang SC"
        else:
            font_family = "DejaVu Sans"
        font = tkFont.Font(family=font_family, size=12)

        self.messages = tk.Text(self.master, state='disabled', font=font, wrap=tk.WORD)
        self.messages.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.messages.tag_config("link", foreground="blue", underline=True)
        self.messages.tag_bind("link", "<Button-1>", self.open_link)
        self.messages.tag_bind("link", "<Enter>", lambda e: self.messages.config(cursor="hand2"))
        self.messages.tag_bind("link", "<Leave>", lambda e: self.messages.config(cursor=""))
        self.image_references = []

        self.group_listbox = tk.Listbox(self.master, font=font, height=5)
        self.group_listbox.grid(row=0, column=3, sticky="nsew")
        self.group_listbox.bind("<Double-Button-1>", self.switch_group_from_list)
        
        self.client_listbox = tk.Listbox(self.master, font=font)
        self.client_listbox.grid(row=1, column=3, rowspan=3, sticky="nsew")
        self.client_listbox.bind("<Double-Button-1>", self.start_private_chat)
        
        mode_frame = tk.Frame(self.master)
        mode_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        
        self.mode_label = tk.Label(mode_frame, text="当前: 主群", font=font)
        self.mode_label.pack(side=tk.LEFT, padx=2)
        
        self.mode_button = tk.Button(mode_frame, text="切换私聊", command=self.toggle_chat_mode, font=font)
        self.mode_button.pack(side=tk.LEFT, padx=2)
        
        self.create_group_button = tk.Button(mode_frame, text="创建小群", command=self.create_group_dialog, font=font)
        self.create_group_button.pack(side=tk.LEFT, padx=2)
        
        self.join_group_button = tk.Button(mode_frame, text="加入群", command=self.join_group_dialog, font=font)
        self.join_group_button.pack(side=tk.LEFT, padx=2)
        
        self.invite_button = tk.Button(mode_frame, text="拉人", command=self.invite_user_dialog, font=font)
        self.invite_button.pack(side=tk.LEFT, padx=2)
        
        self.manage_frame = tk.Frame(self.master)
        self.manage_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        
        self.kick_member_button = tk.Button(self.manage_frame, text="踢出成员", command=self.kick_member_dialog, font=font, state='disabled')
        self.kick_member_button.pack(side=tk.LEFT, padx=2)
        
        self.mute_member_button = tk.Button(self.manage_frame, text="禁言成员", command=self.mute_member_dialog, font=font, state='disabled')
        self.mute_member_button.pack(side=tk.LEFT, padx=2)
        
        self.unmute_member_button = tk.Button(self.manage_frame, text="解禁成员", command=self.unmute_member_dialog, font=font, state='disabled')
        self.unmute_member_button.pack(side=tk.LEFT, padx=2)
        
        self.set_admin_button = tk.Button(self.manage_frame, text="设置管理员", command=self.set_admin_dialog, font=font, state='disabled')
        self.set_admin_button.pack(side=tk.LEFT, padx=2)
        
        self.remove_admin_button = tk.Button(self.manage_frame, text="移除管理员", command=self.remove_admin_dialog, font=font, state='disabled')
        self.remove_admin_button.pack(side=tk.LEFT, padx=2)
        
        settings_frame = tk.Frame(self.master)
        settings_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        
        self.language_button = tk.Button(settings_frame, text="中文/EN", command=self.toggle_language, font=font)
        self.language_button.pack(side=tk.LEFT, padx=2)
        
        self.theme_button = tk.Button(settings_frame, text="主题 Theme", command=self.toggle_theme, font=font)
        self.theme_button.pack(side=tk.LEFT, padx=2)
        
        alpha_label = tk.Label(settings_frame, text="透明度:", font=font)
        alpha_label.pack(side=tk.LEFT, padx=2)
        self.alpha_var = tk.StringVar(value="100%")
        self.alpha_menu = tk.OptionMenu(settings_frame, self.alpha_var, 
                                   *[f"{i}%" for i in range(10, 101, 10)],
                                   command=self.on_alpha_change)
        self.alpha_menu.config(font=font)
        self.alpha_menu.pack(side=tk.LEFT, padx=2)

        self.hotkey_button = tk.Button(settings_frame, text="热键", command=self.toggle_hotkeys, font=font)
        self.hotkey_button.pack(side=tk.LEFT, padx=2)
        
        self.leave_group_button = tk.Button(settings_frame, text="退出群聊", command=self.leave_group_dialog, font=font)
        self.leave_group_button.pack(side=tk.LEFT, padx=2)
        
        file_frame = tk.Frame(self.master)
        file_frame.grid(row=0, column=4, rowspan=4, sticky="nsew")
        
        tk.Label(file_frame, text="文件列表 Files", font=font).pack()
        self.file_listbox = tk.Listbox(file_frame, font=font, height=15)
        self.file_listbox.pack(fill=tk.BOTH, expand=True)
        self.file_listbox.bind("<Double-Button-1>", self.download_file_from_list)

        self.input_user = tk.Entry(self.master, font=font)
        self.input_user.grid(row=4, column=0, columnspan=2, sticky="ew")
        
        button_frame = tk.Frame(self.master)
        button_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        
        self.send_button = tk.Button(button_frame, text="发送", command=self.send_message, font=font)
        self.send_button.pack(side=tk.LEFT, padx=2)
        
        self.file_button = tk.Button(button_frame, text="文件", command=self.send_file, font=font)
        self.file_button.pack(side=tk.LEFT, padx=2)
        
        # 不再添加图片按钮
        # self.image_button = tk.Button(button_frame, text="图片", command=self.send_image, font=font)
        # self.image_button.pack(side=tk.LEFT, padx=2)
        
        self.emoji_button = tk.Button(button_frame, text="表情", command=self.show_emoji_picker, font=font)
        self.emoji_button.pack(side=tk.LEFT, padx=2)
        
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_rowconfigure(0, weight=1)
        
        self.master.bind("<Return>", self.send_message)
        self.master.bind("<Map>", self.on_window_focus)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_theme()
        self.set_window_alpha(self.window_alpha)

    def connect(self, host, port, user):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((host, port))
            self.username = user
            threading.Thread(target=self.receive_message, args=(user,), daemon=True).start()
        except socket.error as e:
            error_msg = f"无法连接到服务器: {e}" if self.language == "zh" else f"Can't connect to server: {e}"
            error_title = "连接错误" if self.language == "zh" else "Connection Error"
            messagebox.showerror(error_title, error_msg)
            self.master.destroy()
        else:
            success_msg = f"已连接到服务器 {host}:{port}" if self.language == "zh" else f"Connected to server {host}:{port}"
            success_title = "连接成功" if self.language == "zh" else "Connection Successful"
            messagebox.showinfo(success_title, success_msg)
            self.sock.sendall(f"/name {user}".encode('utf-8'))
            join_msg = f"用户 {user} 加入了聊天室。" if self.language == "zh" else f"User {user} has joined the chat room."
            self.sock.sendall(join_msg.encode('utf-8'))
            main_group_name = "主群" if self.language == "zh" else "Main Group"
            self.my_groups = {"main": main_group_name}
            self.current_group_id = "main"
            self.current_group_name = main_group_name
            self.update_group_list()
            self.sock.sendall("/listgroups".encode('utf-8'))
            self.sock.sendall(f"/checkpermission {self.current_group_id}".encode('utf-8'))
            self.sock.sendall(f"/requestfilelist {self.current_group_id}".encode('utf-8'))

    def receive_message(self, username):
        buffer = b''
        while True:
            try:
                data = self.sock.recv(8192)
                if not data:
                    break
                buffer += data

                if buffer.startswith(b'{'):
                    try:
                        message_str = buffer.decode('utf-8')
                        brace_count = 0
                        end_idx = -1
                        for i in range(len(message_str) - 1, -1, -1):
                            if message_str[i] == '}':
                                brace_count += 1
                                if brace_count == 1:
                                    end_idx = i
                                    break
                            elif message_str[i] == '{':
                                brace_count -= 1
                                if brace_count < 0:
                                    break
                        if end_idx != -1:
                            try:
                                json_msg = json.loads(message_str[:end_idx+1])
                                buffer = buffer[end_idx+1:].lstrip()
                                self.handle_file_receive(json_msg)
                                continue
                            except json.JSONDecodeError:
                                if len(buffer) > 10 * 1024 * 1024:
                                    buffer = b''
                                    continue
                                continue
                    except UnicodeDecodeError:
                        if len(buffer) > 10 * 1024 * 1024:
                            buffer = b''
                            continue
                        continue

                try:
                    # 修正：避免group列表有permissioninfo等多余内容，引入只取首个 \n 行
                    if b'\n' in buffer:
                        parts = buffer.split(b'\n', 1)
                        message = parts[0].decode('utf-8')
                        buffer = parts[1] if len(parts) > 1 else b''
                    else:
                        if len(buffer) > 8192:
                            continue
                        message = buffer.decode('utf-8')
                        buffer = b''
                except UnicodeDecodeError:
                    continue

                # 修复分组列表残留"/groupslist True/Permissioninfo"伪条目，只处理格式合法的group分割
                if message.startswith("/name "):
                    self.update_client_list(message.split()[1:])
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, '正在刷新成员列表。Refreshing user list.\n')
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
                elif message.startswith("/groupcreated "):
                    parts = message.split(' ', 3)
                    if len(parts) >= 4:
                        group_id = parts[1]
                        group_name = parts[2]
                        invite_code = parts[3]
                        self.my_groups[group_id] = group_name
                        self.current_group_id = group_id
                        self.current_group_name = group_name
                        self.update_group_list()
                        self.mode_label.config(text=f"当前: {group_name}")
                        messagebox.showinfo("成功", f"群组创建成功！\n群组名称: {group_name}\n邀请码: {invite_code}\n\nGroup created successfully!\nGroup name: {group_name}\nInvite code: {invite_code}")
                elif message.startswith("/joinedgroup "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        group_name = parts[2]
                        self.my_groups[group_id] = group_name
                        self.current_group_id = group_id
                        self.current_group_name = group_name
                        self.update_group_list()
                        self.mode_label.config(text=f"当前: {group_name}")
                        messagebox.showinfo("成功", f"已加入群组 {group_name}。Joined group {group_name}.")
                elif message.startswith("/switchedgroup "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        group_name = parts[2]
                        self.current_group_id = group_id
                        self.current_group_name = group_name
                        self.mode_label.config(text=f"当前: {group_name}")
                        self.messages.configure(state='normal')
                        self.messages.delete(1.0, tk.END)
                        self.messages.configure(state='disabled')
                        self.sock.sendall(f"/requesthistory {group_id}".encode('utf-8'))
                        self.sock.sendall(f"/checkpermission {group_id}".encode('utf-8'))
                        self.sock.sendall(f"/requestfilelist {group_id}".encode('utf-8'))
                elif message.startswith("/groupslist "):
                    groups_str = message[12:].strip()
                    # 修复：去除 PermissionInfo 或 True 等伪项目
                    valid_groups = []
                    if groups_str:
                        candidates = groups_str.strip().split()
                        for group_info in candidates:
                            # 真正的群组项含有"|"且不是PermissionInfo等
                            if "|" in group_info and not group_info.lower().startswith("permissioninfo"):
                                valid_groups.append(group_info)
                        self.my_groups = {}
                        for group_info in valid_groups:
                            parts = group_info.split('|')
                            if len(parts) >= 2:
                                group_id = parts[0]
                                group_name = parts[1]
                                is_current = False
                                # 允许有第三段, 并且必须为True才为当前
                                if len(parts) >= 3 and parts[2] == 'True':
                                    is_current = True
                                self.my_groups[group_id] = group_name
                                if is_current:
                                    self.current_group_id = group_id
                                    self.current_group_name = group_name
                        # 若未找到当前群组, 默认选主群
                        if not self.my_groups:
                            self.my_groups = {"main": "主群 Main Group"}
                            self.current_group_id = "main"
                            self.current_group_name = "主群 Main Group"
                        else:
                            # 如果未指定当前群组则自动选择第一个
                            if self.current_group_id not in self.my_groups:
                                for gid, gname in self.my_groups.items():
                                    self.current_group_id = gid
                                    self.current_group_name = gname
                                    break
                        self.update_group_list()
                        self.mode_label.config(text=f"当前: {self.current_group_name}")
                        self.sock.sendall(f"/checkpermission {self.current_group_id}".encode('utf-8'))
                        self.sock.sendall(f"/requestfilelist {self.current_group_id}".encode('utf-8'))
                    else:
                        self.my_groups = {"main": "主群 Main Group"}
                        self.current_group_id = "main"
                        self.current_group_name = "主群 Main Group"
                        self.update_group_list()
                        self.mode_label.config(text="当前: 主群")
                        self.update_permission_buttons()
                elif message.startswith("/permissioninfo "):
                    parts = message.split()
                    # 若实际有多余内容也可无视，只处理前三个布尔
                    if len(parts) >= 4:
                        self.is_group_owner = parts[1] == 'True'
                        self.is_group_admin = parts[2] == 'True'
                        self.is_server_owner = parts[3] == 'True'
                        self.update_permission_buttons()
                elif message.startswith("你已被设置为服主"):
                    self.is_server_owner = True
                    self.update_permission_buttons()
                    messagebox.showinfo("提示", "你已被设置为服主。You have been set as server owner.")
                elif message.startswith("/filelist "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        files_str = parts[2]
                        if files_str and group_id == self.current_group_id:
                            files_list = files_str.split()
                            for file_info in files_list:
                                file_parts = file_info.split('|')
                                if len(file_parts) >= 2:
                                    filename = file_parts[0]
                                    sender = file_parts[1]
                                    timestamp = float(file_parts[2]) if len(file_parts) >= 3 else time.time()
                                    found = False
                                    for f in self.file_list:
                                        if f[0] == filename and f[2] == sender and f[3] == group_id:
                                            found = True
                                            break
                                    if not found:
                                        self.file_list.append((filename, timestamp, sender, group_id))
                            self.file_list.sort(key=lambda x: x[1], reverse=True)
                            self.refresh_file_list()
                elif message.startswith("/leftgroup "):
                    group_id = message[11:].strip()
                    if group_id in self.my_groups:
                        del self.my_groups[group_id]
                        if self.current_group_id == group_id:
                            self.current_group_id = "main"
                            self.current_group_name = "主群 Main Group"
                            self.mode_label.config(text="当前: 主群")
                        self.update_group_list()
                        msg = "已退出群组。Left group." if self.language == "zh" else "Left group."
                        messagebox.showinfo("提示" if self.language == "zh" else "Info", msg)
                elif message == f"{username} 已被踢出聊天室。{username} ,you have been kicked out of the chat room.":
                    messagebox.showinfo("提示 Prompt", "你已被踢出聊天室。You have been kicked out of the chat room.")
                    os._exit(0)
                    break
                elif message == f"用户 {username} 被禁言。{username} ,you have been banned.":
                    self.muted = True
                    messagebox.showinfo("提示", "你已被禁言。You have been banned.")
                elif message == f"用户 {username} 被解除禁言。{username} ,you have been unbanned.":
                    self.muted = False
                    messagebox.showinfo("提示", "你已被解除禁言。You have been unbanned.")
                elif message == f"/quit":
                    messagebox.showinfo("提示", "服务器已关闭。The server has been closed.")
                    os._exit(0)
                    break
                else:
                    self.messages.configure(state='normal')
                    self.insert_message_with_links(message)
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
                    if not self.master.focus_get():
                        self.flash_window()
            except socket.error as e:
                print(f"接收错误 Receive error: {e}")
                break

    # 其余方法不变 ...

    def on_alpha_change(self, value):
        alpha = int(value.replace('%', '')) / 100.0
        self.set_window_alpha(alpha)
        # 修复：切换透明度时，刷新主题色
        self.apply_theme()
    
    def set_window_alpha(self, alpha):
        if not IS_WINDOWS:
            try:
                self.master.attributes('-alpha', alpha)
                self.window_alpha = alpha
            except:
                if IS_MACOS:
                    messagebox.showinfo("提示", "macOS系统上窗口透明度功能可能不可用。")
                self.window_alpha = 1.0
        else:
            try:
                self.master.attributes('-alpha', alpha)
                self.window_alpha = alpha
            except:
                messagebox.showerror("错误", "您的系统不支持窗口透明度设置。")

if __name__ == '__main__':
    root = tk.Tk()
    host = simpledialog.askstring("连接 Connect", "服务器地址 IP: ", initialvalue="127.0.0.1")
    port = simpledialog.askinteger("连接 Connect", "端口号 Port: ", initialvalue=8888)
    user = ''
    while not user or user.strip() == '':
        user = simpledialog.askstring("连接 Connect", "用户名 Username: ")
    client = ChatClient(root)
    client.connect(host, port, user)
    root.mainloop()