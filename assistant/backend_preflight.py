"""Check the exact Linux service import location; never inspect chat contents."""
import json,os,subprocess

def verify():
    script="""import importlib,json,pathlib
base=pathlib.Path('/opt/wechat-agent-v2').resolve()
names=('inbox','media_reader','member_window','group_summary','native_gateway','native_connection_health')
ok=[]
for name in names:
    module=importlib.import_module(name)
    if pathlib.Path(module.__file__).resolve().parent!=base:raise RuntimeError('wrong_module_origin')
    ok.append(name)
print(json.dumps({'ok':True,'modules':ok}))
"""
    run=subprocess.run(['wsl','-d','WeChatAgent','-u','root','--cd','/opt/wechat-agent-v2','--exec','python3','-c',script],
        capture_output=True,encoding='utf8',timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if run.returncode:raise RuntimeError('backend_dependencies_unavailable')
    result=json.loads(run.stdout)
    if result.get('ok') is not True:raise RuntimeError('backend_dependencies_unavailable')
    return result
