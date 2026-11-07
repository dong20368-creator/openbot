import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from PIL import Image
from core import Store,Message,Worker
from test_core import Model,Sender
from media_context import prepare,document_text,MediaContext
from media_reader import decode_image
from semantic_memory import SemanticRecall

class CapabilityTests(unittest.TestCase):
    def test_image_normalization_strips_metadata_and_handles_animation(self):
        out=io.BytesIO();Image.new('RGB',(80,60),'red').save(out,format='PNG')
        result=prepare(out.getvalue(),'image','picture.png',with_ocr=False)
        self.assertEqual(len(result['inputs']),1)
        self.assertTrue(result['inputs'][0]['url'].startswith('data:image/jpeg;base64,'))
        out=io.BytesIO();Image.new('RGB',(40,40),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(40,40),'blue')],duration=200)
        self.assertEqual(len(prepare(out.getvalue(),'sticker','a.gif',False)['inputs']),2)

    def test_legacy_xor_and_corrupt_dat(self):
        raw=b'\x89PNG\r\n\x1a\nsynthetic'
        self.assertEqual(decode_image(bytes(b^83 for b in raw)),raw)
        with self.assertRaises(ValueError): decode_image(b'\x07\x08V2\x08\x07broken')

    def test_document_formats_and_formula_caches(self):
        from docx import Document
        from openpyxl import Workbook
        doc=Document();doc.add_paragraph('测试文档正文');out=io.BytesIO();doc.save(out)
        self.assertIn('测试文档正文',document_text(out.getvalue(),'test.docx')[0])
        wb=Workbook();wb.active.append(['预算',3500]);out=io.BytesIO();wb.save(out)
        self.assertIn('3500',document_text(out.getvalue(),'test.xlsx')[0])
        self.assertIn('测试',document_text('测试'.encode(),'a.txt')[0])
        with self.assertRaises(ValueError): document_text(b'anything','a.exe')

    def test_pdf_has_images_and_clear_coverage(self):
        out=io.BytesIO();Image.new('RGB',(100,100),'green').save(out,format='PDF')
        result=prepare(out.getvalue(),'file','test.pdf',False)
        self.assertEqual(len(result['inputs']),1)
        self.assertTrue(any('前3页' in x.get('notice','') for x in result['notes']))

    def test_no_audio_processing(self):
        with self.assertRaises(ValueError): prepare(b'#!SILK_V3','audio','voice.silk')

    def test_video_frames_without_sound(self):
        import av
        out=io.BytesIO()
        with av.open(out,'w',format='mp4') as container:
            stream=container.add_stream('mpeg4',rate=5);stream.width=64;stream.height=64;stream.pix_fmt='yuv420p'
            for i in range(10):
                frame=av.VideoFrame.from_image(Image.new('RGB',(64,64),'blue'))
                for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode():container.mux(packet)
        content=prepare(out.getvalue(),'video','test.mp4',False)
        self.assertGreater(len(content['inputs']),0)
        self.assertTrue(all(i['type']=='image' for i in content['inputs']))
        with self.assertRaises(ValueError): prepare(b'#EXTM3U\nhttps://example.com','video','a.m3u8')

    def test_native_quote_receipt_parser(self):
        from inbox import text_and_reference
        text,ref=text_and_reference({'local_type':49,'message_content':'<msg><appmsg><type>57</type><title>答复</title><refermsg><svrid>123</svrid></refermsg></appmsg></msg>'})
        self.assertEqual((text,ref),('答复','s:123'))

    def test_slide_text(self):
        import zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('ppt/slides/slide1.xml','<p xmlns:a="urn:a"><a:t>项目目标</a:t></p>')
        self.assertIn('项目目标',document_text(data.getvalue(),'test.pptx')[0])

    def test_cross_group_media_never_launches_reader(self):
        source=dict(account='bot',group='wrong',media_kind='image',message_id='s:1')
        with patch('media_context.subprocess.run') as run:
            with self.assertRaises(ValueError): MediaContext()(dict(account='bot',room='room',reference_data=json.dumps(source)))
            run.assert_not_called()

    def test_semantic_recall_privacy_and_forget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp, closing(Store(Path(tmp)/'state.db','bot',{'room'},100)) as store:
            for i,(person,text) in enumerate([('alice','我不吃花生，吃了会过敏'),('bob','我的银行卡密码不能读取')]):
                store.ingest(Message('bot','room',str(i),person,('bot',),text,101),now=102)
                Worker(store,Model(),Sender(),'send').run_one()
            store.ingest(Message('bot','room','next','alice',('bot',),'哪些食材对我有风险',102),now=103)
            job=store.claim(); recall=SemanticRecall(store)
            result=recall(job)
            self.assertTrue(any('花生' in r['question'] for r in result))
            self.assertTrue(all(r['sender']=='alice' for r in result))
            self.assertGreater(store.db.execute('SELECT count(*) FROM semantic_chunks').fetchone()[0],0)
            store.forget('room','alice')
            self.assertEqual(store.db.execute('SELECT count(*) FROM semantic_chunks').fetchone()[0],0)
            self.assertEqual(recall(job),[])

    def test_temporal_facts_keep_old_version_without_replacing_current(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            store=Store(Path(tmp)/'state.db','bot',{'room'},100)
            for i,value in enumerate(['3500','5000']):
                store.ingest(Message('bot','room',str(i),'alice',('bot',),'预算'+value,101+i),now=103)
                Worker(store,Model(),Sender(),'send').run_one()
                row=dict(store.db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone())
                store.remember(row,'预算',value)
            self.assertEqual(store.db.execute('SELECT value FROM facts').fetchone()[0],'5000')
            self.assertEqual(store.db.execute('SELECT count(*) FROM fact_events').fetchone()[0],2)
            store.forget('room','alice')
            self.assertEqual(store.db.execute('SELECT count(*) FROM fact_events').fetchone()[0],0);store.close()

if __name__=='__main__': unittest.main()
