"""Scoped tool dispatch and output validation shared by subscription transports."""
import json
import time
from codex_client import Codex
from futu_evidence import TOOLS as FUTU_TOOLS

def validate_output(value,schema):
    """Validate the closed subset used by ANSWER_SCHEMA and revision_schema locally."""
    kind=schema.get('type')
    types={'object':dict,'array':list,'string':str,'boolean':bool}
    if kind not in types or type(value) is not types[kind]:raise ValueError('tool_bridge_output_schema')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('tool_bridge_output_schema')
    if kind=='object':
        properties=schema.get('properties',{})
        if not set(schema.get('required',[]))<=set(value):raise ValueError('tool_bridge_output_schema')
        if schema.get('additionalProperties') is False and set(value)-set(properties):raise ValueError('tool_bridge_output_schema')
        for key,item in value.items():
            if key in properties:validate_output(item,properties[key])
    if kind=='array':
        if len(value)>schema.get('maxItems',len(value)) or len(value)<schema.get('minItems',0):raise ValueError('tool_bridge_output_schema')
        for item in value:validate_output(item,schema['items'])
    if kind=='string' and len(value)>schema.get('maxLength',len(value)):raise ValueError('tool_bridge_output_schema')


def input_blocks(items):
    result=[]
    for item in items:
        kind=item.get('type')
        if kind in ('text','inputText'):
            result.append({'type':'text','text':item['text']})
        elif kind in ('image','inputImage'):
            url=item.get('url') or item.get('imageUrl')
            if not isinstance(url,str) or not url.startswith('data:image/'):
                raise ValueError('tool_bridge_unsupported_image_source')
            header,data=url.split(',',1)
            result.append({'type':'image','source':{'type':'base64','media_type':header[5:].split(';')[0],'data':data}})
        else:
            raise ValueError('tool_bridge_unsupported_input')
    return result


class ScopedToolBridge(Codex):
    def send(self,value):
        # Called only by the inherited, scope-checked local tool dispatcher.
        self.tool_response=value


    def _mcp_tools(self):
        if self.mcp and getattr(self.mcp,'proc',None) and self.mcp.proc.poll() is None:
            for tool in self.mcp_definitions:
                self.tool_map[tool['name']]=('futu',tool['name'].removeprefix('futu__'))
            return self.mcp_definitions
        self.mcp=Codex(timeout=20)
        try:
            self.mcp._start()
            thread=self.mcp.rpc('thread/start',{'model':'gpt-6-astra','ephemeral':True,
                'sandbox':'read-only','approvalPolicy':'never'})
            self.mcp_thread=thread['thread']['id']
            status=self.mcp.rpc('mcpServerStatus/list',{'threadId':self.mcp_thread,'serverName':'futu_research'})
            result=[]
            for server in status.get('data',[]):
                if server.get('name')!='futu_research':continue
                tools=server.get('tools',{})
                for name,tool in tools.items():
                    actual=tool.get('name',name)
                    if actual not in FUTU_TOOLS:continue
                    public='futu__'+actual
                    self.tool_map[public]=('futu',actual)
                    result.append({'name':public,'description':tool.get('description','Read-only market data'),
                        'input_schema':tool.get('inputSchema',{'type':'object'})})
            self.mcp_definitions=result
            return result
        except Exception:
            self.mcp.close();self.mcp=None
            return []


    def _local_call(self,block):
        name=block.get('name')
        # Resolve case only against the already-advertised allowlist, never guess tools.
        if isinstance(name,str):
            name=next((registered for registered in self.tool_map if registered.casefold()==name.casefold()),name)
        if name not in self.tool_map:return {'type':'tool_result','tool_use_id':block['id'],'is_error':True,'content':'Tool not permitted'}
        namespace,tool=self.tool_map[name]
        if namespace=='futu':
            try:
                self.mcp.deadline=min(self.deadline,time.monotonic()+25)
                result=self.mcp.rpc('mcpServer/tool/call',{'threadId':self.mcp_thread,
                    'server':'futu_research','tool':tool,'arguments':block.get('input',{})})
                result=result.get('result',result)
                self.public_feed_events.append({'type':'mcpToolCall','server':'futu_research','tool':tool,
                    'status':'completed','result':result,'readOnlyHint':True})
                return {'type':'tool_result','tool_use_id':block['id'],'content':json.dumps(result,ensure_ascii=False,separators=(',',':'))}
            except Exception:
                return {'type':'tool_result','tool_use_id':block['id'],'is_error':True,'content':'Market tool unavailable; do not invent data.'}
        self.tool_response=None
        self.events.put({'id':block['id'],'method':'item/tool/call','params':{
            'threadId':self.active_thread,'namespace':namespace,'tool':tool,'arguments':block.get('input',{})}})
        super().receive()
        response=self.tool_response or {}
        result=response.get('result',{})
        content=input_blocks(result.get('contentItems',[])) if result.get('contentItems') else 'Tool unavailable'
        return {'type':'tool_result','tool_use_id':block['id'],'is_error':not result.get('success',False),'content':content}
