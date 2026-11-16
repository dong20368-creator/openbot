import hashlib
import unittest
from link_delivery import dispatch_parts, split_links


class LinkDeliveryTests(unittest.TestCase):
    def test_separates_sources_without_losing_claims(self):
        parts = split_links('相关消息尚未证实。[原帖](https://x.com/example/status/123)\n同源 https://x.com/example/status/123。')
        self.assertEqual(len(parts), 2)
        self.assertNotIn('https://', parts[0])
        self.assertIn('尚未证实', parts[0])
        self.assertEqual(parts[1].count('https://'), 1)
        self.assertEqual(split_links('普通回复'), ['普通回复'])

    def test_url_label_does_not_leak_into_body(self):
        self.assertNotIn('https://', split_links('[https://x.com/a](https://x.com/a)')[0])

    def test_multipart_receipts_and_recovery_do_not_send(self):
        text = '摘要 [出处](https://example.com/a)'
        job = dict(id=7, account='account', room='room', answer=text,
                   answer_hash=hashlib.sha256(text.encode()).hexdigest(), attachments=['synthetic'])
        saved = {}; sends = []
        def single(req):
            j = req['job']
            if req['op'] == 'receipt':
                return saved.get(j['id'])
            sends.append(req)
            receipt = dict(account=j['account'], group=j['room'], message_id=str(j['id']),
                           text_sha256=j['answer_hash'], images=[])
            saved[j['id']] = receipt
            return receipt
        result = dispatch_parts(dict(op='send', job=job, text=text, images=['synthetic']), single)
        self.assertEqual(len(sends), 2)
        self.assertEqual(sends[0]['images'], ['synthetic'])
        self.assertEqual(sends[1]['images'], [])
        self.assertIsNone(sends[1]['job']['attachments'])
        self.assertEqual(result['text_sha256'], job['answer_hash'])
        self.assertFalse(result['recipient_verified'])
        self.assertEqual(dispatch_parts(dict(op='receipt', job=job), single), result)
        self.assertEqual(len(sends), 2)
        saved.pop(sends[1]['job']['id'])
        self.assertIsNone(dispatch_parts(dict(op='receipt', job=job), single))
        self.assertEqual(len(sends), 2)

    def test_existing_legacy_receipt_never_replays(self):
        legacy = {'message_id': 'old'}
        calls = []
        def single(req):
            calls.append(req['op'])
            return legacy
        self.assertIs(dispatch_parts(dict(op='send', job={'id': 1}, text='https://example.com/a'), single), legacy)
        self.assertEqual(calls, ['receipt'])

    def test_partial_failure_does_not_claim_success(self):
        calls = []
        def single(req):
            if req['op'] == 'receipt': return None
            calls.append(req)
            if len(calls) == 2: raise RuntimeError('native_delivery_uncertain')
            j = req['job']
            return dict(account='a', group='g', message_id='1', text_sha256=j['answer_hash'])
        with self.assertRaisesRegex(RuntimeError, 'native_delivery_uncertain'):
            dispatch_parts(dict(op='send', job=dict(id=1, account='a', room='g'), text='摘要 https://example.com/a'), single)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__': unittest.main()
