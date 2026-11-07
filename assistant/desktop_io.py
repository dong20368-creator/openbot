"""UI adapter for the user's requested unattended bot, inside its container only.

No shell strings, no arbitrary commands/coordinates from chat. It operates only
the main WeChat window and does not navigate to other chats. The Windows caller
must OCR-check the header before paste and again before send.
"""
import base64
import hashlib
import io
import json
import subprocess
import sys
import time
from PIL import Image, ImageDraw, ImageChops
from Xlib import display, X

SEND_MAY_HAVE_OCCURRED=False


def command(*args,input=None):
    try:
        return subprocess.run(list(args),input=input,stdout=subprocess.DEVNULL if input is not None else subprocess.PIPE,
            stderr=subprocess.DEVNULL,check=True,timeout=8).stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError('command_timeout:'+args[0]+':'+args[1]) from None


def window():
    ids=command('xdotool','search','--onlyvisible','--class','wechat').decode().split()
    found=[]
    for wid in ids:
        values=dict(line.split('=',1) for line in command('xdotool','getwindowgeometry','--shell',wid).decode().splitlines() if '=' in line)
        if int(values['WIDTH'])>=700 and int(values['HEIGHT'])>=500:
            found.append((wid,values))
    if len(found)!=1: raise RuntimeError('Main window not unique')
    wid,v=found[0]
    return wid,{k:int(v[k]) for k in ('X','Y','WIDTH','HEIGHT')}


def click(g,x,y):
    # --sync waits for a movement event and can hang when already at this point.
    command('xdotool','mousemove',str(g['X']+x),str(g['Y']+y),'click','1')


def header(wid,g):
    d=display.Display()
    try:
        root=d.screen().root
        width,height=min(g['WIDTH']-320,680),55
        raw=root.get_image(g['X']+285,g['Y']+18,width,height,X.ZPixmap,0xffffffff)
        im=Image.frombytes('RGB',(width,height),raw.data,'raw','BGRX')
        active=root.get_full_property(d.intern_atom('_NET_ACTIVE_WINDOW'),X.AnyPropertyType)
        signature={'window':wid,'width':g['WIDTH'],'height':g['HEIGHT'],
            'title_hash':hashlib.sha256(im.tobytes()).hexdigest()}
        return im,signature,bool(active is not None and int(active.value[0])==int(wid))
    finally: d.close()


def verify_header(wid,g,request):
    expected=request.get('expected_header')
    if not expected: raise RuntimeError('Missing verified target fingerprint')
    _,signature,active=header(wid,g)
    if signature!=expected or not active: raise RuntimeError('Target fingerprint changed')

def search_capture(wid,g):
    d=display.Display()
    try:
        raw=d.screen().root.get_image(g['X']+60,g['Y']+65,225,g['HEIGHT']-100,X.ZPixmap,0xffffffff)
        im=Image.frombytes('RGB',(225,g['HEIGHT']-100),raw.data,'raw','BGRX')
        out=io.BytesIO(); im.save(out,format='PNG')
        return {'image':base64.b64encode(out.getvalue()).decode(),'hash':hashlib.sha256(im.tobytes()).hexdigest(),'window':wid}
    finally: d.close()

def window_capture(g):
    d=display.Display()
    try:
        raw=d.screen().root.get_image(g['X'],g['Y'],g['WIDTH'],g['HEIGHT'],X.ZPixmap,0xffffffff)
        im=Image.frombytes('RGB',(g['WIDTH'],g['HEIGHT']),raw.data,'raw','BGRX')
        out=io.BytesIO();im.save(out,format='PNG')
        stable=im.copy()
        stable.paste(stable_editor(im),(285,g['HEIGHT']-240))
        return {'image':base64.b64encode(out.getvalue()).decode(),'width':g['WIDTH'],'height':g['HEIGHT'],
            'view_hash':hashlib.sha256(stable.tobytes()).hexdigest()}
    finally: d.close()


def stable_editor(im):
    crop=im.crop((285,im.height-240,im.width-18,im.height-65)).convert('RGB')
    background=crop.getpixel((crop.width-2,2))
    # Pinned WeChat theme: the 2px blinking caret is exactly this green.
    # Body content is independently checked by exact clipboard SHA-256; quote
    # text/card pixels remain in this fingerprint. Never mask message history.
    diff=ImageChops.difference(crop,Image.new('RGB',crop.size,(7,193,96)))
    r,g,b=diff.split()
    mask=ImageChops.lighter(ImageChops.lighter(r,g),b).point(lambda p:255 if p==0 else 0)
    crop.paste(background,mask=mask)
    return crop


def editor_state(g):
    """Includes native quote chips, unlike copying the text input alone."""
    cap=window_capture(g)
    im=Image.open(io.BytesIO(base64.b64decode(cap['image'])))
    crop=stable_editor(im)
    extrema=crop.convert('L').getextrema()
    return {'editor_hash':hashlib.sha256(crop.tobytes()).hexdigest(),
        'blank':extrema[1]-extrema[0]<=5}


def verify_editor(g,request):
    expected=request.get('expected_editor')
    if not expected or editor_state(g)['editor_hash']!=expected:
        raise RuntimeError('Editor fingerprint changed')


def read_input(g):
    # The bottom of a long editor may contain a quote card or hyperlink. Focus
    # its first text line instead, and normalize scrolling after clipboard read.
    click(g,330,g['HEIGHT']-230)
    sentinel='wechat-agent-input-read-sentinel'
    command('xclip','-selection','clipboard',input=sentinel.encode())
    command('xdotool','key','--clearmodifiers','ctrl+a','ctrl+c');time.sleep(.1)
    text=command('xclip','-selection','clipboard','-o')
    command('xdotool','key','--clearmodifiers','ctrl+End')
    command('xdotool','mousemove',str(g['X']+g['WIDTH']-50),str(g['Y']+g['HEIGHT']-110),
        'click','--repeat','8','--delay','15','5')
    time.sleep(.15)
    return b'' if text==sentinel.encode() else text


def perform(request):
    global SEND_MAY_HAVE_OCCURRED
    SEND_MAY_HAVE_OCCURRED=False
    wid,g=window()
    if request['op'] in ('quote_view','quote_click','quote_copy_read','quote_escape','quote_editor','quote_scroll','quote_latest'):
        verify_header(wid,g,request)
        if request['op']=='quote_view': return window_capture(g)
        if request['op']=='quote_editor': return editor_state(g)
        if request['op']=='quote_latest':
            if not editor_state(g)['blank']:raise RuntimeError('Existing draft')
            command('xdotool','mousemove',str(g['X']+g['WIDTH']-80),str(g['Y']+200),
                'click','--repeat','40','--delay','10','5')
            time.sleep(.2);before=window_capture(g)['view_hash']
            command('xdotool','click','--repeat','4','--delay','20','5')
            time.sleep(.15)
            return {'at_bottom':before==window_capture(g)['view_hash']}
        if request['op']=='quote_copy_read': return {'text':command('xclip','-selection','clipboard','-o').decode()}
        if request['op']=='quote_escape':
            command('xdotool','key','--clearmodifiers','Escape'); return {'closed':True}
        if request['op']=='quote_scroll':
            if not request.get('expected_view') or window_capture(g)['view_hash']!=request['expected_view']:
                raise RuntimeError('Quote view changed')
            command('xdotool','mousemove',str(g['X']+g['WIDTH']-80),str(g['Y']+200),
                'click','--repeat','4','--delay','60','4')
            time.sleep(.2);return {'scrolled':True}
        x,y=request.get('x',-1),request.get('y',-1)
        if not (285<x<g['WIDTH'] and 65<y<g['HEIGHT']-10): raise ValueError('Outside message region')
        if not request.get('expected_view') or window_capture(g)['view_hash']!=request['expected_view']:
            raise RuntimeError('Quote view changed')
        button=3 if request.get('right') else 1
        command('xdotool','mousemove',str(g['X']+int(x)),str(g['Y']+int(y)),'click',str(button))
        time.sleep(.15); return {'clicked':True}
    if request['op']=='search_group':
        name=request.get('name','')
        if not name or len(name)>80 or '\n' in name: raise ValueError('Invalid group name')
        command('xdotool','windowactivate','--sync',wid)
        click(g,330,g['HEIGHT']-80)
        sentinel='wechat-agent-check-empty-navigation'
        command('xclip','-selection','clipboard',input=sentinel.encode())
        command('xdotool','key','--clearmodifiers','ctrl+a','ctrl+c'); time.sleep(.1)
        if command('xclip','-selection','clipboard','-o').decode() not in ('',sentinel):
            raise RuntimeError('Existing draft; navigation cancelled')
        click(g,150,42)
        command('xclip','-selection','clipboard',input=name.encode())
        command('xdotool','key','--clearmodifiers','ctrl+a','ctrl+v'); time.sleep(.7)
        return search_capture(wid,g)
    if request['op']=='select_group':
        current=search_capture(wid,g)
        if current['window']!=request.get('window') or current['hash']!=request.get('hash'): raise RuntimeError('Search results changed')
        x,y=request.get('x',-1),request.get('y',-1)
        if not (0<=x<225 and 0<=y<g['HEIGHT']-100): raise ValueError('Selection outside search results')
        click(g,60+int(x),65+int(y)); time.sleep(.4)
        return {'selected':True}
    if request['op'] in ('paste','check_input','send','check_empty'):
        command('xdotool','windowactivate','--sync',wid)
        verify_header(wid,g,request)
    if request['op']=='check_empty':
        empty=not read_input(g)
        state=editor_state(g)
        return {'empty':empty and state['blank'],**state}
    if request['op']=='check_clipboard':
        actual=command('xclip','-selection','clipboard','-o').decode()
        expected=request['text']
        return {'matches':actual==expected,'actual_length':len(actual),'expected_length':len(expected),
            'normalized_matches':actual.replace('\r\n','\n').replace('\r','\n')==expected.replace('\r\n','\n').replace('\r','\n'),
            'first_difference':next(([ord(a),ord(b)] for a,b in zip(actual,expected) if a!=b),None)}
    if request['op']=='check_input':
        command('xdotool','windowactivate','--sync',wid)
        text=read_input(g)
        return {'input_sha256':hashlib.sha256(text).hexdigest(),'input_bytes':len(text),**editor_state(g)}
    if request['op']=='header':
        command('xdotool','windowactivate','--sync',wid)
        im,signature,active=header(wid,g)
        out=io.BytesIO(); im.save(out,format='PNG')
        return {'header_png':base64.b64encode(out.getvalue()).decode(),'signature':signature,'active':active}
    if request['op']=='paste':
        text=request['text']
        if not text or len(text)>4000: raise ValueError('Invalid text')
        command('xdotool','windowactivate','--sync',wid)
        # Inspect a possible existing draft before modifying it. Copying an
        # empty selection leaves a sentinel clipboard unchanged.
        draft=read_input(g).decode()
        if draft: raise RuntimeError('Existing draft; input untouched')
        verify_editor(g,request)
        command('xclip','-selection','clipboard',input=text.encode())
        command('xdotool','key','--clearmodifiers','ctrl+v')
        time.sleep(.2)
        readback=read_input(g).decode()
        if readback!=text: raise RuntimeError('Input readback mismatch; not sent')
        return {'input_sha256':hashlib.sha256(readback.encode()).hexdigest(),**editor_state(g)}
    if request['op']=='send':
        # Caller checks the title immediately before this operation. Verify the
        # input again to avoid sending a concurrent user edit.
        readback=read_input(g)
        if hashlib.sha256(readback).hexdigest()!=request['text_sha256']:
            raise RuntimeError('Draft changed; not sent')
        verify_header(wid,g,request)
        verify_editor(g,request)
        # From this point a timeout is ambiguous; never claim it was unsent.
        SEND_MAY_HAVE_OCCURRED=True
        click(g,g['WIDTH']-65,g['HEIGHT']-34)
        return {'clicked':True}
    raise ValueError('Unknown operation')


if __name__=='__main__':
    try: print(json.dumps(perform(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({'error':str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__,
            'send_may_have_occurred':SEND_MAY_HAVE_OCCURRED})); sys.exit(1)
