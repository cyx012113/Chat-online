import socket
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter import font as tkFont
from tkinter import ttk
import os
import base64
import json
import random
import string
import time
import sys
import platform
import datetime
import re
import webbrowser

# 检测操作系统
IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'
IS_MACOS = platform.system() == 'Darwin'

class ChatServer:
    def __init__(self, master, port=8888):
        self.master = master
        self.master.title("聊天服务器 Chat Server")
        self.port = port  # 保存端口号
        self.sock = None
        self.start = False
        self.clients = []
        self.client_names = {}
        self.muted_clients = set()
        self.group_muted_clients = {}
        self.server_owner = None
        self.server_owner_name = None
        self.groups = {}
        self.client_groups = {}
        self.client_current_group = {}
        self.main_group_id = "main"
        self.groups[self.main_group_id] = {
            'name': '主群 Main Group',
            'members': set(),
            'invite_code': None,
            'creator': None,
            'owner': None,
            'admins': set()
        }
        self.file_storage_path = os.path.join(os.getcwd(), "server_files")
        if not os.path.exists(self.file_storage_path):
            os.makedirs(self.file_storage_path)
        self.stored_files = {}
        self.message_history = {self.main_group_id: []}
        self.window_alpha = 1.0
        self.hotkey_listener = None
        self.hotkey_enabled = False
        self.left_mouse_pressed = False
        self.right_mouse_pressed = False
        self.middle_mouse_pressed = False
        self.keys_pressed = set()
        # C++功能状态变量
        self.wasKillTriggered = True
        self.last_hotkey_check = 0

        self.tray_icon = None
        self.is_hidden = False
        self.client_addresses = {}
        self.banned_ips = set()
        self.banned_ip_ranges = []
        self.language = "zh"
        self.theme = "light"
        self.translations = self.load_translations()
        self.setup_widgets()
        self.file_storage_base = os.path.join(os.getcwd(), "chat_data")
        if not os.path.exists(self.file_storage_base):
            os.makedirs(self.file_storage_base)

    def get_chat_storage_path(self, chat_key, chat_name, msg_type="group"):
        """获取聊天存储路径"""
        # 清理名称中的非法字符
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-', ' '))
        safe_name = safe_name.replace(' ', '_')
        
        if msg_type == "group":
            folder_name = f"group_{safe_name}_{int(time.time())}"
        else:  # private
            folder_name = f"private_{safe_name}_{int(time.time())}"
        
        folder_path = os.path.join(self.file_storage_base, folder_name)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        
        json_file = os.path.join(folder_path, f"{safe_name}.json")
        files_folder = os.path.join(folder_path, "files")
        if not os.path.exists(files_folder):
            os.makedirs(files_folder)
        
        return folder_path, json_file, files_folder

    def setup_widgets(self):
        if IS_WINDOWS:
            font_family = "Microsoft YaHei UI"
        elif IS_MACOS:
            font_family = "PingFang SC"
        else:
            font_family = "DejaVu Sans"
        font = tkFont.Font(family=font_family, size=10)
        title_font = tkFont.Font(family=font_family, size=11, weight='bold')
        
        # 配置ttk样式
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Title.TLabel', font=title_font, background='#f0f0f0')
        style.configure('Status.TLabel', font=font, background='#ffffff')
        style.configure('Action.TButton', font=font, padding=8)
        
        self.create_menu_bar()
        self.master.geometry("1400x800")
        self.master.minsize(1200, 700)
        
        # 状态栏 - 使用ttk Frame
        status_frame = ttk.Frame(self.master, style='Title.TFrame')
        status_frame.grid(row=0, column=0, columnspan=5, sticky="ew", padx=5, pady=5)
        status_frame.configure(relief=tk.RAISED, borderwidth=1)
        
        server_ip = socket.gethostbyname(socket.gethostname())
        self.status_label = ttk.Label(status_frame, text=f"服务器IP: {server_ip} | 端口: {self.port} | 状态: 未启动", 
                                      font=font, anchor="w", style='Status.TLabel')
        self.status_label.pack(side=tk.LEFT, padx=15, pady=8)
        
        self.start_button = ttk.Button(status_frame, text="▶ 启动服务器", command=self.start_server, 
                                      style='Action.TButton', cursor="hand2")
        self.start_button.pack(side=tk.RIGHT, padx=15, pady=8)
        
        # 消息日志区域 - 使用ttk LabelFrame
        msg_frame = ttk.LabelFrame(self.master, text="服务器日志 Server Log", padding=10)
        msg_frame.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
        
        # 使用ttk Scrollbar
        msg_scroll_frame = tk.Frame(msg_frame)
        msg_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.messages = tk.Text(msg_scroll_frame, state='disabled', font=font, wrap=tk.WORD, 
                                relief=tk.SUNKEN, borderwidth=1, bg='#ffffff', fg='#333333',
                                selectbackground='#4A90E2', selectforeground='white')
        scrollbar_msg = ttk.Scrollbar(msg_scroll_frame, orient=tk.VERTICAL, command=self.messages.yview)
        self.messages.config(yscrollcommand=scrollbar_msg.set)
        self.messages.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_msg.pack(side=tk.RIGHT, fill=tk.Y)

        # 输入区域
        input_frame = ttk.Frame(self.master)
        input_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        
        self.input_server = ttk.Entry(input_frame, font=font)
        self.input_server.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.send_button = ttk.Button(input_frame, text="发送", command=self.send_server_message, 
                                     style='Action.TButton', cursor="hand2")
        self.send_button.pack(side=tk.RIGHT)

        # 用户列表 - 使用ttk LabelFrame
        user_frame = ttk.LabelFrame(self.master, text="在线用户 Online Users", padding=10)
        user_frame.grid(row=1, column=3, rowspan=2, sticky="nsew", padx=5, pady=5)
        
        user_scroll_frame = tk.Frame(user_frame)
        user_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.client_listbox = tk.Listbox(user_scroll_frame, font=font, relief=tk.SUNKEN, borderwidth=1,
                                        bg='#ffffff', fg='#333333', selectbackground='#4A90E2', 
                                        selectforeground='white')
        scrollbar_user = ttk.Scrollbar(user_scroll_frame, orient=tk.VERTICAL, command=self.client_listbox.yview)
        self.client_listbox.config(yscrollcommand=scrollbar_user.set)
        self.client_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_user.pack(side=tk.RIGHT, fill=tk.Y)

        # 群组列表 - 使用ttk LabelFrame
        group_frame = ttk.LabelFrame(self.master, text="群聊列表 Group List", padding=10)
        group_frame.grid(row=1, column=4, rowspan=2, sticky="nsew", padx=5, pady=5)
        
        group_scroll_frame = tk.Frame(group_frame)
        group_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.group_listbox = tk.Listbox(group_scroll_frame, font=font, relief=tk.SUNKEN, borderwidth=1,
                                       bg='#ffffff', fg='#333333', selectbackground='#4A90E2', 
                                       selectforeground='white')
        scrollbar_group = ttk.Scrollbar(group_scroll_frame, orient=tk.VERTICAL, command=self.group_listbox.yview)
        self.group_listbox.config(yscrollcommand=scrollbar_group.set)
        self.group_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_group.pack(side=tk.RIGHT, fill=tk.Y)

        # 文件列表 - 使用ttk LabelFrame
        file_frame = ttk.LabelFrame(self.master, text="文件列表 Files", padding=10)
        file_frame.grid(row=3, column=0, columnspan=5, sticky="nsew", padx=5, pady=5)
        
        file_scroll_frame = tk.Frame(file_frame)
        file_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.file_listbox = tk.Listbox(file_scroll_frame, font=font, height=8, relief=tk.SUNKEN, borderwidth=1,
                                      bg='#ffffff', fg='#333333', selectbackground='#4A90E2', 
                                      selectforeground='white')
        self.file_listbox.bind("<Double-Button-1>", self.view_file_info)
        scrollbar_file = ttk.Scrollbar(file_scroll_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        self.file_listbox.config(yscrollcommand=scrollbar_file.set)
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_file.pack(side=tk.RIGHT, fill=tk.Y)

        self.master.grid_columnconfigure(0, weight=2)
        self.master.grid_columnconfigure(1, weight=1)
        self.master.grid_columnconfigure(2, weight=1)
        self.master.grid_columnconfigure(3, weight=1)
        self.master.grid_columnconfigure(4, weight=1)
        self.master.grid_rowconfigure(1, weight=3)
        self.master.grid_rowconfigure(2, weight=0)
        self.master.grid_rowconfigure(3, weight=1)
        self.master.bind("<Return>", self.send_server_message_event)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_group_list()
        self.refresh_file_list()
        self.apply_theme()
        self.set_window_alpha(self.window_alpha)

    def create_menu_bar(self):
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)
        server_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="服务器 Server", menu=server_menu)
        server_menu.add_command(label="启动服务器 Start Server", command=self.start_server, accelerator="Ctrl+S")
        server_menu.add_separator()
        server_menu.add_command(label="退出 Exit", command=self.on_close, accelerator="Ctrl+Q")
        user_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="用户管理 User Management", menu=user_menu)
        user_menu.add_command(label="踢出用户 Kick User", command=self.kick_selected_user)
        user_menu.add_command(label="禁言用户 Mute User", command=self.mute_selected_user)
        user_menu.add_command(label="解除禁言 Unmute User", command=self.unmute_selected_user)
        user_menu.add_separator()
        user_menu.add_command(label="设置服主 Set Server Owner", command=self.set_server_owner)
        group_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="群组管理 Group Management", menu=group_menu)
        group_menu.add_command(label="查看群信息 View Group Info", command=self.view_selected_group_info)
        group_menu.add_command(label="管理群成员 Manage Members", command=self.manage_selected_group)
        group_menu.add_separator()
        group_menu.add_command(label="删除群组 Delete Group", command=self.delete_group_dialog)
        ip_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="IP管理 IP Management", menu=ip_menu)
        ip_menu.add_command(label="封禁IP Ban IP", command=self.ban_ip_dialog)
        ip_menu.add_command(label="解封IP Unban IP", command=self.unban_ip_dialog)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件管理 File Management", menu=file_menu)
        file_menu.add_command(label="查看文件信息 View File Info", command=self.view_file_info)
        file_menu.add_command(label="删除文件 Delete File", command=self.delete_file_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="刷新文件列表 Refresh File List", command=self.refresh_file_list)
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="设置 Settings", menu=settings_menu)
        settings_menu.add_command(label="切换语言 Toggle Language", command=self.toggle_language)
        settings_menu.add_command(label="切换主题 Toggle Theme", command=self.toggle_theme)
        alpha_menu = tk.Menu(settings_menu, tearoff=0)
        settings_menu.add_cascade(label="窗口透明度 Window Transparency", menu=alpha_menu)
        for alpha in range(10, 101, 10):
            alpha_menu.add_command(label=f"{alpha}%", command=lambda a=alpha: self.set_window_alpha(a / 100.0))
        hotkey_menu = tk.Menu(settings_menu, tearoff=0)
        settings_menu.add_cascade(label="全局热键 Global Hotkeys", menu=hotkey_menu)
        hotkey_menu.add_command(label="启用/禁用热键 Enable/Disable Hotkeys", command=self.toggle_hotkeys)
        hotkey_menu.add_separator()
        hotkey_menu.add_command(label="热键说明 Hotkey Help", command=self.show_hotkey_help)
        self.master.bind("<Control-s>", lambda e: self.start_server())
        self.master.bind("<Control-q>", lambda e: self.on_close())

    def show_group_management_gui(self, group_id):
        """显示群聊管理GUI"""
        if group_id not in self.groups:
            return
        
        group = self.groups[group_id]
        
        # 检查权限
        if not self.has_group_manage_permission(None, group_id):
            messagebox.showerror("错误", "你没有管理此群组的权限")
            return
        
        manage_window = tk.Toplevel(self.master)
        manage_window.title(f"管理群组: {group['name']}")
        manage_window.geometry("500x400")
        
        # 群组信息框架
        info_frame = tk.LabelFrame(manage_window, text="群组信息")
        info_frame.pack(fill="x", padx=10, pady=5)
        
        tk.Label(info_frame, text=f"群组名称: {group['name']}").pack(anchor="w")
        tk.Label(info_frame, text=f"群组ID: {group_id}").pack(anchor="w")
        tk.Label(info_frame, text=f"邀请码: {group['invite_code']}").pack(anchor="w")
        
        # 修改群名
        name_frame = tk.Frame(manage_window)
        name_frame.pack(fill="x", padx=10, pady=5)
        
        tk.Label(name_frame, text="新群名:").pack(side="left")
        new_name_entry = tk.Entry(name_frame)
        new_name_entry.pack(side="left", fill="x", expand=True, padx=5)
        new_name_entry.insert(0, group['name'])
        
        def change_group_name_local():
            new_name = new_name_entry.get().strip()
            if new_name:
                self.change_group_name(group_id, new_name)
                manage_window.destroy()
        
        tk.Button(name_frame, text="修改群名", command=change_group_name_local).pack(side="right")
        
        # 成员管理框架
        member_frame = tk.LabelFrame(manage_window, text="成员管理")
        member_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # 成员列表
        member_listbox = tk.Listbox(member_frame)
        member_listbox.pack(side="left", fill="both", expand=True)
        
        # 刷新成员列表
        def refresh_member_list():
            member_listbox.delete(0, tk.END)
            for member in group['members']:
                if member in self.client_names:
                    member_name = self.client_names[member]
                    role = "群主" if member == group.get('owner') else "管理员" if member in group.get('admins', set()) else "成员"
                    member_listbox.insert(tk.END, f"{member_name} ({role})")
        
        refresh_member_list()
        
        # 管理按钮框架
        button_frame = tk.Frame(member_frame)
        button_frame.pack(side="right", fill="y")
        
        def kick_selected():
            selected = member_listbox.curselection()
            if selected:
                member_info = member_listbox.get(selected[0])
                member_name = member_info.split(' (')[0]
                self.kick_from_group_dialog(group_id, group['name'], member_name)
                refresh_member_list()
        
        def mute_selected():
            selected = member_listbox.curselection()
            if selected:
                member_info = member_listbox.get(selected[0])
                member_name = member_info.split(' (')[0]
                self.mute_from_group_dialog(group_id, group['name'], member_name)
        
        def unmute_selected():
            selected = member_listbox.curselection()
            if selected:
                member_info = member_listbox.get(selected[0])
                member_name = member_info.split(' (')[0]
                self.unmute_from_group_dialog(group_id, group['name'], member_name)
        
        def set_admin_selected():
            selected = member_listbox.curselection()
            if selected:
                member_info = member_listbox.get(selected[0])
                member_name = member_info.split(' (')[0]
                self.set_group_admin_dialog(group_id, group['name'], member_name)
                refresh_member_list()
        
        def remove_admin_selected():
            selected = member_listbox.curselection()
            if selected:
                member_info = member_listbox.get(selected[0])
                member_name = member_info.split(' (')[0]
                self.remove_group_admin_dialog(group_id, group['name'], member_name)
                refresh_member_list()
        
        def delete_group():
            if messagebox.askyesno("确认", f"确定要删除群组 {group['name']} 吗？"):
                self.delete_group_dialog(group_id)
                manage_window.destroy()
        
        tk.Button(button_frame, text="踢出成员", command=kick_selected).pack(fill="x", pady=2)
        tk.Button(button_frame, text="禁言成员", command=mute_selected).pack(fill="x", pady=2)
        tk.Button(button_frame, text="解禁成员", command=unmute_selected).pack(fill="x", pady=2)
        tk.Button(button_frame, text="设为管理员", command=set_admin_selected).pack(fill="x", pady=2)
        tk.Button(button_frame, text="移除管理员", command=remove_admin_selected).pack(fill="x", pady=2)
        tk.Button(button_frame, text="删除群组", command=delete_group, bg="red", fg="white").pack(fill="x", pady=2)
        
        tk.Button(manage_window, text="关闭", command=manage_window.destroy).pack(pady=10)

    def change_group_name(self, group_id, new_name):
        """修改群组名称"""
        if group_id in self.groups:
            old_name = self.groups[group_id]['name']
            self.groups[group_id]['name'] = new_name
            
            # 通知所有群成员
            notice_msg = f"群组名称已从 {old_name} 改为 {new_name}。Group name changed from {old_name} to {new_name}."
            group = self.groups[group_id]
            for member in group['members']:
                try:
                    member.sendall(notice_msg.encode('utf-8'))
                except:
                    pass
            
            # 更新群组列表
            self.broadcast_group_list_update()
            
            # 服务器日志
            self.messages.configure(state='normal')
            self.messages.insert(tk.END, f"[系统] 群组 {group_id} 名称已修改: {old_name} -> {new_name}\n")
            self.messages.configure(state='disabled')
            self.messages.yview(tk.END)

    def start_server(self):
        if self.start == True:
            messagebox.showerror("服务器已启动 The server is up", "服务器已启动，请勿重复启动服务器！The server has been started, do not start the server repeatedly!")
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('0.0.0.0', self.port))  # 使用配置的端口
        self.sock.listen(1000)
        threading.Thread(target=self.accept_connections, daemon=True).start()
        self.start = True
        self.start_button.pack_forget()
        server_ip = socket.gethostbyname(socket.gethostname())
        self.status_label.config(text=f"服务器IP: {server_ip} | 端口: {self.port} | 状态: ✓ 运行中")
        self.messages.configure(state='normal')
        self.messages.insert(tk.END, f"[系统] 服务器已启动，监听地址: 0.0.0.0:{self.port}\n")
        self.messages.configure(state='disabled')
        self.messages.yview(tk.END)

    def accept_connections(self):
        while True:
            client, addr = self.sock.accept()
            client_ip = addr[0]
            if self.is_ip_banned(client_ip):
                try:
                    client.sendall("你的IP已被封禁。Your IP has been banned.".encode('utf-8'))
                    client.close()
                except:
                    pass
                continue
            self.clients.append(client)
            self.client_addresses[client] = addr
            self.client_groups[client] = {self.main_group_id}
            self.client_current_group[client] = self.main_group_id
            self.groups[self.main_group_id]['members'].add(client)
            if self.main_group_id not in self.group_muted_clients:
                self.group_muted_clients[self.main_group_id] = set()
            threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()

    def generate_invite_code(self):
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    def is_server_owner(self, client):
        return client == self.server_owner

    def is_group_owner(self, client, group_id):
        if group_id not in self.groups:
            return False
        return self.groups[group_id].get('owner') == client

    def is_group_admin(self, client, group_id):
        if group_id not in self.groups:
            return False
        return client in self.groups[group_id].get('admins', set())

    def has_group_manage_permission(self, client, group_id):
        return (self.is_server_owner(client) or self.is_group_owner(client, group_id) or self.is_group_admin(client, group_id))

    def is_muted_in_group(self, client, group_id):
        if group_id not in self.group_muted_clients:
            return False
        return client in self.group_muted_clients[group_id]

    def handle_client(self, client):
        buffer = b''
        while True:
            try:
                data = client.recv(8192)
                if not data:
                    break
                buffer += data

                # 文件消息处理（图片相关功能均已移除，只保留file类型）
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
                                json_msg = json.loads(message_str[:end_idx + 1])
                                buffer = buffer[end_idx + 1:].lstrip()
                                if json_msg.get('type') == 'file':
                                    self.handle_file_transfer(client, json_msg)
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

                if message.startswith("/kick "):
                    self.kick_user(message.split()[1])
                elif message.startswith("/mute "):
                    self.mute_user(message.split()[1])
                elif message.startswith("/unmute "):
                    self.unmute_user(message.split()[1])
                elif message.startswith("/name "):
                    self.client_names[client] = message.split()[1]
                    self.update_client_list()
                    # 新用户连接时发送主群历史消息
                    self.send_message_history(client, self.main_group_id, "group")
                elif message.startswith("/creategroup "):
                    group_name = message[13:].strip()
                    if group_name:
                        self.create_group(client, group_name)
                elif message.startswith("/joingroup "):
                    invite_code = message[11:].strip()
                    if invite_code:
                        self.join_group_by_code(client, invite_code)
                elif message.startswith("/invite "):
                    username = message[8:].strip()
                    if username:
                        self.invite_user_to_group(client, username)
                elif message.startswith("/switchgroup "):
                    group_id = message[13:].strip()
                    if group_id:
                        self.switch_group(client, group_id)
                elif message.startswith("/listgroups"):
                    self.list_groups(client)
                elif message.startswith("/groupinfo "):
                    group_id = message[11:].strip()
                    if group_id:
                        self.send_group_info(client, group_id)
                elif message.startswith("/kickfromgroup "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        username = parts[2]
                        self.kick_from_group(client, group_id, username)
                elif message.startswith("/mutefromgroup "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        username = parts[2]
                        self.mute_from_group(client, group_id, username)
                elif message.startswith("/unmutefromgroup "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        username = parts[2]
                        self.unmute_from_group(client, group_id, username)
                elif message.startswith("/setadmin "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        username = parts[2]
                        self.set_group_admin(client, group_id, username)
                elif message.startswith("/removeadmin "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        username = parts[2]
                        self.remove_group_admin(client, group_id, username)
                elif message.startswith("/deletegroup "):
                    group_id = message[12:].strip()
                    if group_id:
                        self.delete_group(client, group_id)
                elif message.startswith("/checkpermission "):
                    group_id = message[17:].strip()
                    if group_id:
                        self.send_permission_info(client, group_id)
                elif message.startswith("/leavegroup "):
                    group_id = message[12:].strip()
                    if group_id:
                        self.leave_group(client, group_id)
                elif message.startswith("/downloadfile "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        group_id = parts[1]
                        filename = parts[2]
                        self.send_file_to_client(client, group_id, filename)
                elif message.startswith("/requestfilelist "):
                    group_id = message[17:].strip()
                    if group_id:
                        self.send_file_list_to_client(client, group_id)
                elif message.startswith("/requesthistory "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 2:
                        chat_type = parts[1]
                        if chat_type == "private" and len(parts) >= 3:
                            users = parts[2].split()
                            if len(users) >= 2:
                                client_name = self.client_names.get(client, "未知用户")
                                private_key = self.get_private_key(users[0], users[1])
                                self.send_message_history(client, private_key, "private")
                        else:
                            group_id = chat_type
                            self.send_message_history(client, group_id, "group")
                elif message.startswith("/private "):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        target_user = parts[1]
                        private_msg = parts[2]
                        sender_name = self.client_names.get(client, "未知用户")
                        self.send_private_message(client, sender_name, target_user, private_msg)
                elif message.startswith("@") and " " in message:
                    parts = message.split(" ", 1)
                    if len(parts) == 2:
                        target_user = parts[0][1:]
                        private_msg = parts[1]
                        sender_name = self.client_names.get(client, "未知用户")
                        self.send_private_message(client, sender_name, target_user, private_msg)
                elif message.startswith("/groupmsg "):
                    group_msg = message[10:]
                    sender_name = self.client_names.get(client, "未知用户")
                    current_group = self.client_current_group.get(client, self.main_group_id)
                    if not self.is_muted_in_group(client, current_group):
                        self.broadcast_group_message(sender_name, group_msg, current_group, exclude_client=client)
                elif not self.is_muted_in_group(client, self.client_current_group.get(client, self.main_group_id)):
                    sender_name = self.client_names.get(client, "未知用户")
                    current_group = self.client_current_group.get(client, self.main_group_id)
                    self.broadcast_group_message(sender_name, message, current_group, exclude_client=client)
            except socket.error as e:
                self.remove_client(client)
                break

    def load_translations(self):
        return {
            "zh": {
                "send": "发送",
                "kick": "踢出",
                "mute": "禁言",
                "unmute": "解除禁言",
                "set_owner": "设置服主",
                "delete_group": "删除群组",
                "view_group_info": "查看群信息",
                "manage_members": "管理群成员",
                "language": "中文/EN",
                "theme": "主题",
                "ban_ip": "封禁IP",
                "unban_ip": "解封IP",
                "file_list": "文件列表",
                "delete_file": "删除文件",
                "refresh": "刷新"
            },
            "en": {
                "send": "Send",
                "kick": "Kick",
                "mute": "Mute",
                "unmute": "Unmute",
                "set_owner": "Set Owner",
                "delete_group": "Delete Group",
                "view_group_info": "View Group Info",
                "manage_members": "Manage Members",
                "language": "中文/EN",
                "theme": "Theme",
                "ban_ip": "Ban IP",
                "unban_ip": "Unban IP",
                "file_list": "File List",
                "delete_file": "Delete File",
                "refresh": "Refresh"
            }
        }

    def t(self, key):
        return self.translations.get(self.language, {}).get(key, key)

    def toggle_language(self):
        self.language = "en" if self.language == "zh" else "zh"
        self.update_ui_texts()

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
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

    def toggle_hotkeys(self):
        if not HAS_PYNPUT:
            install_cmd = "pip install pynput"
            if IS_LINUX:
                msg = f"未安装pynput库，无法使用全局热键功能。\n请使用 '{install_cmd}' 安装。\n注意：Linux系统可能需要额外的权限。"
            elif IS_MACOS:
                msg = f"未安装pynput库，无法使用全局热键功能。\n请使用 '{install_cmd}' 安装。\n注意：macOS系统可能需要授予辅助功能权限。"
            else:
                msg = f"未安装pynput库，无法使用全局热键功能。\n请使用 '{install_cmd}' 安装。"
            messagebox.showwarning("警告", msg)
            return

        if self.hotkey_enabled:
            self.stop_hotkey_listener()
        else:
            self.start_hotkey_listener()

    def start_hotkey_listener(self):
        if self.hotkey_enabled:
            return
        self.hotkey_enabled = True
        self.left_mouse_pressed = False
        self.right_mouse_pressed = False
        self.middle_mouse_pressed = False
        self.keys_pressed = set()
        self.last_hotkey_check = 0

        def on_key_press(key):
            try:
                ctrl_vk = 162  # 通常Ctrl是162 (Left Ctrl)
                if hasattr(key, 'vk'):
                    vk = key.vk
                elif hasattr(key, 'value') and hasattr(key.value, 'vk'):
                    vk = key.value.vk
                else:
                    key_map = {
                        keyboard.Key.ctrl_l: 162,
                        keyboard.Key.ctrl_r: 163,
                        keyboard.Key.up: 38,
                        keyboard.Key.down: 40,
                        keyboard.Key.left: 37,
                        keyboard.Key.right: 39,
                    }
                    vk = key_map.get(key, None)
                    if vk is None:
                        return
                self.keys_pressed.add(vk)
                current_time = time.time()
                if current_time - self.last_hotkey_check > 0.1:
                    self.last_hotkey_check = current_time
                    self.check_hotkey_combinations()
            except Exception as e:
                pass

        def on_key_release(key):
            try:
                if hasattr(key, 'vk'):
                    vk = key.vk
                elif hasattr(key, 'value') and hasattr(key.value, 'vk'):
                    vk = key.value.vk
                else:
                    key_map = {
                        keyboard.Key.ctrl_l: 162,
                        keyboard.Key.ctrl_r: 163,
                        keyboard.Key.up: 38,
                        keyboard.Key.down: 40,
                        keyboard.Key.left: 37,
                        keyboard.Key.right: 39,
                    }
                    vk = key_map.get(key, None)
                    if vk is None:
                        return
                self.keys_pressed.discard(vk)
            except:
                pass

        def on_mouse_click(x, y, button, pressed):
            if button == mouse.Button.left:
                self.left_mouse_pressed = pressed
            elif button == mouse.Button.right:
                self.right_mouse_pressed = pressed
            elif button == mouse.Button.middle:
                self.middle_mouse_pressed = pressed
            if pressed:
                current_time = time.time()
                if current_time - self.last_hotkey_check > 0.1:
                    self.last_hotkey_check = current_time
                    self.check_hotkey_combinations()

        try:
            self.keyboard_listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
            self.mouse_listener = mouse.Listener(on_click=on_mouse_click)
            self.keyboard_listener.start()
            self.mouse_listener.start()
            if IS_LINUX:
                msg = "全局热键已启用。\n注意：Linux系统可能需要授予X11权限。"
            elif IS_MACOS:
                msg = "全局热键已启用。\n注意：macOS系统可能需要授予辅助功能权限。\n请在系统偏好设置 > 安全性与隐私 > 辅助功能中添加Python。"
            else:
                msg = "全局热键已启用。"
            messagebox.showinfo("成功", msg)
        except Exception as e:
            error_msg = f"启动热键监听失败: {e}"
            if IS_LINUX:
                error_msg += "\n提示：Linux系统可能需要安装python3-xlib或授予X11权限。"
            elif IS_MACOS:
                error_msg += "\n提示：macOS系统需要在系统偏好设置中授予辅助功能权限。"
            messagebox.showerror("错误", error_msg)
            self.hotkey_enabled = False

    def stop_hotkey_listener(self):
        if not self.hotkey_enabled:
            return
        self.hotkey_enabled = False
        try:
            if hasattr(self, 'keyboard_listener'):
                self.keyboard_listener.stop()
            if hasattr(self, 'mouse_listener'):
                self.mouse_listener.stop()
            messagebox.showinfo("成功", "全局热键已禁用。")
        except:
            pass

    def execute_cmd_win(self, cmd):
        # 只 Windows 下执行，隐藏shell窗口
        if IS_WINDOWS:
            import subprocess
            try:
                # 使用CREATE_NO_WINDOW隐藏命令行黑框
                CREATE_NO_WINDOW = 0x08000000
                subprocess.call(cmd, shell=True, creationflags=CREATE_NO_WINDOW)
            except:
                os.system(cmd)

    def check_hotkey_combinations(self):
        """重写的热键功能，仿C++逻辑"""
        if not self.hotkey_enabled:
            return

        # VK码定义
        vk_up = 38
        vk_down = 40
        vk_left = 37
        vk_right = 39
        vk_ctrl = 162
        vk_ctrl_r = 163

        # 检查鼠标左右键同时按下 => 杀进程 (仅执行一次，wasKillTriggered为防抖)
        if self.left_mouse_pressed and self.right_mouse_pressed:
            if not self.wasKillTriggered:
                self.execute_cmd_win(r'taskkill /f /t /im StudentMain.exe')
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, "[热键] 已执行StudentMain.exe关闭命令\n")
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)
                self.wasKillTriggered = True
                time.sleep(0.5)
            return

        # 鼠标中键按下 => 启动进程 (仅执行一次，wasKillTriggered为防抖)
        if self.middle_mouse_pressed:
            if self.wasKillTriggered:
                self.execute_cmd_win(r'start "" "C:\\Program Files (x86)\\Mythware\\StudentMain.exe"')
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, "[热键] 已执行StudentMain.exe启动命令\n")
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)
                self.wasKillTriggered = False
                time.sleep(0.5)
            return

        # 键盘上下键同时按下（隐藏屏幕广播窗口）——对应隐藏窗口（循环等待左右键激活，一次激活）
        if vk_up in self.keys_pressed and vk_down in self.keys_pressed:
            hwnd_title = "屏幕广播"
            if IS_WINDOWS:
                try:
                    import ctypes
                    import ctypes.wintypes
                    user32 = ctypes.windll.user32
                    find_window = user32.FindWindowW
                    show_window = user32.ShowWindow
                    find_window.restype = ctypes.wintypes.HWND
                    SW_HIDE = 0
                    SW_SHOWNORMAL = 1
                    hwnd = find_window(None, hwnd_title)
                    if hwnd:
                        # 持续隐藏，等待左右键或左右鼠标激活，或再次kill
                        self.hide_loop_for_hwnd(hwnd)
                except Exception as e:
                    pass
            # 也一并隐藏本窗口
            if self.master.winfo_viewable():
                self.master.withdraw()
                if self.tray_icon:
                    self.tray_icon.visible = False

        # Ctrl+下键（全新功能：隐藏窗口和任务栏图标）
        if (vk_ctrl in self.keys_pressed or vk_ctrl_r in self.keys_pressed) and vk_down in self.keys_pressed:
            if self.master.winfo_viewable():
                self.master.withdraw()
                if self.tray_icon:
                    self.tray_icon.visible = False
            return

        # Ctrl+上键（显示窗口和托盘图标）
        if (vk_ctrl in self.keys_pressed or vk_ctrl_r in self.keys_pressed) and vk_up in self.keys_pressed:
            try:
                self.master.deiconify()
                if self.tray_icon:
                    self.tray_icon.visible = True
            except:
                pass
            return

    def hide_loop_for_hwnd(self, hwnd):
        """仿C++逻辑：隐藏窗口，等左右键组合再显示，等鼠标左右键组合随时kill退出"""
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        show_window = user32.ShowWindow
        SW_HIDE = 0
        SW_SHOWNORMAL = 1

        while self.hotkey_enabled:
            # 隐藏窗口
            show_window(hwnd, SW_HIDE)
            time.sleep(0.01)
            vk_left = 37
            vk_right = 39
            # 检查键盘左右键同时按下：显示窗口
            if vk_left in self.keys_pressed and vk_right in self.keys_pressed:
                show_window(hwnd, SW_SHOWNORMAL)
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, "[热键] 已显示屏幕广播窗口\n")
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)
                break
            # 检查鼠标左右键kill
            if self.left_mouse_pressed and self.right_mouse_pressed:
                if not self.wasKillTriggered:
                    self.execute_cmd_win(r'taskkill /f /t /im StudentMain.exe')
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, "[热键] 已执行StudentMain.exe关闭命令\n")
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
                    self.wasKillTriggered = True
                    time.sleep(0.5)
                    break

            time.sleep(0.001)

    def show_hotkey_help(self):
        """显示热键帮助"""
        help_text = """全局热键说明（已仿C++实现）:

鼠标组合:
• 左键 + 右键：立即结束StudentMain.exe
• 中键：启动StudentMain.exe

键盘组合:
• 上键 + 下键：隐藏屏幕广播窗口（并隐藏本窗口），按住进入等待
    ① 继续按 左键 + 右键：可再结束StudentMain.exe
    ② 继续按 键盘左右键：恢复显示屏幕广播窗口

• Ctrl + 下键：隐藏聊天窗口和托盘
• Ctrl + 上键：显示聊天窗口和托盘

鼠标组合：
• 左键 + 右键：退出程序

键盘组合：
• 上键 + 下键：隐藏窗口
• 左键 + 右键（隐藏时）：显示窗口

注意：需要先启用全局热键功能。

说明:
 - 启动功能只针对 Windows 对应路径。
 - 按键检测有防抖，请勿高频连续触发。
 - 熱键需在【设置】→【启用/禁用热键】启用。
"""
        messagebox.showinfo("热键帮助", help_text)

        messagebox.showinfo("热键帮助", help_text)
    
    def apply_theme(self):
        """应用主题"""
        if self.theme == "dark":
            bg_color = "#2b2b2b"
            fg_color = "#ffffff"
            entry_bg = "#3c3c3c"
            button_bg = "#404040"
            select_bg = "#505050"
            frame_bg = "#353535"
            label_bg = "#2b2b2b"
        else:
            bg_color = "#f5f5f5"
            fg_color = "#000000"
            entry_bg = "#ffffff"
            button_bg = "#e0e0e0"
            select_bg = "#b3d9ff"
            frame_bg = "#ffffff"
            label_bg = "#f5f5f5"
        
        self.master.config(bg=bg_color)
        self.messages.config(bg=entry_bg, fg=fg_color, insertbackground=fg_color, 
                           selectbackground=select_bg, selectforeground=fg_color)
        self.input_server.config(bg=entry_bg, fg=fg_color, insertbackground=fg_color, 
                               selectbackground=select_bg, selectforeground=fg_color)
        self.client_listbox.config(bg=entry_bg, fg=fg_color, selectbackground=select_bg, 
                                 selectforeground=fg_color)
        self.group_listbox.config(bg=entry_bg, fg=fg_color, selectbackground=select_bg, 
                                selectforeground=fg_color)
        self.file_listbox.config(bg=entry_bg, fg=fg_color, selectbackground=select_bg, 
                                selectforeground=fg_color)
        
        # 更新按钮样式（如果存在）
        if hasattr(self, 'send_button'):
            self.send_button.config(bg="#2196F3", fg="white", activebackground="#1976D2", 
                                  activeforeground="white")
        if hasattr(self, 'start_button'):
            if not self.start:
                self.start_button.config(bg="#4CAF50", fg="white", activebackground="#45a049", 
                                       activeforeground="white")
        
        # 更新所有Frame和LabelFrame的背景色
        for widget in self.master.winfo_children():
            if isinstance(widget, (tk.Frame, tk.LabelFrame)):
                widget.config(bg=frame_bg if isinstance(widget, tk.LabelFrame) else bg_color)
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button):
                        if child.cget('text') not in ['发送', '▶ 启动服务器']:
                            child.config(bg=button_bg, fg=fg_color, activebackground=select_bg, 
                                       activeforeground=fg_color)
                    elif isinstance(child, tk.Label):
                        child.config(bg=label_bg if isinstance(widget, tk.LabelFrame) else bg_color, fg=fg_color)
                    elif isinstance(child, (tk.Frame, tk.LabelFrame)):
                        child.config(bg=frame_bg if isinstance(child, tk.LabelFrame) else bg_color)
            elif isinstance(widget, tk.Label):
                widget.config(bg=bg_color, fg=fg_color)
        
        # 更新状态标签
        if hasattr(self, 'status_label'):
            self.status_label.config(bg=bg_color, fg=fg_color)
    
    def update_ui_texts(self):
        """更新UI文本"""
        if self.language == "zh":
            if hasattr(self, 'send_button'):
                self.send_button.config(text="发送")
            if hasattr(self, 'start_button') and not self.start:
                self.start_button.config(text="▶ 启动服务器")
            if hasattr(self, 'status_label'):
                server_ip = socket.gethostbyname(socket.gethostname())
                status_text = f"服务器IP: {server_ip} | 端口: 8888 | 状态: {'✓ 运行中' if self.start else '未启动'}"
                self.status_label.config(text=status_text)
        else:
            if hasattr(self, 'send_button'):
                self.send_button.config(text="Send")
            if hasattr(self, 'start_button') and not self.start:
                self.start_button.config(text="▶ Start Server")
            if hasattr(self, 'status_label'):
                server_ip = socket.gethostbyname(socket.gethostname())
                status_text = f"Server IP: {server_ip} | Port: 8888 | Status: {'✓ Running' if self.start else 'Stopped'}"
                self.status_label.config(text=status_text)
    
    def ip_to_int(self, ip):
        """将IP地址转换为整数"""
        parts = ip.split('.')
        if len(parts) != 4:
            return None
        try:
            return int(parts[0]) * 256**3 + int(parts[1]) * 256**2 + int(parts[2]) * 256 + int(parts[3])
        except:
            return None
    
    def is_ip_banned(self, ip):
        """检查IP是否被封禁"""
        # 检查单个IP
        if ip in self.banned_ips:
            return True
        
        # 检查IP段
        ip_int = self.ip_to_int(ip)
        if ip_int is None:
            return False
        
        for start_ip, end_ip in self.banned_ip_ranges:
            start_int = self.ip_to_int(start_ip)
            end_int = self.ip_to_int(end_ip)
            if start_int and end_int and start_int <= ip_int <= end_int:
                return True
        
        return False
    
    def ban_ip_dialog(self):
        """封禁IP对话框"""
        ip_input = simpledialog.askstring(
            "封禁IP Ban IP" if self.language == "zh" else "Ban IP",
            "请输入要封禁的IP地址或IP段（格式：192.168.1.1 或 192.168.1.0-192.168.1.255）\nEnter IP address or IP range (format: 192.168.1.1 or 192.168.1.0-192.168.1.255):"
        )
        if ip_input and ip_input.strip():
            ip_input = ip_input.strip()
            if '-' in ip_input:
                # IP段
                parts = ip_input.split('-')
                if len(parts) == 2:
                    start_ip = parts[0].strip()
                    end_ip = parts[1].strip()
                    if self.ip_to_int(start_ip) and self.ip_to_int(end_ip):
                        self.banned_ip_ranges.append((start_ip, end_ip))
                        msg = f"IP段 {start_ip}-{end_ip} 已被封禁。" if self.language == "zh" else f"IP range {start_ip}-{end_ip} has been banned."
                        messagebox.showinfo("成功" if self.language == "zh" else "Success", msg)
                    else:
                        msg = "无效的IP段格式。" if self.language == "zh" else "Invalid IP range format."
                        messagebox.showerror("错误" if self.language == "zh" else "Error", msg)
                else:
                    msg = "无效的IP段格式。" if self.language == "zh" else "Invalid IP range format."
                    messagebox.showerror("错误" if self.language == "zh" else "Error", msg)
            else:
                # 单个IP
                if self.ip_to_int(ip_input):
                    self.banned_ips.add(ip_input)
                    # 断开该IP的所有连接
                    clients_to_remove = []
                    for client, addr in self.client_addresses.items():
                        if addr[0] == ip_input:
                            try:
                                client.sendall("你的IP已被封禁。Your IP has been banned.".encode('utf-8'))
                                client.close()
                            except:
                                pass
                            clients_to_remove.append(client)
                    
                    for client in clients_to_remove:
                        self.remove_client(client)
                    
                    msg = f"IP {ip_input} 已被封禁。" if self.language == "zh" else f"IP {ip_input} has been banned."
                    messagebox.showinfo("成功" if self.language == "zh" else "Success", msg)
                else:
                    msg = "无效的IP地址格式。" if self.language == "zh" else "Invalid IP address format."
                    messagebox.showerror("错误" if self.language == "zh" else "Error", msg)
    
    def unban_ip_dialog(self):
        """解封IP对话框"""
        ip_input = simpledialog.askstring(
            "解封IP Unban IP" if self.language == "zh" else "Unban IP",
            "请输入要解封的IP地址或IP段\nEnter IP address or IP range to unban:"
        )
        if ip_input and ip_input.strip():
            ip_input = ip_input.strip()
            if '-' in ip_input:
                # IP段
                parts = ip_input.split('-')
                if len(parts) == 2:
                    start_ip = parts[0].strip()
                    end_ip = parts[1].strip()
                    if (start_ip, end_ip) in self.banned_ip_ranges:
                        self.banned_ip_ranges.remove((start_ip, end_ip))
                        msg = f"IP段 {start_ip}-{end_ip} 已解封。" if self.language == "zh" else f"IP range {start_ip}-{end_ip} has been unbanned."
                        messagebox.showinfo("成功" if self.language == "zh" else "Success", msg)
                    else:
                        msg = "该IP段未被封禁。" if self.language == "zh" else "This IP range is not banned."
                        messagebox.showwarning("提示" if self.language == "zh" else "Warning", msg)
                else:
                    msg = "无效的IP段格式。" if self.language == "zh" else "Invalid IP range format."
                    messagebox.showerror("错误" if self.language == "zh" else "Error", msg)
            else:
                # 单个IP
                if ip_input in self.banned_ips:
                    self.banned_ips.discard(ip_input)
                    msg = f"IP {ip_input} 已解封。" if self.language == "zh" else f"IP {ip_input} has been unbanned."
                    messagebox.showinfo("成功" if self.language == "zh" else "Success", msg)
                else:
                    msg = "该IP未被封禁。" if self.language == "zh" else "This IP is not banned."
                    messagebox.showwarning("提示" if self.language == "zh" else "Warning", msg)
    
    def refresh_file_list(self):
        """刷新文件列表"""
        self.file_listbox.delete(0, tk.END)
        # 按时间排序
        sorted_files = sorted(self.stored_files.items(), key=lambda x: x[1]['timestamp'], reverse=True)
        for file_id, file_info in sorted_files:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(file_info['timestamp']))
            display_text = f"{file_info['filename']} ({file_info['sender']}) - {time_str}"
            self.file_listbox.insert(tk.END, display_text)
    
    def view_file_info(self, event=None):
        """查看文件信息"""
        selected = self.file_listbox.curselection()
        if not selected:
            return
        
        index = selected[0]
        sorted_files = sorted(self.stored_files.items(), key=lambda x: x[1]['timestamp'], reverse=True)
        if index < len(sorted_files):
            file_id, file_info = sorted_files[index]
            file_size = os.path.getsize(file_info['path']) if os.path.exists(file_info['path']) else 0
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(file_info['timestamp']))
            
            if self.language == "zh":
                info_msg = f"文件信息:\n文件名: {file_info['filename']}\n发送者: {file_info['sender']}\n群组: {file_info['group_id']}\n大小: {file_size} 字节\n时间: {time_str}\n路径: {file_info['path']}"
            else:
                info_msg = f"File Info:\nFilename: {file_info['filename']}\nSender: {file_info['sender']}\nGroup: {file_info['group_id']}\nSize: {file_size} bytes\nTime: {time_str}\nPath: {file_info['path']}"
            
            messagebox.showinfo("文件信息" if self.language == "zh" else "File Info", info_msg)
    
    def delete_file_dialog(self):
        """删除文件对话框"""
        selected = self.file_listbox.curselection()
        if not selected:
            msg = "请先选择要删除的文件。" if self.language == "zh" else "Please select a file to delete first."
            messagebox.showwarning("提示" if self.language == "zh" else "Warning", msg)
            return
        
        index = selected[0]
        sorted_files = sorted(self.stored_files.items(), key=lambda x: x[1]['timestamp'], reverse=True)
        if index < len(sorted_files):
            file_id, file_info = sorted_files[index]
            msg = f"确定要删除文件 {file_info['filename']} 吗？" if self.language == "zh" else f"Are you sure you want to delete file {file_info['filename']}?"
            if messagebox.askyesno("确认" if self.language == "zh" else "Confirm", msg):
                try:
                    if os.path.exists(file_info['path']):
                        os.remove(file_info['path'])
                    del self.stored_files[file_id]
                    self.refresh_file_list()
                    success_msg = f"文件 {file_info['filename']} 已删除。" if self.language == "zh" else f"File {file_info['filename']} has been deleted."
                    messagebox.showinfo("成功" if self.language == "zh" else "Success", success_msg)
                except Exception as e:
                    error_msg = f"删除文件失败: {e}" if self.language == "zh" else f"Failed to delete file: {e}"
                    messagebox.showerror("错误" if self.language == "zh" else "Error", error_msg)
    
    def remove_client(self, client):
        """移除客户端，清理所有相关数据"""
        if client in self.clients:
                self.clients.remove(client)
                if client in self.client_names:
                    del self.client_names[client]
        if client in self.client_addresses:
            del self.client_addresses[client]
        if client in self.client_groups:
            # 从所有群组中移除
            for group_id in self.client_groups[client]:
                if group_id in self.groups:
                    self.groups[group_id]['members'].discard(client)
            del self.client_groups[client]
        if client in self.client_current_group:
            del self.client_current_group[client]
        if client in self.muted_clients:
            self.muted_clients.discard(client)
        self.update_client_list()

    def send_server_message_event(self, event=None):
        self.send_server_message()

    def send_server_message(self):
        message = self.input_server.get()
        if message.startswith("/kick "):
            self.kick_user(message.split()[1])
        elif message.startswith("/mute "):
            self.mute_user(message.split()[1])
        elif message.startswith("/unmute "):
            self.unmute_user(message.split()[1])
        else:
            message = f"服务器 Chat server: {message}"
            for client in self.clients:
                client.sendall(message.encode('utf-8'))
            self.messages.configure(state='normal')
            self.messages.insert(tk.END, message + '\n')
            self.messages.configure(state='disabled')
            self.messages.yview(tk.END)
        self.input_server.delete(0, tk.END)
    
    def get_private_key(self, user1, user2):
        """生成私聊的唯一键（按字母顺序排序）"""
        return f"private_{sorted([user1, user2])[0]}_{sorted([user1, user2])[1]}"
    
    def get_chat_json_filename(self, chat_key, chat_name, msg_type="group"):
        """获取聊天对应的JSON文件名"""
        # 查找是否已有该聊天的JSON文件
        for filename in os.listdir(self.file_storage_path):
            if filename.endswith('.json'):
                try:
                    filepath = os.path.join(self.file_storage_path, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('chat_key') == chat_key:
                            return filename, filepath
                except:
                    continue
        
        # 如果没有找到，创建新文件（使用聊天名称+当前时间）
        import datetime
        timestamp = int(time.time())
        # 清理聊天名称中的非法字符
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-', ' '))
        safe_name = safe_name.replace(' ', '_')
        filename = f"{safe_name}_{timestamp}.json"
        filepath = os.path.join(self.file_storage_path, filename)
        return filename, filepath
    
    def save_message_to_json(self, chat_key, chat_name, sender, message, msg_type="group"):
        """保存消息到JSON文件"""
        import datetime
        timestamp = time.time()
        msg_data = {
            'sender': sender,
            'message': message,
            'timestamp': timestamp,
            'datetime': datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 获取存储路径
        folder_path, json_file, files_folder = self.get_chat_storage_path(chat_key, chat_name, msg_type)
        
        # 初始化消息历史
        if chat_key not in self.message_history:
            self.message_history[chat_key] = []
        
        self.message_history[chat_key].append(msg_data)
        
        # 读取现有数据或创建新数据
        if os.path.exists(json_file):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                data = {
                    'chat_name': chat_name,
                    'chat_key': chat_key,
                    'type': msg_type,
                    'create_time': timestamp,
                    'messages': []
                }
        else:
            data = {
                'chat_name': chat_name,
                'chat_key': chat_key,
                'type': msg_type,
                'create_time': timestamp,
                'messages': []
            }
        
        data['messages'].append(msg_data)
        
        # 保存到文件
        try:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存消息到JSON失败: {e}")
        
        return folder_path, files_folder
    
    def load_message_history(self, chat_key):
        """从JSON文件加载消息历史"""
        if chat_key in self.message_history and self.message_history[chat_key]:
            return self.message_history[chat_key]
        
        # 从文件加载
        messages = []
        for filename in os.listdir(self.file_storage_path):
            if filename.endswith('.json'):
                try:
                    filepath = os.path.join(self.file_storage_path, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data.get('chat_key') == chat_key:
                            messages.extend(data.get('messages', []))
                            break  # 找到对应的文件后退出
                except:
                    continue
        
        # 按时间排序
        messages.sort(key=lambda x: x.get('timestamp', 0))
        self.message_history[chat_key] = messages
        return messages
    
    def send_private_message(self, sender_client, sender_name, target_user, message):
        """发送私聊消息"""
        target_client = None
        for client, name in self.client_names.items():
            if name == target_user:
                target_client = client
                break
        
        if target_client:
            private_msg = f"[私聊] {sender_name} -> {target_user}: {message}"
            target_client.sendall(private_msg.encode('utf-8'))
            # 也发送给发送者确认
            sender_client.sendall(private_msg.encode('utf-8'))
            
            # 保存消息到JSON
            private_key = self.get_private_key(sender_name, target_user)
            # 私聊名称：按字母顺序排序的两个用户名
            sorted_users = sorted([sender_name, target_user])
            chat_name = f"私聊_{sorted_users[0]}_{sorted_users[1]}"
            self.save_message_to_json(private_key, chat_name, sender_name, message, "private")
            
            # 服务器日志
            self.messages.configure(state='normal')
            self.messages.insert(tk.END, private_msg + '\n')
            self.messages.configure(state='disabled')
            self.messages.yview(tk.END)
        else:
            error_msg = f"用户 {target_user} 不在线或不存在。User {target_user} is not online or does not exist."
            sender_client.sendall(error_msg.encode('utf-8'))
    
    def broadcast_group_message(self, sender_name, message, group_id, exclude_client=None):
        """向指定群组广播消息"""
        if group_id not in self.groups:
            return
        
        group = self.groups[group_id]
        group_name = group['name']
        group_msg = f"[{group_name}] {sender_name}: {message}"
        
        # 保存消息到JSON
        self.save_message_to_json(group_id, group_name, sender_name, message, "group")
        
        # 只向该群组的成员发送
        for client in group['members']:
            if client != exclude_client:
                try:
                    client.sendall(group_msg.encode('utf-8'))
                except:
                    pass
        
        # 服务器日志
        self.messages.configure(state='normal')
        self.messages.insert(tk.END, group_msg + '\n')
        self.messages.configure(state='disabled')
        self.messages.yview(tk.END)
    
    def create_group(self, creator_client, group_name):
        """创建小群"""
        creator_name = self.client_names.get(creator_client, "未知用户")
        group_id = f"group_{len(self.groups)}"
        invite_code = self.generate_invite_code()
        
        self.groups[group_id] = {
            'name': group_name,
            'members': {creator_client},
            'invite_code': invite_code,
            'creator': creator_name,
            'owner': creator_client,  # 创建者自动成为群主
            'admins': set()  # 管理员列表
        }
        
        # 初始化群组禁言列表
        self.group_muted_clients[group_id] = set()
        
        # 初始化消息历史
        self.message_history[group_id] = []
        
        # 创建者加入新群
        if creator_client not in self.client_groups:
            self.client_groups[creator_client] = set()
        self.client_groups[creator_client].add(group_id)
        self.client_current_group[creator_client] = group_id
        
        success_msg = f"/groupcreated {group_id} {group_name} {invite_code}"
        creator_client.sendall(success_msg.encode('utf-8'))
        
        # 通知所有客户端更新群组列表
        self.broadcast_group_list_update()
    
    def join_group_by_code(self, client, invite_code):
        """通过邀请码加入群组"""
        client_name = self.client_names.get(client, "未知用户")
        
        for group_id, group in self.groups.items():
            if group['invite_code'] == invite_code:
                if client not in group['members']:
                    group['members'].add(client)
                    if client not in self.client_groups:
                        self.client_groups[client] = set()
                    self.client_groups[client].add(group_id)
                    
                    # 初始化群组禁言列表（如果不存在）
                    if group_id not in self.group_muted_clients:
                        self.group_muted_clients[group_id] = set()
                    
                    success_msg = f"/joinedgroup {group_id} {group['name']}"
                    client.sendall(success_msg.encode('utf-8'))
                    
                    # 发送历史消息给新成员
                    self.send_message_history(client, group_id, "group")
                    
                    # 通知群组其他成员
                    join_notice = f"用户 {client_name} 加入了群组 {group['name']}。User {client_name} joined group {group['name']}."
                    for member in group['members']:
                        if member != client:
                            try:
                                member.sendall(join_notice.encode('utf-8'))
                            except:
                                pass
                    
                    self.broadcast_group_list_update()
                    return
        
        error_msg = f"邀请码无效。Invalid invite code."
        client.sendall(error_msg.encode('utf-8'))
    
    def invite_user_to_group(self, inviter_client, username):
        """拉人入群"""
        inviter_name = self.client_names.get(inviter_client, "未知用户")
        current_group_id = self.client_current_group.get(inviter_client)
        
        if not current_group_id or current_group_id == self.main_group_id:
            error_msg = "只能在小群中拉人。You can only invite users in small groups."
            inviter_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            inviter_client.sendall(error_msg.encode('utf-8'))
            return
        
        if current_group_id in self.groups:
            group = self.groups[current_group_id]
            if target_client not in group['members']:
                group['members'].add(target_client)
                if target_client not in self.client_groups:
                    self.client_groups[target_client] = set()
                self.client_groups[target_client].add(current_group_id)
                
                # 初始化群组禁言列表（如果不存在）
                if current_group_id not in self.group_muted_clients:
                    self.group_muted_clients[current_group_id] = set()
                
                success_msg = f"你被 {inviter_name} 拉入了群组 {group['name']}。You were invited to group {group['name']} by {inviter_name}."
                target_client.sendall(success_msg.encode('utf-8'))
                
                notice_msg = f"用户 {username} 被拉入了群组。User {username} was invited to the group."
                inviter_client.sendall(notice_msg.encode('utf-8'))
                
                # 通知群组其他成员
                join_notice = f"用户 {username} 加入了群组 {group['name']}。User {username} joined group {group['name']}."
                for member in group['members']:
                    if member != target_client:
                        try:
                            member.sendall(join_notice.encode('utf-8'))
                        except:
                            pass
                
                self.broadcast_group_list_update()
            else:
                error_msg = f"用户 {username} 已在群组中。User {username} is already in the group."
                inviter_client.sendall(error_msg.encode('utf-8'))
    
    def switch_group(self, client, group_id):
        """切换群组"""
        if client not in self.client_groups:
            error_msg = "你不在任何群组中。You are not in any group."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        if group_id in self.client_groups[client]:
            self.client_current_group[client] = group_id
            group_name = self.groups[group_id]['name']
            success_msg = f"/switchedgroup {group_id} {group_name}"
            client.sendall(success_msg.encode('utf-8'))
        else:
            error_msg = f"你不在群组 {group_id} 中。You are not in group {group_id}."
            client.sendall(error_msg.encode('utf-8'))
    
    def list_groups(self, client):
        """列出用户所在的所有群组"""
        if client not in self.client_groups:
            client.sendall("/groupslist ".encode('utf-8'))
            return
        
        groups_info = []
        for group_id in self.client_groups[client]:
            if group_id in self.groups:
                group = self.groups[group_id]
                is_current = (self.client_current_group.get(client) == group_id)
                groups_info.append(f"{group_id}|{group['name']}|{is_current}")
        
        groups_msg = "/groupslist " + " ".join(groups_info)
        client.sendall(groups_msg.encode('utf-8'))
    
    def send_group_info(self, client, group_id):
        """发送群组信息"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        group = self.groups[group_id]
        member_names = []
        for member_client in group['members']:
            if member_client in self.client_names:
                member_names.append(self.client_names[member_client])
        
        info_msg = f"/groupinfo {group_id} {group['name']} {group['invite_code'] or 'N/A'} {' '.join(member_names)}"
        client.sendall(info_msg.encode('utf-8'))
    
    def broadcast_group_list_update(self):
        """广播群组列表更新给所有客户端"""
        for client in self.clients:
            try:
                self.list_groups(client)
            except:
                pass
    
    def kick_from_group_server_side(self, group_id, username):
        """服务器端从群组踢出成员（不需要operator_client）"""
        if group_id not in self.groups:
            msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            messagebox.showerror("错误", msg)
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            msg = f"用户 {username} 不在线。User {username} is not online."
            messagebox.showerror("错误", msg)
            return
        
        group = self.groups[group_id]
        if target_client not in group['members']:
            msg = f"用户 {username} 不在群组中。User {username} is not in the group."
            messagebox.showerror("错误", msg)
            return
        
        # 权限检查：不能踢出群主、服主
        if group.get('owner') == target_client:
            msg = "不能踢出群主。Cannot kick group owner."
            messagebox.showerror("错误", msg)
            return
        
        if self.is_server_owner(target_client):
            msg = "不能踢出服主。Cannot kick server owner."
            messagebox.showerror("错误", msg)
            return
        
        # 执行踢出
        group['members'].discard(target_client)
        if target_client in self.client_groups:
            self.client_groups[target_client].discard(group_id)
        
        # 如果被踢出的是当前群组，切换到主群
        if self.client_current_group.get(target_client) == group_id:
            self.client_current_group[target_client] = self.main_group_id
        
        # 从群组禁言列表中移除
        if group_id in self.group_muted_clients:
            self.group_muted_clients[group_id].discard(target_client)
        
        kick_msg = f"你被服务器管理员从群组 {group['name']} 中踢出。You were kicked from group {group['name']} by server admin."
        target_client.sendall(kick_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 已被踢出群组。User {username} has been kicked from the group."
        messagebox.showinfo("成功", notice_msg)
        
        # 通知群组其他成员
        for member in group['members']:
            if member != target_client:
                try:
                    member.sendall(notice_msg.encode('utf-8'))
                except:
                    pass
        
        self.broadcast_group_list_update()
    
    def kick_from_group(self, operator_client, group_id, username):
        """从群组踢出成员"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 检查权限
        if operator_client and not self.has_group_manage_permission(operator_client, group_id):
            error_msg = "你没有权限执行此操作。You don't have permission to perform this action."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        group = self.groups[group_id]
        if target_client not in group['members']:
            error_msg = f"用户 {username} 不在群组中。User {username} is not in the group."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 权限检查：不能踢出群主、服主、管理员（除非操作者是服主或群主）
        if group.get('owner') == target_client:
            error_msg = "不能踢出群主。Cannot kick group owner."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        if self.is_server_owner(target_client):
            error_msg = "不能踢出服主。Cannot kick server owner."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 管理员不能踢出其他管理员（除非是服主或群主）
        if target_client in group.get('admins', set()):
            if not (self.is_group_owner(operator_client, group_id) or self.is_server_owner(operator_client)):
                error_msg = "管理员不能踢出其他管理员。Admins cannot kick other admins."
                operator_client.sendall(error_msg.encode('utf-8'))
                return
        
        # 执行踢出
        group['members'].discard(target_client)
        if target_client in self.client_groups:
            self.client_groups[target_client].discard(group_id)
        
        # 如果被踢出的是当前群组，切换到主群
        if self.client_current_group.get(target_client) == group_id:
            self.client_current_group[target_client] = self.main_group_id
        
        # 从群组禁言列表中移除
        if group_id in self.group_muted_clients:
            self.group_muted_clients[group_id].discard(target_client)
        
        operator_name = self.client_names.get(operator_client, "服务器管理员") if operator_client else "服务器管理员"
        kick_msg = f"你被 {operator_name} 从群组 {group['name']} 中踢出。You were kicked from group {group['name']} by {operator_name}."
        target_client.sendall(kick_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 已被踢出群组。User {username} has been kicked from the group."
        if operator_client:
            operator_client.sendall(notice_msg.encode('utf-8'))
        
        # 通知群组其他成员
        for member in group['members']:
            if member != operator_client:
                try:
                    member.sendall(notice_msg.encode('utf-8'))
                except:
                    pass
        
        self.broadcast_group_list_update()
    
    def mute_from_group(self, operator_client, group_id, username):
        """在群组内禁言成员"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 检查权限（服务器端操作跳过权限检查）
        if operator_client and not self.has_group_manage_permission(operator_client, group_id):
            error_msg = "你没有权限执行此操作。You don't have permission to perform this action."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        group = self.groups[group_id]
        if target_client not in group['members']:
            error_msg = f"用户 {username} 不在群组中。User {username} is not in the group."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 权限检查：不能禁言群主、服主、管理员（除非操作者是服主或群主）
        if group.get('owner') == target_client:
            error_msg = "不能禁言群主。Cannot mute group owner."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        if self.is_server_owner(target_client):
            error_msg = "不能禁言服主。Cannot mute server owner."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 管理员不能禁言其他管理员（除非是服主或群主，或服务器端操作）
        if target_client in group.get('admins', set()):
            if operator_client and not (self.is_group_owner(operator_client, group_id) or self.is_server_owner(operator_client)):
                error_msg = "管理员不能禁言其他管理员。Admins cannot mute other admins."
                operator_client.sendall(error_msg.encode('utf-8'))
                return
        
        # 执行禁言
        if group_id not in self.group_muted_clients:
            self.group_muted_clients[group_id] = set()
        self.group_muted_clients[group_id].add(target_client)
        
        operator_name = self.client_names.get(operator_client, "服务器管理员") if operator_client else "服务器管理员"
        mute_msg = f"你在群组 {group['name']} 中被 {operator_name} 禁言。You were muted in group {group['name']} by {operator_name}."
        target_client.sendall(mute_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 已在群组中被禁言。User {username} has been muted in the group."
        if operator_client:
            operator_client.sendall(notice_msg.encode('utf-8'))
        else:
            messagebox.showinfo("成功", notice_msg)
    
    def unmute_from_group(self, operator_client, group_id, username):
        """在群组内解禁成员"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 检查权限（服务器端操作跳过权限检查）
        if operator_client and not self.has_group_manage_permission(operator_client, group_id):
            error_msg = "你没有权限执行此操作。You don't have permission to perform this action."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 执行解禁
        if group_id in self.group_muted_clients:
            self.group_muted_clients[group_id].discard(target_client)
        
        group = self.groups[group_id]
        operator_name = self.client_names.get(operator_client, "服务器管理员") if operator_client else "服务器管理员"
        unmute_msg = f"你在群组 {group['name']} 中被 {operator_name} 解除禁言。You were unmuted in group {group['name']} by {operator_name}."
        target_client.sendall(unmute_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 已在群组中被解除禁言。User {username} has been unmuted in the group."
        if operator_client:
            operator_client.sendall(notice_msg.encode('utf-8'))
        else:
            messagebox.showinfo("成功", notice_msg)
    
    def set_group_admin(self, operator_client, group_id, username):
        """设置群管理员"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 只有群主或服主可以设置管理员（服务器端操作跳过权限检查）
        if operator_client and not (self.is_group_owner(operator_client, group_id) or self.is_server_owner(operator_client)):
            error_msg = "只有群主可以设置管理员。Only group owner can set admins."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        group = self.groups[group_id]
        if target_client not in group['members']:
            error_msg = f"用户 {username} 不在群组中。User {username} is not in the group."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 不能设置群主为管理员
        if group.get('owner') == target_client:
            error_msg = "群主已经是管理员。Group owner is already an admin."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 执行设置
        group['admins'].add(target_client)
        
        operator_name = self.client_names.get(operator_client, "服务器管理员") if operator_client else "服务器管理员"
        admin_msg = f"你被 {operator_name} 设置为群组 {group['name']} 的管理员。You were set as admin of group {group['name']} by {operator_name}."
        target_client.sendall(admin_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 已被设置为群组管理员。User {username} has been set as group admin."
        if operator_client:
            operator_client.sendall(notice_msg.encode('utf-8'))
        else:
            messagebox.showinfo("成功", notice_msg)
    
    def remove_group_admin(self, operator_client, group_id, username):
        """移除群管理员"""
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 只有群主或服主可以移除管理员（服务器端操作跳过权限检查）
        if operator_client and not (self.is_group_owner(operator_client, group_id) or self.is_server_owner(operator_client)):
            error_msg = "只有群主可以移除管理员。Only group owner can remove admins."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        # 查找目标用户
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if not target_client:
            error_msg = f"用户 {username} 不在线。User {username} is not online."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        group = self.groups[group_id]
        if target_client not in group['admins']:
            error_msg = f"用户 {username} 不是管理员。User {username} is not an admin."
            if operator_client:
                operator_client.sendall(error_msg.encode('utf-8'))
            else:
                messagebox.showerror("错误", error_msg)
            return
        
        # 执行移除
        group['admins'].discard(target_client)
        
        operator_name = self.client_names.get(operator_client, "服务器管理员") if operator_client else "服务器管理员"
        remove_msg = f"你被 {operator_name} 移除了群组 {group['name']} 的管理员权限。You were removed as admin of group {group['name']} by {operator_name}."
        target_client.sendall(remove_msg.encode('utf-8'))
        
        notice_msg = f"用户 {username} 的管理员权限已被移除。User {username}'s admin permission has been removed."
        if operator_client:
            operator_client.sendall(notice_msg.encode('utf-8'))
        else:
            messagebox.showinfo("成功", notice_msg)
    
    def delete_group(self, operator_client, group_id):
        """删除群组（仅服主）"""
        if not self.is_server_owner(operator_client):
            error_msg = "只有服主可以删除群组。Only server owner can delete groups."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        if group_id == self.main_group_id:
            error_msg = "不能删除主群。Cannot delete main group."
            operator_client.sendall(error_msg.encode('utf-8'))
            return
        
        group = self.groups[group_id]
        group_name = group['name']
        
        # 通知所有成员
        delete_msg = f"群组 {group_name} 已被服主删除。Group {group_name} has been deleted by server owner."
        for member in group['members']:
            try:
                member.sendall(delete_msg.encode('utf-8'))
                # 将被删除群组的成员切换到主群
                if self.client_current_group.get(member) == group_id:
                    self.client_current_group[member] = self.main_group_id
            except:
                pass
        
        # 从所有成员的群组列表中移除
        for member in group['members']:
            if member in self.client_groups:
                self.client_groups[member].discard(group_id)
        
        # 删除群组
        del self.groups[group_id]
        if group_id in self.group_muted_clients:
            del self.group_muted_clients[group_id]
        
        success_msg = f"群组 {group_name} 已删除。Group {group_name} has been deleted."
        operator_client.sendall(success_msg.encode('utf-8'))
        
        # 服务器日志
        self.messages.configure(state='normal')
        self.messages.insert(tk.END, f"[系统] {success_msg}\n")
        self.messages.configure(state='disabled')
        self.messages.yview(tk.END)
        
        # 更新所有客户端的群组列表
        self.broadcast_group_list_update()
    
    def set_server_owner(self):
        """设置服主（服务器界面按钮）"""
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if not selected_user:
            msg = "请先选择用户。" if self.language == "zh" else "Please select a user first."
            messagebox.showwarning("提示" if self.language == "zh" else "Warning", msg)
            return
        
        # 提取用户名（去掉IP部分）
        if '(' in selected_user:
            username = selected_user.split('(')[0].strip()
        else:
            username = selected_user
        
        # 查找客户端
        target_client = None
        for client, name in self.client_names.items():
            if name == username:
                target_client = client
                break
        
        if target_client:
            self.server_owner = target_client
            self.server_owner_name = username
            msg = f"已设置 {username} 为服主。" if self.language == "zh" else f"{username} has been set as server owner."
            messagebox.showinfo("成功" if self.language == "zh" else "Success", msg)
            
            # 通知服主
            owner_msg = "你已被设置为服主。You have been set as server owner."
            try:
                target_client.sendall(owner_msg.encode('utf-8'))
            except:
                pass
        else:
            msg = "用户不在线。" if self.language == "zh" else "User is not online."
            messagebox.showerror("错误" if self.language == "zh" else "Error", msg)
    
    def send_permission_info(self, client, group_id):
        """发送权限信息给客户端"""
        is_owner = self.is_group_owner(client, group_id)
        is_admin = self.is_group_admin(client, group_id)
        is_server_owner = self.is_server_owner(client)
        
        perm_msg = f"/permissioninfo {is_owner} {is_admin} {is_server_owner}"
        client.sendall(perm_msg.encode('utf-8'))
    
    def leave_group(self, client, group_id):
        """退出群聊"""
        if group_id == self.main_group_id:
            error_msg = "不能退出主群。Cannot leave main group."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        if group_id not in self.groups:
            error_msg = f"群组 {group_id} 不存在。Group {group_id} does not exist."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        if client not in self.client_groups or group_id not in self.client_groups[client]:
            error_msg = "你不在该群组中。You are not in this group."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        group = self.groups[group_id]
        client_name = self.client_names.get(client, "未知用户")
        
        # 从群组中移除
        group['members'].discard(client)
        self.client_groups[client].discard(group_id)
        
        # 如果是当前群组，切换到主群
        if self.client_current_group.get(client) == group_id:
            self.client_current_group[client] = self.main_group_id
        
        # 从群组禁言列表中移除
        if group_id in self.group_muted_clients:
            self.group_muted_clients[group_id].discard(client)
        
        # 从管理员列表中移除（如果是管理员）
        if client in group.get('admins', set()):
            group['admins'].discard(client)
        
        success_msg = f"/leftgroup {group_id}"
        client.sendall(success_msg.encode('utf-8'))
        
        # 通知群组其他成员
        leave_notice = f"用户 {client_name} 退出了群组 {group['name']}。User {client_name} left group {group['name']}."
        for member in group['members']:
            try:
                member.sendall(leave_notice.encode('utf-8'))
            except:
                pass
        
        self.broadcast_group_list_update()
    
    def send_file_list_to_client(self, client, group_id):
        """发送文件列表给客户端"""
        group_files = []
        for file_id, file_info in self.stored_files.items():
            if file_info['group_id'] == group_id:
                group_files.append(f"{file_info['filename']}|{file_info['sender']}|{file_info['timestamp']}")
        
        if group_files:
            file_list_msg = f"/filelist {group_id} {' '.join(group_files)}"
            try:
                client.sendall(file_list_msg.encode('utf-8'))
            except:
                pass
    
    def send_message_history(self, client, chat_key, chat_type):
        """发送消息历史给客户端"""
        messages = self.load_message_history(chat_key)
        
        if chat_type == "group":
            # 群聊历史
            if chat_key in self.groups:
                group_name = self.groups[chat_key]['name']
                for msg in messages:
                    history_msg = f"[{group_name}] {msg['sender']}: {msg['message']}"
                    try:
                        client.sendall(history_msg.encode('utf-8'))
                    except:
                        pass
        else:
            # 私聊历史
            for msg in messages:
                # 私聊消息格式需要根据发送者和接收者确定
                history_msg = f"[私聊] {msg['sender']}: {msg['message']}"
                try:
                    client.sendall(history_msg.encode('utf-8'))
                except:
                    pass
    
    def send_file_to_client(self, client, group_id, filename):
        """发送文件给客户端"""
        # 查找文件
        file_info = None
        for file_id, info in self.stored_files.items():
            if info['group_id'] == group_id and info['filename'] == filename:
                file_info = info
                break
        
        if not file_info:
            error_msg = f"文件 {filename} 不存在。File {filename} does not exist."
            client.sendall(error_msg.encode('utf-8'))
            return
        
        try:
            with open(file_info['path'], 'rb') as f:
                file_data = base64.b64encode(f.read()).decode('utf-8')
            
            json_msg = {
                'type': 'file_download',
                'filename': file_info['filename'],
                'data': file_data,
                'sender': file_info['sender'],
                'group_id': group_id
            }
            
            client.sendall(json.dumps(json_msg).encode('utf-8'))
        except Exception as e:
            error_msg = f"读取文件失败: {e}"
            client.sendall(error_msg.encode('utf-8'))
    
    def delete_group_dialog(self):
        """删除群组对话框（服务器界面）"""
        if not self.server_owner:
            messagebox.showwarning("提示", "请先设置服主。Please set server owner first.")
            return
        
        group_id = simpledialog.askstring("删除群组 Delete Group", "请输入要删除的群组ID Enter group ID:")
        if group_id and group_id.strip():
            if group_id in self.groups:
                group_name = self.groups[group_id]['name']
                if messagebox.askyesno("确认", f"确定要删除群组 {group_name} 吗？\nAre you sure you want to delete group {group_name}?"):
                    self.delete_group(self.server_owner, group_id)
            else:
                messagebox.showerror("错误", "群组不存在。Group does not exist.")
    
    def broadcast_file_list_update(self, group_id):
        """广播文件列表更新给群组成员"""
        if group_id not in self.groups:
            return
        
        # 获取该群组的所有文件
        group_files = []
        for file_id, file_info in self.stored_files.items():
            if file_info['group_id'] == group_id:
                group_files.append(f"{file_info['filename']}|{file_info['sender']}|{file_info['timestamp']}")
        
        # 发送文件列表更新消息给群组所有成员
        if group_files:
            file_list_msg = f"/filelist {group_id} {' '.join(group_files)}"
            group = self.groups[group_id]
            for client in group['members']:
                try:
                    client.sendall(file_list_msg.encode('utf-8'))
                except:
                    pass

    def handle_file_transfer(self, sender_client, json_msg):
        """处理文件传输"""
        sender_name = self.client_names.get(sender_client, "未知用户")
        target_user = json_msg.get('target')
        file_name = json_msg.get('filename')
        file_data = json_msg.get('data')
        group_id = json_msg.get('group_id')
        
        # 保存文件到服务器（仅群聊文件）
        if not target_user and group_id:
            if group_id in self.groups:
                group_name = self.groups[group_id]['name']
                # 获取文件存储路径
                folder_path, json_file, files_folder = self.get_chat_storage_path(group_id, group_name, "group")
                
                # 生成文件名：名字+发送时间戳
                file_base = os.path.splitext(file_name)[0]
                file_ext = os.path.splitext(file_name)[1]
                timestamp = int(time.time())
                saved_filename = f"{file_base}_{timestamp}{file_ext}"
                file_path = os.path.join(files_folder, saved_filename)
                
                try:
                    file_bytes = base64.b64decode(file_data)
                    with open(file_path, 'wb') as f:
                        f.write(file_bytes)
                    
                    file_id = f"{group_id}_{timestamp}_{file_name}"
                    self.stored_files[file_id] = {
                        'filename': file_name,
                        'saved_filename': saved_filename,
                        'path': file_path,
                        'sender': sender_name,
                        'group_id': group_id,
                        'timestamp': timestamp
                    }
                    # 广播文件列表更新
                    self.broadcast_file_list_update(group_id)
                    # 刷新服务器端文件列表
                    self.refresh_file_list()
                except Exception as e:
                    print(f"保存文件失败: {e}")
    
    def handle_image_transfer(self, sender_client, json_msg):
        """处理图片传输"""
        sender_name = self.client_names.get(sender_client, "未知用户")
        target_user = json_msg.get('target')
        file_name = json_msg.get('filename')
        group_id = json_msg.get('group_id')  # 如果指定了群组
        
        if target_user:
            # 私聊图片
            target_client = None
            for client, name in self.client_names.items():
                if name == target_user:
                    target_client = client
                    break
            
            if target_client:
                json_msg['sender'] = sender_name
                json_msg['type'] = 'image_private'
                target_client.sendall(json.dumps(json_msg).encode('utf-8'))
                sender_client.sendall(json.dumps(json_msg).encode('utf-8'))
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, f"[私聊图片] {sender_name} -> {target_user}: {file_name}\n")
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)
        elif group_id and group_id in self.groups:
            # 发送到指定群组
            json_msg['sender'] = sender_name
            json_msg['type'] = 'image_group'
            group = self.groups[group_id]
            for client in group['members']:
                if client != sender_client:
                    try:
                        client.sendall(json.dumps(json_msg).encode('utf-8'))
                    except:
                        pass
            self.messages.configure(state='normal')
            self.messages.insert(tk.END, f"[{group['name']}图片] {sender_name}: {file_name}\n")
            self.messages.configure(state='disabled')
            self.messages.yview(tk.END)
        else:
            # 发送到当前群组
            current_group_id = self.client_current_group.get(sender_client, self.main_group_id)
            json_msg['sender'] = sender_name
            json_msg['type'] = 'image_group'
            if current_group_id in self.groups:
                group = self.groups[current_group_id]
                for client in group['members']:
                    if client != sender_client:
                        try:
                            client.sendall(json.dumps(json_msg).encode('utf-8'))
                        except:
                            pass
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, f"[{group['name']}图片] {sender_name}: {file_name}\n")
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)

    def kick_user(self, username):
        for client, name in self.client_names.items():
            if name == username:
                client.sendall(f"{username} 已被踢出聊天室。{username} ,you have been kicked out of the chat room.".encode('utf-8'))
                self.clients.remove(client)
                del self.client_names[client]
                self.update_client_list()
                break

    def mute_user(self, username):
        for client, name in self.client_names.items():
            if name == username:
                self.muted_clients.add(client)
                client.sendall(f"用户 {username} 被禁言。{username} ,you have been banned.".encode('utf-8'))
                break

    def unmute_user(self, username):
        for client, name in self.client_names.items():
            if name == username:
                self.muted_clients.discard(client)
                client.sendall(f"用户 {username} 被解除禁言。{username} ,you have been unbanned.".encode('utf-8'))
                break

    def kick_selected_user(self):
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if selected_user:
            # 提取用户名（去掉IP部分）
            if '(' in selected_user:
                username = selected_user.split('(')[0].strip()
            else:
                username = selected_user
            self.kick_user(username)

    def mute_selected_user(self):
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if selected_user:
            # 提取用户名（去掉IP部分）
            if '(' in selected_user:
                username = selected_user.split('(')[0].strip()
            else:
                username = selected_user
            self.mute_user(username)

    def unmute_selected_user(self):
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if selected_user:
            # 提取用户名（去掉IP部分）
            if '(' in selected_user:
                username = selected_user.split('(')[0].strip()
            else:
                username = selected_user
            self.unmute_user(username)

    def update_client_list(self):
        self.client_listbox.delete(0, tk.END)
        for client, name in self.client_names.items():
            # 显示用户名和IP地址
            if client in self.client_addresses:
                ip = self.client_addresses[client][0]
                display_text = f"{name} ({ip})"
            else:
                display_text = name
            self.client_listbox.insert(tk.END, display_text)
        # 向所有客户端发送更新后的用户列表
        client_list_message = "/name " + " ".join(self.client_names.values())
        for client in self.clients:
            client.sendall(client_list_message.encode('utf-8'))

    def update_group_list(self):
        self.group_listbox.delete(0, tk.END)
        for group_id, group_info in self.groups.items():
            # 服务器端只显示群组ID和名称，不显示"当前"状态
            self.group_listbox.insert(tk.END, f"{group_id}|{group_info['name']}")
        # 注意：服务器端不发送群组列表给客户端，客户端通过/listgroups命令获取

    def view_selected_group_info(self):
        selected_item = self.group_listbox.get(tk.ACTIVE)
        if not selected_item:
            messagebox.showwarning("提示", "请先选择一个群组。Please select a group first.")
            return
        
        parts = selected_item.split('|')
        group_id = parts[0]
        group_name = parts[1] if len(parts) > 1 else group_id
        
        if group_id in self.groups:
            group_info = self.groups[group_id]
            members_list = []
            for member_client in group_info['members']:
                if member_client in self.client_names:
                    members_list.append(self.client_names[member_client])
            
            owner_name = 'N/A'
            if group_info.get('owner') and group_info['owner'] in self.client_names:
                owner_name = self.client_names[group_info['owner']]
            
            admin_names = []
            for admin in group_info.get('admins', set()):
                if admin in self.client_names:
                    admin_names.append(self.client_names[admin])
            
            info_msg = f"群组信息 Group Info:\n群组ID: {group_id}\n群组名称: {group_name}\n邀请码: {group_info['invite_code'] or 'N/A'}\n创建者: {group_info['creator'] or 'N/A'}\n群主: {owner_name}\n管理员: {', '.join(admin_names) if admin_names else 'N/A'}\n成员: {', '.join(members_list) if members_list else '无'}"
            
            messagebox.showinfo("群组信息", info_msg)
        else:
            messagebox.showerror("错误", "群组不存在。Group does not exist.")

    def manage_selected_group(self):
        selected_item = self.group_listbox.get(tk.ACTIVE)
        if not selected_item:
            messagebox.showwarning("提示", "请先选择一个群组。Please select a group first.")
            return
        
        parts = selected_item.split('|')
        group_id = parts[0]
        group_name = parts[1] if len(parts) > 1 else group_id
        
        if group_id in self.groups:
            manage_window = tk.Toplevel(self.master)
            manage_window.title(f"管理群组: {group_name}")
            manage_window.geometry("400x300")
            
            # 定义字体（根据操作系统选择）
            if IS_WINDOWS:
                font_family = "Microsoft YaHei"
            elif IS_MACOS:
                font_family = "PingFang SC"
            else:
                font_family = "DejaVu Sans"
            font = tkFont.Font(family=font_family, size=12)
            
            # 添加踢出成员按钮
            tk.Button(manage_window, text="踢出成员 Kick Member", command=lambda: self.kick_from_group_dialog(group_id, group_name), font=font).pack(pady=5)
            # 添加禁言成员按钮
            tk.Button(manage_window, text="禁言成员 Mute Member", command=lambda: self.mute_from_group_dialog(group_id, group_name), font=font).pack(pady=5)
            # 添加解除禁言成员按钮
            tk.Button(manage_window, text="解除禁言成员 Unmute Member", command=lambda: self.unmute_from_group_dialog(group_id, group_name), font=font).pack(pady=5)
            # 添加设置管理员按钮
            tk.Button(manage_window, text="设置管理员 Set Admin", command=lambda: self.set_group_admin_dialog(group_id, group_name), font=font).pack(pady=5)
            # 添加移除管理员按钮
            tk.Button(manage_window, text="移除管理员 Remove Admin", command=lambda: self.remove_group_admin_dialog(group_id, group_name), font=font).pack(pady=5)
            # 添加退出按钮
            tk.Button(manage_window, text="退出", command=manage_window.destroy, font=font).pack(pady=5)
        else:
            messagebox.showerror("错误", "群组不存在。Group does not exist.")

    def kick_from_group_dialog(self, group_id, group_name):
        username = simpledialog.askstring("踢出成员 Kick Member", f"请输入要踢出的用户名 (在群组 {group_name} 中):")
        if username:
            # 服务器端操作，使用None作为operator_client
            self.kick_from_group_server_side(group_id, username)

    def mute_from_group_dialog(self, group_id, group_name):
        username = simpledialog.askstring("禁言成员 Mute Member", f"请输入要禁言的用户名 (在群组 {group_name} 中):")
        if username:
            # 服务器端操作，直接调用方法但传递None作为operator_client
            self.mute_from_group(None, group_id, username)

    def unmute_from_group_dialog(self, group_id, group_name):
        username = simpledialog.askstring("解除禁言成员 Unmute Member", f"请输入要解除禁言的用户名 (在群组 {group_name} 中):")
        if username:
            # 服务器端操作，直接调用方法但传递None作为operator_client
            self.unmute_from_group(None, group_id, username)

    def set_group_admin_dialog(self, group_id, group_name):
        username = simpledialog.askstring("设置管理员 Set Admin", f"请输入要设置为管理员的用户名 (在群组 {group_name} 中):")
        if username:
            # 服务器端操作，直接调用方法但传递None作为operator_client
            self.set_group_admin(None, group_id, username)

    def remove_group_admin_dialog(self, group_id, group_name):
        username = simpledialog.askstring("移除管理员 Remove Admin", f"请输入要移除管理员权限的用户名 (在群组 {group_name} 中):")
        if username:
            # 服务器端操作，直接调用方法但传递None作为operator_client
            self.remove_group_admin(None, group_id, username)

    def on_close(self):
        # 停止热键监听
        if self.hotkey_enabled:
            self.stop_hotkey_listener()
        
        if self.sock:
            for client in self.clients[:]:  # 使用副本遍历
                try:
                    client.sendall(b"/quit")
                    client.close()
                except:
                    pass
            self.sock.close()
        os._exit(0)


class ChatClient:
    def __init__(self, master):
        self.master = master
        self.master.title("聊天客户端 Chat Client")
        # 透明度设置
        self.window_alpha = 1.0  # 默认100%不透明
        self.language = "zh"  # "zh" 或 "en"
        self.theme = "light"  # "light" 或 "dark"
        self.translations = self.load_translations()
        # ==== 本地缓存相关 ====
        self.local_storage_path = os.path.join(os.getcwd(), "local_cache")
        if not os.path.exists(self.local_storage_path):
            os.makedirs(self.local_storage_path)
        self.offline_mode = False
        self.pending_messages = []
        # ==== 本地缓存end ====
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
        self.was_kill_triggered = True  # 初始与C++逻辑一致

    def setup_widgets(self):
        if IS_WINDOWS:
            font_family = "Microsoft YaHei UI"
        elif IS_MACOS:
            font_family = "PingFang SC"
        else:
            font_family = "DejaVu Sans"
        font = tkFont.Font(family=font_family, size=10)
        title_font = tkFont.Font(family=font_family, size=11, weight='bold')
        
        # 配置ttk样式
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Action.TButton', font=font, padding=6)
        style.configure('Title.TLabel', font=title_font)
        
        self.master.geometry("1200x700")
        self.master.minsize(1000, 600)
        
        # 消息显示区域 - 使用ttk LabelFrame
        msg_frame = ttk.LabelFrame(self.master, text="聊天消息 Chat Messages", padding=10)
        msg_frame.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
        
        msg_scroll_frame = tk.Frame(msg_frame)
        msg_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.messages = tk.Text(msg_scroll_frame, state='disabled', font=font, wrap=tk.WORD,
                               bg='#ffffff', fg='#333333', relief=tk.SUNKEN, borderwidth=1,
                               selectbackground='#4A90E2', selectforeground='white')
        self.messages.tag_config("link", foreground="#0066cc", underline=True)
        self.messages.tag_bind("link", "<Button-1>", self.open_link)
        self.messages.tag_bind("link", "<Enter>", lambda e: self.messages.config(cursor="hand2"))
        self.messages.tag_bind("link", "<Leave>", lambda e: self.messages.config(cursor=""))
        self.image_references = []
        
        scrollbar_msg = ttk.Scrollbar(msg_scroll_frame, orient=tk.VERTICAL, command=self.messages.yview)
        self.messages.config(yscrollcommand=scrollbar_msg.set)
        self.messages.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_msg.pack(side=tk.RIGHT, fill=tk.Y)

        # 群组列表 - 使用ttk LabelFrame
        group_frame = ttk.LabelFrame(self.master, text="群组列表 Groups", padding=10)
        group_frame.grid(row=0, column=3, sticky="nsew", padx=5, pady=5)
        
        group_scroll_frame = tk.Frame(group_frame)
        group_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.group_listbox = tk.Listbox(group_scroll_frame, font=font, height=5,
                                        bg='#ffffff', fg='#333333', relief=tk.SUNKEN, borderwidth=1,
                                        selectbackground='#4A90E2', selectforeground='white')
        self.group_listbox.grid(row=0, column=0, sticky="nsew")
        self.group_listbox.bind("<Double-Button-1>", self.switch_group_from_list)
        self.group_listbox.bind("<Button-3>", self.show_group_context_menu)  # 右键菜单
        
        group_scrollbar = ttk.Scrollbar(group_scroll_frame, orient=tk.VERTICAL, command=self.group_listbox.yview)
        self.group_listbox.config(yscrollcommand=group_scrollbar.set)
        group_scrollbar.grid(row=0, column=1, sticky="ns")
        group_scroll_frame.grid_columnconfigure(0, weight=1)

        # 用户列表 - 使用ttk LabelFrame
        user_frame = ttk.LabelFrame(self.master, text="在线用户 Online Users", padding=10)
        user_frame.grid(row=1, column=3, rowspan=3, sticky="nsew", padx=5, pady=5)
        
        user_scroll_frame = tk.Frame(user_frame)
        user_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.client_listbox = tk.Listbox(user_scroll_frame, font=font,
                                        bg='#ffffff', fg='#333333', relief=tk.SUNKEN, borderwidth=1,
                                        selectbackground='#4A90E2', selectforeground='white')
        self.client_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.client_listbox.bind("<Double-Button-1>", self.start_private_chat)
        
        user_scrollbar = ttk.Scrollbar(user_scroll_frame, orient=tk.VERTICAL, command=self.client_listbox.yview)
        self.client_listbox.config(yscrollcommand=user_scrollbar.set)
        user_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 模式和控制按钮框架
        mode_frame = ttk.Frame(self.master)
        mode_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.mode_label = ttk.Label(mode_frame, text="当前: 主群", font=font, style='Title.TLabel')
        self.mode_label.pack(side=tk.LEFT, padx=5)
        
        self.mode_button = ttk.Button(mode_frame, text="切换私聊", command=self.toggle_chat_mode, style='Action.TButton')
        self.mode_button.pack(side=tk.LEFT, padx=2)
        
        self.create_group_button = ttk.Button(mode_frame, text="创建小群", command=self.create_group_dialog, style='Action.TButton')
        self.create_group_button.pack(side=tk.LEFT, padx=2)
        
        self.join_group_button = ttk.Button(mode_frame, text="加入群", command=self.join_group_dialog, style='Action.TButton')
        self.join_group_button.pack(side=tk.LEFT, padx=2)
        
        self.invite_button = ttk.Button(mode_frame, text="拉人", command=self.invite_user_dialog, style='Action.TButton')
        self.invite_button.pack(side=tk.LEFT, padx=2)
        
        # 管理按钮框架
        self.manage_frame = ttk.Frame(self.master)
        self.manage_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.kick_member_button = ttk.Button(self.manage_frame, text="踢出成员", command=self.kick_member_dialog, 
                                           style='Action.TButton', state='disabled')
        self.kick_member_button.pack(side=tk.LEFT, padx=2)
        
        self.mute_member_button = ttk.Button(self.manage_frame, text="禁言成员", command=self.mute_member_dialog, 
                                           style='Action.TButton', state='disabled')
        self.mute_member_button.pack(side=tk.LEFT, padx=2)
        
        self.unmute_member_button = ttk.Button(self.manage_frame, text="解禁成员", command=self.unmute_member_dialog, 
                                             style='Action.TButton', state='disabled')
        self.unmute_member_button.pack(side=tk.LEFT, padx=2)
        
        self.set_admin_button = ttk.Button(self.manage_frame, text="设置管理员", command=self.set_admin_dialog, 
                                         style='Action.TButton', state='disabled')
        self.set_admin_button.pack(side=tk.LEFT, padx=2)
        
        self.remove_admin_button = ttk.Button(self.manage_frame, text="移除管理员", command=self.remove_admin_dialog, 
                                            style='Action.TButton', state='disabled')
        self.remove_admin_button.pack(side=tk.LEFT, padx=2)
        
        # 设置框架
        settings_frame = ttk.Frame(self.master)
        settings_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.language_button = ttk.Button(settings_frame, text="中文/EN", command=self.toggle_language, style='Action.TButton')
        self.language_button.pack(side=tk.LEFT, padx=2)
        
        self.theme_button = ttk.Button(settings_frame, text="主题 Theme", command=self.toggle_theme, style='Action.TButton')
        self.theme_button.pack(side=tk.LEFT, padx=2)
        
        alpha_label = ttk.Label(settings_frame, text="透明度:", font=font)
        alpha_label.pack(side=tk.LEFT, padx=2)
        self.alpha_var = tk.StringVar(value="100%")
        self.alpha_menu = ttk.OptionMenu(settings_frame, self.alpha_var, "100%",
                                         *[f"{i}%" for i in range(10, 101, 10)],
                                         command=self.on_alpha_change)
        self.alpha_menu.pack(side=tk.LEFT, padx=2)

        self.hotkey_button = ttk.Button(settings_frame, text="热键", command=self.toggle_hotkeys, style='Action.TButton')
        self.hotkey_button.pack(side=tk.LEFT, padx=2)
        
        self.leave_group_button = ttk.Button(settings_frame, text="退出群聊", command=self.leave_group_dialog, style='Action.TButton')
        self.leave_group_button.pack(side=tk.LEFT, padx=2)
        
        # 文件列表 - 使用ttk LabelFrame
        file_frame = ttk.LabelFrame(self.master, text="文件列表 Files", padding=10)
        file_frame.grid(row=0, column=4, rowspan=4, sticky="nsew", padx=5, pady=5)
        
        file_scroll_frame = tk.Frame(file_frame)
        file_scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        self.file_listbox = tk.Listbox(file_scroll_frame, font=font, height=15,
                                      bg='#ffffff', fg='#333333', relief=tk.SUNKEN, borderwidth=1,
                                      selectbackground='#4A90E2', selectforeground='white')
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_listbox.bind("<Double-Button-1>", self.download_file_from_list)
        
        file_scrollbar = ttk.Scrollbar(file_scroll_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        self.file_listbox.config(yscrollcommand=file_scrollbar.set)
        file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 输入框和按钮
        input_frame = ttk.Frame(self.master)
        input_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.input_user = ttk.Entry(input_frame, font=font)
        self.input_user.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        button_frame = ttk.Frame(self.master)
        button_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        
        self.send_button = ttk.Button(button_frame, text="发送", command=self.send_message, style='Action.TButton')
        self.send_button.pack(side=tk.LEFT, padx=2)
        
        self.file_button = ttk.Button(button_frame, text="文件", command=self.send_file, style='Action.TButton')
        self.file_button.pack(side=tk.LEFT, padx=2)
        
        self.emoji_button = ttk.Button(button_frame, text="表情", command=self.show_emoji_picker, style='Action.TButton')
        self.emoji_button.pack(side=tk.LEFT, padx=2)
        
        self.master.grid_columnconfigure(0, weight=2)
        self.master.grid_columnconfigure(1, weight=1)
        self.master.grid_columnconfigure(2, weight=1)
        self.master.grid_columnconfigure(3, weight=1)
        self.master.grid_columnconfigure(4, weight=1)
        self.master.grid_rowconfigure(0, weight=3)
        self.master.grid_rowconfigure(1, weight=0)
        self.master.grid_rowconfigure(2, weight=0)
        self.master.grid_rowconfigure(3, weight=0)
        self.master.grid_rowconfigure(4, weight=0)
        self.master.grid_rowconfigure(5, weight=0)
        
        self.master.bind("<Return>", self.send_message)
        self.master.bind("<Map>", self.on_window_focus)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_theme()
        self.set_window_alpha(self.window_alpha)

    def show_group_context_menu(self, event):
        """显示群组上下文菜单"""
        index = self.group_listbox.nearest(event.y)
        if index < 0:
            return

        group_info = self.group_listbox.get(index)
        parts = group_info.split('|')
        if len(parts) < 2:
            return
        group_id = parts[0]
        group_name = parts[1]

        context_menu = tk.Menu(self.master, tearoff=0)
        if (self.is_group_owner or self.is_group_admin or self.is_server_owner) and group_id != "main":
            context_menu.add_command(label="管理群组", 
                                    command=lambda: self.manage_group(group_id, group_name))

        context_menu.add_command(label="查看群信息", 
                                command=lambda: self.view_group_info(group_id))

        try:
            context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            context_menu.grab_release()

    def manage_group(self, group_id, group_name):
        """管理群组"""
        self.sock.sendall(f"/checkpermission {group_id}".encode('utf-8'))
        manage_msg = f"/managegroup {group_id}"
        self.sock.sendall(manage_msg.encode('utf-8'))

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

    # ==== 本地缓存相关 ====
    def save_message_to_local(self, chat_key, chat_name, sender, message, msg_type="group"):
        """保存消息到本地缓存"""
        timestamp = time.time()
        msg_data = {
            'sender': sender,
            'message': message,
            'timestamp': timestamp,
            'datetime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
        }
        # 本地缓存文件路径
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-', ' '))
        safe_name = safe_name.replace(' ', '_')
        cache_file = os.path.join(self.local_storage_path, f"{safe_name}.json")

        # 读取或创建缓存文件
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                data = {
                    'chat_name': chat_name,
                    'chat_key': chat_key,
                    'type': msg_type,
                    'messages': []
                }
        else:
            data = {
                'chat_name': chat_name,
                'chat_key': chat_key,
                'type': msg_type,
                'messages': []
            }
        data['messages'].append(msg_data)
        # 保存到本地
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存消息到本地缓存失败: {e}")

    def load_local_messages(self, chat_key, chat_name):
        """从本地缓存加载消息"""
        safe_name = "".join(c for c in chat_name if c.isalnum() or c in ('_', '-', ' '))
        safe_name = safe_name.replace(' ', '_')
        cache_file = os.path.join(self.local_storage_path, f"{safe_name}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('messages', [])
            except:
                pass
        return []
    # ==== 本地缓存end ====

    def receive_message(self, username):
        buffer = b''
        self.offline_mode = False  # 连接成功，退出离线模式

        # 发送待处理消息
        for msg in self.pending_messages[:]:
            try:
                self.sock.sendall(msg.encode('utf-8'))
                self.pending_messages.remove(msg)
            except:
                continue

        while True:
            try:
                data = self.sock.recv(8192)
                if not data:
                    # 连接断开，进入离线模式
                    self.offline_mode = True
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, "\n[系统] 连接断开，进入离线模式。重新连接后将同步消息。\n")
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
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
                    valid_groups = []
                    if groups_str:
                        candidates = groups_str.strip().split()
                        for group_info in candidates:
                            if "|" in group_info and not group_info.lower().startswith("permissioninfo"):
                                valid_groups.append(group_info)
                        self.my_groups = {}
                        for group_info in valid_groups:
                            parts = group_info.split('|')
                            if len(parts) >= 2:
                                group_id = parts[0]
                                group_name = parts[1]
                                is_current = False
                                if len(parts) >= 3 and parts[2] == 'True':
                                    is_current = True
                                self.my_groups[group_id] = group_name
                                if is_current:
                                    self.current_group_id = group_id
                                    self.current_group_name = group_name
                        if not self.my_groups:
                            self.my_groups = {"main": "主群 Main Group"}
                            self.current_group_id = "main"
                            self.current_group_name = "主群 Main Group"
                        else:
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
                    # ==== 本地缓存: 保存消息 ====
                    if self.current_group_id:
                        sender_name = "其他用户"
                        raw = message
                        if ": " in raw:
                            sender_name = raw.split(": ", 1)[0]
                        self.save_message_to_local(
                            self.current_group_id, 
                            self.current_group_name, 
                            sender_name, 
                            message, 
                            "group"
                        )
                    # ==== 本地缓存end ====

                    self.messages.configure(state='normal')
                    self.insert_message_with_links(message)
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
                    if not self.master.focus_get():
                        self.flash_window()
            except socket.error as e:
                print(f"接收错误 Receive error: {e}")
                self.offline_mode = True
                break

    def send_message(self, event=None):
        message = self.input_user.get()
        if not message:
            return
        
        if self.offline_mode:
            self.pending_messages.append(message)
            self.messages.configure(state='normal')
            self.messages.insert(tk.END, f"[离线] {self.username}: {message}\n")
            self.messages.configure(state='disabled')
            self.messages.yview(tk.END)
            if self.current_group_id:
                self.save_message_to_local(
                    self.current_group_id,
                    self.current_group_name,
                    self.username,
                    message,
                    "group"
                )
        else:
            if self.chat_mode == "private" and self.private_target:
                private_msg = f"/private {self.private_target} {message}"
                self.sock.sendall(private_msg.encode('utf-8'))
            else:
                group_msg = f"/groupmsg {message}"
                self.sock.sendall(group_msg.encode('utf-8'))

        self.input_user.delete(0, tk.END)

    def on_alpha_change(self, value):
        alpha = int(value.replace('%', '')) / 100.0
        self.set_window_alpha(alpha)
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
    import argparse
    
    # 解析命令行参数（用于非交互式启动）
    parser = argparse.ArgumentParser(description='聊天服务器')
    parser.add_argument('--port', type=int, default=None, help='服务器端口号 (默认: 8888)')
    parser.add_argument('--mode', type=str, choices=['server', 'client'], default=None, 
                       help='运行模式: server(服务器) 或 client(客户端)')
    args = parser.parse_args()
    
    # 创建启动选择窗口
    root = tk.Tk()
    root.withdraw()  # 先隐藏主窗口
    
    # 启动模式选择对话框
    mode_window = tk.Toplevel(root)
    mode_window.title("选择启动模式 Choose Mode")
    mode_window.geometry("400x250")
    mode_window.resizable(False, False)
    mode_window.transient(root)
    mode_window.grab_set()  # 模态对话框
    
    # 居中显示
    mode_window.update_idletasks()
    x = (mode_window.winfo_screenwidth() // 2) - (mode_window.winfo_width() // 2)
    y = (mode_window.winfo_screenheight() // 2) - (mode_window.winfo_height() // 2)
    mode_window.geometry(f"+{x}+{y}")
    
    # 配置样式
    if IS_WINDOWS:
        font_family = "Microsoft YaHei UI"
    elif IS_MACOS:
        font_family = "PingFang SC"
    else:
        font_family = "DejaVu Sans"
    font = tkFont.Font(family=font_family, size=10)
    title_font = tkFont.Font(family=font_family, size=12, weight='bold')
    
    # 标题
    title_label = tk.Label(mode_window, text="请选择启动模式\nPlease Choose Mode", 
                           font=title_font, pady=20)
    title_label.pack()
    
    # 模式选择框架
    mode_frame = tk.Frame(mode_window)
    mode_frame.pack(pady=20)
    
    selected_mode = tk.StringVar(value=args.mode if args.mode else "server")
    
    server_radio = tk.Radiobutton(mode_frame, text="服务器模式 Server Mode", 
                                  variable=selected_mode, value="server",
                                  font=font, anchor="w", width=25)
    server_radio.pack(pady=5, padx=20, anchor="w")
    
    client_radio = tk.Radiobutton(mode_frame, text="客户端模式 Client Mode", 
                                  variable=selected_mode, value="client",
                                  font=font, anchor="w", width=25)
    client_radio.pack(pady=5, padx=20, anchor="w")
    
    # 端口输入（仅服务器模式需要）
    port_frame = tk.Frame(mode_window)
    port_frame.pack(pady=10)
    
    port_label = tk.Label(port_frame, text="端口号 Port:", font=font)
    port_label.pack(side=tk.LEFT, padx=5)
    
    port_var = tk.StringVar(value=str(args.port if args.port else 8888))
    port_entry = tk.Entry(port_frame, textvariable=port_var, font=font, width=10)
    port_entry.pack(side=tk.LEFT, padx=5)
    
    # 按钮框架
    button_frame = tk.Frame(mode_window)
    button_frame.pack(pady=20)
    
    def start_application():
        mode = selected_mode.get()
        try:
            port = int(port_var.get()) if port_var.get().strip() else 8888
        except ValueError:
            messagebox.showerror("错误 Error", "端口号必须是数字！\nPort must be a number!")
            return
        
        mode_window.destroy()
        root.deiconify()  # 显示主窗口
        
        if mode == 'server':
            server = ChatServer(root, port=port)
        else:
            # 客户端模式
            host = simpledialog.askstring("连接 Connect", "服务器地址 IP: ", initialvalue="127.0.0.1")
            if not host:
                root.destroy()
                return
            port_input = simpledialog.askinteger("连接 Connect", "端口号 Port: ", initialvalue=8888)
            if port_input is None:
                root.destroy()
                return
            user = ''
            while not user or user.strip() == '':
                user = simpledialog.askstring("连接 Connect", "用户名 Username: ")
                if not user:
                    root.destroy()
                    return
            client = ChatClient(root)
            client.connect(host, port_input, user)
        
        root.mainloop()
    
    def cancel_application():
        root.destroy()
        mode_window.destroy()
    
    start_button = tk.Button(button_frame, text="确定 OK", command=start_application, 
                            font=font, bg="#4CAF50", fg="white", width=10, padx=10, pady=5)
    start_button.pack(side=tk.LEFT, padx=10)
    
    cancel_button = tk.Button(button_frame, text="取消 Cancel", command=cancel_application, 
                             font=font, bg="#f44336", fg="white", width=10, padx=10, pady=5)
    cancel_button.pack(side=tk.LEFT, padx=10)
    
    # 绑定回车键
    mode_window.bind('<Return>', lambda e: start_application())
    mode_window.bind('<Escape>', lambda e: cancel_application())
    port_entry.focus_set()
    
    # 运行选择对话框
    mode_window.mainloop()