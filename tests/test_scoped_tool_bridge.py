import unittest
from unittest.mock import Mock
from argon_client import Argon
from scoped_tool_bridge import input_blocks,validate_output
from codex_client import Codex
from reply_contract import ANSWER_SCHEMA

class ScopedToolBridgeTests(unittest.TestCase):
    def client(self):
        b=Argon()
        b.prepare_process()
        b.active_thread="synthetic"
        b.public_feed_events=[]
        return b

    def test_vision_and_no_arbitrary_file_access(self):
        value=input_blocks([{'type':'image','url':'data:image/png;base64,TEST'}])
        self.assertEqual(value[0]['source']['data'],'TEST')
        with self.assertRaises(ValueError):input_blocks([{'type':'image','url':'file:///secret'}])


    def test_scoped_dispatch_and_unknown_tool(self):
        b=self.client();b.local_tool_seconds=0;b.math_calls=0;b.math_tool_rounds=0
        b.tool_map={'investment_math__calculate':('investment_math','calculate')}
        result=b._local_call({'id':'1','name':'Investment_math__calculate','input':{'expression':'12*3'}})
        self.assertFalse(result['is_error'])
        self.assertIn('36',result['content'][0]['text'])
        self.assertTrue(b._local_call({'id':'2','name':'shell','input':{}})['is_error'])


    def test_output_contract_enforced_locally(self):
        value={'category':'conversation','model_disclosure':False,'answer':'test',
            'unavailable_reason':'none','sources':[],'memories':[]}
        validate_output(value,ANSWER_SCHEMA)
        for bad in ({**value,'memories':'invalid'},{**value,'model_disclosure':'true'},{**value,'extra':'not permitted'}):
            with self.assertRaises(ValueError):validate_output(bad,ANSWER_SCHEMA)


    def test_original_pipeline_and_scoped_memory_dispatch_retained(self):
        self.assertIs(Argon.answer,Codex.answer)
        b=self.client();b.memory_calls=0;b.local_tool_seconds=0
        b.retriever=Mock(return_value=[{'question':'synthetic question','answer':'synthetic answer'}])
        b.tool_map={'wechat_memory__search':('wechat_memory','search')}
        result=b._local_call({'id':'memory','name':'Wechat_memory__search','input':{'query':'synthetic'}})
        self.assertFalse(result['is_error'])
        b.retriever.assert_called_once_with('synthetic')
        self.assertEqual(b.memory_calls,1)
