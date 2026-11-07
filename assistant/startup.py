"""Per-user login startup for this project only; no admin task or service."""
from pathlib import Path
import winreg
ROOT=Path(__file__).resolve().parent
KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME='WeChatAssistantLocal'

def command():
    return 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+str(ROOT/'start-login.ps1')+'"'

def enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key: return winreg.QueryValueEx(key,NAME)[0]==command()
    except FileNotFoundError: return False

def set_enabled(value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as key:
        if value: winreg.SetValueEx(key,NAME,0,winreg.REG_SZ,command())
        else:
            try: winreg.DeleteValue(key,NAME)
            except FileNotFoundError: pass

if __name__=='__main__':
    import sys
    set_enabled('--enable' in sys.argv)
    print('login_startup_enabled',enabled())
