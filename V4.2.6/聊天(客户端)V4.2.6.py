import socket
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
from tkinter import font as tkFont

class ChatClient:
    def __init__(self, master):
        self.master = master
        self.master.title("聊天客户端 Chat Client")
        self.setup_widgets()
        self.sock = None
        self.muted = False  # 新增：标记是否被禁言

    def setup_widgets(self):
        font = tkFont.Font(family="华文行楷", size=12)  # 设置字体和字号

        self.messages = tk.Text(self.master, state='disabled', font=font)
        self.messages.grid(row=0, column=0, columnspan=2)

        self.client_listbox = tk.Listbox(self.master, font=font)
        self.client_listbox.grid(row=0, column=2, rowspan=2)

        self.input_user = tk.Entry(self.master, font=font)
        self.input_user.grid(row=1, column=0)
        self.send_button = tk.Button(self.master, text="发送 Sent", command=self.send_message, font=font)
        self.send_button.grid(row=1, column=1)
        
        self.master.bind("<Return>", self.send_message)

        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    def connect(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((host, port))
            threading.Thread(target=self.receive_message, args=(user,), daemon=True).start()
        except socket.error as e:
            messagebox.showerror("连接错误 Connect Error", f"无法连接到服务器 Can't connect to server: {e}")
            self.master.destroy()
        else:
            messagebox.showinfo("连接成功 Connected accept.", f"已连接到服务器 {host}:{port} Connected to server.")
            self.sock.sendall(f"/name {user}".encode('utf-8'))
            self.sock.sendall(f"用户 {user} 加入了聊天室。User {user} has joined the chat room.".encode('utf-8'))

    def send_message(self, event=None):
        if self.muted:
            messagebox.showwarning("禁言 Banned", "你已被禁言，无法发送消息。You have been banned from sending messages.")
            return
        message = f"{user}: " + self.input_user.get()
        if message:
            try:
                self.sock.sendall(message.encode('utf-8'))
                self.input_user.delete(0, tk.END)
                self.messages.configure(state='normal')
                self.messages.insert(tk.END, message + '\n')
                self.messages.configure(state='disabled')
                self.messages.yview(tk.END)
            except socket.error as e:
                messagebox.showerror("发送错误 Sent Error", f"无法发送消息 Can't to sent message: {e}")

    def receive_message(self, username):
        while True:
            try:
                data = self.sock.recv(1024)
                message = data.decode('utf-8')
                if message.startswith("/name "):
                    self.update_client_list(message.split()[1:])
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, '正在刷新成员列表。Refreshing user list.\n')
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
                elif message == f"{username} 已被踢出聊天室。{username} ,you have been kicked out of the chat room.":
                    messagebox.showinfo("提示 Prompt", "你已被踢出聊天室。You have been kicked out of the chat room.")
                    self.on_close()
                    break
                elif message == f"用户 {username} 被禁言。{username} ,you have been banned.":
                    self.muted = True
                    messagebox.showinfo("提示", "你已被禁言。You have been banned.")
                elif message == f"用户 {username} 被解除禁言。{username} ,you have been unbanned.":
                    self.muted = False
                    messagebox.showinfo("提示", "你已被解除禁言。You have been unbanned.")
                elif message == f"/quit":
                    messagebox.showinfo("提示", "服务器已关闭。The server has been closed.")
                    self.on_close()
                    break
                else:
                    self.messages.configure(state='normal')
                    self.messages.insert(tk.END, message + '\n')
                    self.messages.configure(state='disabled')
                    self.messages.yview(tk.END)
            except socket.error as e:
                print(f"接收错误 Receive error: {e}")
                break

    def update_client_list(self, names):
        self.client_listbox.delete(0, tk.END)
        for name in names:
            self.client_listbox.insert(tk.END, name)

    def on_close(self):
        if self.sock:
            if user != '':
                self.sock.sendall(f"用户 {user} 离开了聊天室。User {user} has left the chat room.".encode('utf-8'))
            else:
                self.sock.sendall("无名用户离开了聊天室。Unnamed user has left the chat room.".encode('utf-8'))
            self.sock.close()
        self.master.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    client = ChatClient(root)
    host = simpledialog.askstring("连接 Connect", "服务器地址 IP: ", initialvalue="127.0.0.1")
    port = simpledialog.askinteger("连接 Connect", "端口号 Port: ", initialvalue=8888)
    user = simpledialog.askstring("连接 Connect", "用户名 Username: ")
    client.connect(host, port)
    root.mainloop()