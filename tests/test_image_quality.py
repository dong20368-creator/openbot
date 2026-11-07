import io,json,tempfile,unittest
from pathlib import Path
from PIL import Image
from media_reader import best_image,image_dimensions
from codex_client import checked_reply

class ImageQuality(unittest.TestCase):
    def test_headers(self):
        for fmt in ('JPEG','PNG','GIF','WEBP'):
            b=io.BytesIO();Image.new('RGB',(320,640)).save(b,fmt)
            self.assertEqual(image_dimensions(b.getvalue()),(320,640),fmt)
        for raw in (b'',b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'not an image'):
            self.assertIsNone(image_dimensions(raw))

    def test_selects_pixels_not_order_or_file_size(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'runtime') as tmp:
            root=Path(tmp).resolve();thumb=root/'thumb.jpg';full=root/'full.jpg';bad=root/'bad.dat'
            Image.effect_noise((96,210),100).convert('RGB').save(thumb,quality=100)
            Image.new('RGB',(1206,2622)).save(full,quality=20)
            bad.write_bytes(b'invalid')
            for paths in ([thumb,bad,full],[full,thumb,bad]):
                p,raw,size=best_image(paths,root,object(),{})
                self.assertEqual(p,full);self.assertEqual(size,(1206,2622))
            self.assertEqual(best_image([thumb],root,object(),{})[2],(96,210))
            with self.assertRaises(ValueError):best_image([bad],root,object(),{})

    def test_does_not_claim_missing_download_when_image_present(self):
        raw=json.dumps({'category':'unavailable','unavailable_reason':'missing_material','answer':'unreadable'})
        present=checked_reply(raw,[],'now',material_expected=True,media_available=True)
        absent=checked_reply(raw,[],'now',material_expected=True)
        self.assertIn('已取得',present);self.assertNotIn('请确认材料已下载',absent)
        self.assertNotIn('没有读到',present)

if __name__=='__main__':unittest.main()
