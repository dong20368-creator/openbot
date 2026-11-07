"""Synthetic WAL tests; no real user data."""
import struct
import unittest
from db_reader import PAGE,checksum,committed_image

class WALTests(unittest.TestCase):
    def wal(self,frames):
        salt=b'12345678'; header=struct.pack('>IIII',0x377f0682,3007000,PAGE,0)+salt
        seed=checksum(header); result=header+struct.pack('>II',*seed)
        for page,commit,payload in frames:
            h=struct.pack('>II',page,commit)+salt; seed=checksum(h[:8]+payload,seed)
            result+=h+struct.pack('>II',*seed)+payload
        return result
    def test_only_committed_transaction_visible(self):
        wal=self.wal([(1,2,b'C'*PAGE),(2,0,b'D'*PAGE)])
        self.assertEqual(committed_image(b'A'*PAGE+b'B'*PAGE,wal),b'C'*PAGE+b'B'*PAGE)
    def test_truncated_and_corrupt_tail_not_applied(self):
        wal=self.wal([(1,1,b'B'*PAGE),(1,1,b'C'*PAGE)])
        self.assertEqual(committed_image(b'A'*PAGE,wal[:-1]),b'B'*PAGE)
        bad=bytearray(wal); bad[-1]^=1
        self.assertEqual(committed_image(b'A'*PAGE,bytes(bad)),b'B'*PAGE)
    def test_bad_header_rejected(self):
        bad=bytearray(self.wal([(1,1,b'A'*PAGE)])); bad[24]^=1
        with self.assertRaises(ValueError): committed_image(b'A'*PAGE,bytes(bad))
if __name__=='__main__': unittest.main()
