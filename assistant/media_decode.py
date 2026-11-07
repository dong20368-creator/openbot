"""Bounded-lifetime local media parser. Private bytes use pipes, not disk."""
import base64
import json
import sys
from media_context import prepare,MAX_BYTES

def main():
    request=json.loads(sys.stdin.buffer.read(MAX_BYTES*2))
    raw=base64.b64decode(request['data'],validate=True)
    result=prepare(raw,request['kind'],request['name'])
    sys.stdout.write(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':
    try: main()
    except Exception:
        sys.stdout.write(json.dumps({'error':'media_decode_failed'}));sys.exit(1)
