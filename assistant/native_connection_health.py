"""Read-only authenticated connection classification; never reconnect or log in."""
import urllib.parse
from native_gateway import client,settings

def status():
    transport=None
    try:
        cfg,original=settings();transport=client()
        server=transport.request('/v1/status')
        account=transport.request(transport.base)
        if server.get('serverId')!=cfg['server_id']:return 'native_target_changed'
        if account.get('authenticated') is not True:return 'native_auth_required'
        if account.get('ownUsername')!=original['account']:return 'native_target_changed'
        if account.get('connection')!='online':return 'native_reconnecting'
        group=transport.request(transport.base+'/groups/'+urllib.parse.quote(original['group'],safe=''))
        if group.get('id')!=original['group'] or group.get('isInGroup') is not True:return 'native_target_changed'
        return 'online'
    except Exception:return 'native_transport_unavailable'
    finally:
        if transport is not None:transport.close()
