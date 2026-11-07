"""User-facing data controls must describe their actual scope."""
import unittest
import test_core


class MemoryControlsCopy(unittest.TestCase):
    setUp=test_core.PipelineTests.setUp
    tearDown=test_core.PipelineTests.tearDown

    def control(self,text,reference=None):
        return self.store.memory_control({'room':'room','sender':'alice','question':text,'reference_id':reference})

    def test_forget_and_restore_explain_public_views_without_claiming_wechat_deletion(self):
        reply=self.control('忘记我')
        self.assertFalse(self.store.memory_setting('room','alice')[0])
        self.assertIn('历史观点',reply)
        self.assertIn('微信原始聊天不会被删除',reply)
        self.assertIn('整体摘要仍按原范围处理',reply)
        self.assertIn('整体摘要不受',self.control('查看我的记忆'))
        reply=self.control('恢复记忆')
        self.assertTrue(self.store.memory_setting('room','alice')[0])
        self.assertIn('不恢复已清除',reply)
        self.assertIn('公开观点汇总也恢复可用',reply)

    def test_quoted_control_does_not_change_memory_setting(self):
        before=self.store.memory_setting('room','alice')
        self.assertIsNone(self.control('忘记我','s:quoted'))
        self.assertEqual(self.store.memory_setting('room','alice'),before)


if __name__=='__main__':unittest.main()
