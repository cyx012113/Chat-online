import socket
import threading
import tkinter as tk
from tkinter import messagebox
from tkinter import font as tkFont

class ChatServer:
    def __init__(self, master):
        self.master = master
        self.master.title("聊天服务器 Chat Server")
        self.setup_widgets()
        self.sock = None
        self.start = False
        self.clients = []
        self.client_names = {}  # 新增：存储客户端名称
        self.muted_clients = set()  # 新增：存储被禁言的客户端

    def setup_widgets(self):
        font = tkFont.Font(family="华文行楷", size=12)  # 设置字体和字号

        self.messages = tk.Text(self.master, state='disabled', font=font)
        self.messages.grid(row=0, column=0, columnspan=2)

        self.client_listbox = tk.Listbox(self.master, font=font)
        self.client_listbox.grid(row=0, column=2, rowspan=2)

        self.input_server = tk.Entry(self.master, font=font)
        self.input_server.grid(row=2, column=0)

        self.send_button = tk.Button(self.master, text="发送 Send", command=self.send_server_message, font=font)
        self.send_button.grid(row=3, column=0)

        self.kick_button = tk.Button(self.master, text="踢出 Kick", command=self.kick_selected_user, font=font)
        self.kick_button.grid(row=3, column=1)

        self.mute_button = tk.Button(self.master, text="禁言 Mute", command=self.mute_selected_user, font=font)
        self.mute_button.grid(row=4, column=1)

        self.unmute_button = tk.Button(self.master, text="解除禁言 Unmute", command=self.unmute_selected_user, font=font)
        self.unmute_button.grid(row=5, column=1)

        tk.Label(self.master, text="服务器IP地址 Server IP address: " + str(socket.gethostbyname(socket.gethostname())), font=font).grid(row=1, column=2)

        self.start_button = tk.Button(self.master, text="启动服务器 Start server", command=self.start_server, font=font)
        self.start_button.grid(row=1, column=0)

        self.master.bind("<Return>", self.send_server_message_event)

        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    def start_server(self):
        if self.start == True:
            messagebox.showerror("服务器已启动 The server is up", "服务器已启动，请勿重复启动服务器！The server has been started, do not start the server repeatedly!")
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('0.0.0.0', 8888))
        self.sock.listen(1000)
        threading.Thread(target=self.accept_connections, daemon=True).start()
        self.start = True

    def accept_connections(self):
        while True:
            client, addr = self.sock.accept()
            self.clients.append(client)
            threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()

    def handle_client(self, client):
        while True:
            try:
                data = client.recv(1024)
                message = data.decode('utf-8')
                if message.startswith("/kick "):
                    self.kick_user(message.split()[1])
                elif message.startswith("/mute "):
                    self.mute_user(message.split()[1])
                elif message.startswith("/unmute "):
                    self.unmute_user(message.split()[1])
                elif message.startswith("/name "):
                    self.client_names[client] = message.split()[1]
                    self.update_client_list()
                elif client not in self.muted_clients:
                    for other in self.clients:
                        if other != client:
                            other.sendall(data)
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, message + '\n')
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
            except socket.error as e:
                self.clients.remove(client)
                if client in self.client_names:
                    del self.client_names[client]
                self.update_client_list()
                break

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

    def kick_user(self, username):
        for client, name in self.client_names.items():
            if name == username:
                client.sendall(f"{username} 已被踢出聊天室。{username} ,you have been kicked out of the chat room.".encode('utf-8'))
                client.close()
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
            self.kick_user(selected_user)

    def mute_selected_user(self):
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if selected_user:
            self.mute_user(selected_user)

    def unmute_selected_user(self):
        selected_user = self.client_listbox.get(tk.ACTIVE)
        if selected_user:
            self.unmute_user(selected_user)

    def update_client_list(self):
        self.client_listbox.delete(0, tk.END)
        for name in self.client_names.values():
            self.client_listbox.insert(tk.END, name)
        # 向所有客户端发送更新后的用户列表
        client_list_message = "/name " + " ".join(self.client_names.values())
        for client in self.clients:
            client.sendall(client_list_message.encode('utf-8'))

    def on_close(self):
        if self.sock:
            for client in self.clients:
                client.sendall(b"/quit")
                client.close()
            self.sock.close()
        self.master.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    server = ChatServer(root)
    root.mainloop()