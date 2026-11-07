"""On-demand local control window. No HTTP server and no chat-content display."""
import json
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import ttk
import startup
from status_view import present
ROOT=Path(__file__).resolve().parent
window=tk.Tk(); window.title('微信群助手'); window.geometry('560x420')
window.resizable(False,False)
title=tk.StringVar(value='读取状态中'); details=tk.StringVar(); note=tk.StringVar()
ttk.Label(window,textvariable=title,font=('Microsoft YaHei UI',16)).pack(pady=(24,10))
ttk.Label(window,textvariable=details,justify='left').pack(pady=10)
ttk.Label(window,textvariable=note,wraplength=510,justify='left').pack(pady=10)
row=ttk.Frame(window); row.pack(pady=12)

def start():
    subprocess.Popen(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'start-bot.ps1')],
        creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def stop():
    (ROOT/'runtime/STOP').write_text('user requested pause',encoding='utf-8')
    note.set('正在暂停，当前已开始的回复处理完后停止。')

ttk.Button(row,text='启动 / 恢复',command=start).pack(side='left',padx=6)
ttk.Button(row,text='暂停',command=stop).pack(side='left',padx=6)
login_start=tk.BooleanVar(value=startup.enabled())
ttk.Checkbutton(window,text='Windows 登录后自动启动（手动暂停后保持暂停）',variable=login_start,
    command=lambda:startup.set_enabled(login_start.get())).pack(pady=4)

def refresh():
    try:
        data=json.loads((ROOT/'runtime/bot-status.json').read_text(encoding='utf-8'))
        heading,counts,hint=present(data,stopping=(ROOT/'runtime/STOP').exists())
        title.set(heading);details.set(counts);note.set(hint)
    except (OSError,ValueError,KeyError,TypeError):
        title.set('暂时无法读取运行状态')
        details.set('当前是否在线尚未确认')
        note.set('请稍后查看；如持续如此，请联系维护助手。不要仅凭旧状态判断已回复。')
    window.after(1500,refresh)
refresh(); window.mainloop()

