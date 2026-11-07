"""Owner-facing status presentation; pure, no chats or runtime mutations."""
import time


def present(data, stopping=False, now=None):
    now=time.time() if now is None else now
    state=data['state']
    age=max(0,now-data['updated'])
    jobs=data.get('jobs',{})
    queued=sum(jobs.get(s,0) for s in ('pending','retry','ready'))
    generating=jobs.get('generating',0)
    sending=sum(jobs.get(s,0) for s in ('dispatching','sending'))
    review=sum(jobs.get(s,0) for s in ('uncertain','attention'))
    labels={'running':'运行正常','stopped':'已暂停' if stopping else '当前未运行',
            'degraded':'连接或服务暂时异常','attention':'有回复需要核对',
            'starting':'正在连接微信','paused':'等待目标群验证','needs_login':'微信需要重新登录'}
    title=labels.get(state,'运行状态待核实')
    details=(f'排队 {queued}  ·  正在回答 {generating}  ·  正在发送 {sending}\n'
             f'待核对 {review}  ·  累计未完成 {jobs.get("failed",0)}\n'
             f'累计已确认回复 {jobs.get("confirmed",0)}\n'
             f'累计已过期 {jobs.get("expired",0)}  ·  提问已撤回 {jobs.get("cancelled",0)}')
    if age>30 and not (state=='stopped' and stopping):
        title='状态已过期，无法确认是否在线'
        details='以下是上次记录，非实时状态：\n'+details
        note='请稍后刷新；若持续不更新，请交给维护助手检查。不要重复补发旧消息。'
    elif stopping and state!='stopped':
        title='正在暂停'
        note='正在等待已开始的任务结束。暂停完成前请勿反复启动；关闭面板不会中止后台。'
    elif state=='stopped':
        note='点击“启动 / 恢复”后继续处理。关闭面板不影响后台；已确认回复不会重复发送。'
    elif state=='needs_login':
        note='微信认证已失效，请联系维护助手恢复登录。未确认的消息不会自动重发。'
    elif state=='degraded':
        note=('网络暂断，服务正在自动重连，队列保留；暂不需要重新扫码。'
              if data.get('intake',{}).get('reason')=='native_reconnecting'
              else '连接或处理服务异常，暂不能保证回复。请交给维护助手检查，保留现有队列。')
    elif state=='attention':
        note=('有任务因目标或现场校验失败暂停发送，请联系维护助手核对。'
              if jobs.get('attention',0) else '个别消息发送结果待核对，不会自动重发；其他新问题可继续处理。')
    elif state in ('starting','paused'):
        note='正在核对微信连接与授权群，通过后自动继续；不会发送到其他群。'
    elif generating or sending or queued:
        note='复杂问题需要更长时间；新提问会进入队列，群里只发送最终回答。'
    else:
        note='在授权群 @Openbot 提问。看图可引用图片后再 @；电脑睡眠或关机会停止工作。'
    return title,details,note
