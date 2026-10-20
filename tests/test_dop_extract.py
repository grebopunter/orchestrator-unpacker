"""Tests against a synthetic .orc file built here (no copyrighted songs)."""

import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dop_extract as dx  # noqa: E402


def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def mtrk(events):
    body = b"".join(events) + b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def meta(delta, mtype, payload):
    return vlq(delta) + bytes([0xFF, mtype]) + vlq(len(payload)) + payload


def voy(key, value):
    return meta(0, 0x7F, b"\x03\x00" + bytes([key]) + value)


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def build_orc():
    conductor = mtrk([meta(0, 0x51, (500000).to_bytes(3, "big")),
                      meta(0, 0x58, b"\x04\x02\x18\x08"),
                      meta(960, 0x51, (600000).to_bytes(3, "big"))])
    piano = mtrk([voy(0x01, b"\x00\x00"), meta(0, 0x03, b"piano"),
                  b"\x00\xb0\x00\x05",          # bank select MSB 5
                  b"\x00\xc0\x10",              # program 16
                  b"\x00\x90\x3c\x64",
                  b"\x83\x60\x80\x3c\x40"])     # 480 ticks later
    aud1 = mtrk([voy(0x01, b"\x00\x01"), voy(0x14, b"\x01"),
                 voy(0x08, b"\x20"), voy(0x09, b"\x00"),
                 meta(0, 0x03, b"left")])
    aud2 = mtrk([voy(0x01, b"\x00\x02"), voy(0x03, b"\x01"), voy(0x14, b"\x01"),
                 meta(0, 0x03, b"right")])
    midi = (b"MThd" + struct.pack(">IHHH", 6, 1, 4, 480)
            + conductor + piano + aud1 + aud2)

    # three blocks of constant sample values 100, 200, 300 (stored out of order)
    lens = {1: 10, 2: 6, 3: 8}
    vals = {1: 100, 2: 200, 3: 300}
    order = [2, 3, 1]
    pcm = b""
    starts = {}
    for b in order:
        starts[b] = len(pcm) // 2
        pcm += struct.pack("<%dh" % lens[b], *([vals[b]] * lens[b]))
    v = b"\x00\x0b" + vlq(3)
    for b in (1, 2, 3):
        npk = 1
        v += vlq(starts[b]) + vlq(lens[b]) + vlq(npk) + vlq(100) + b"\x00" * 4 * npk
    v += b"\x00" + vlq(2)
    track1 = [(5, 0), (0, 1), (9, 2), (4, 3)]  # silence, full, slot>len, slot<len
    track2 = [(0, 3)]
    for t in (track1, track2):
        v += vlq(len(t)) + b"".join(vlq(a) + vlq(b) for a, b in t)
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16) + b"\x00\x00"
    wavebody = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"voyl", v) + chunk(b"data", pcm)
    rmid = b"RMID" + chunk(b"data", midi)
    return chunk(b"RIFF", rmid) + chunk(b"RIFF", wavebody), midi


def read_wav(path):
    with wave.open(path) as w:
        n = w.getnframes()
        return list(struct.unpack("<%dh" % n, w.readframes(n)))


class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data, cls.midi = build_orc()
        cls.src = os.path.join(cls.tmp.name, "song.orc")
        with open(cls.src, "wb") as f:
            f.write(data)
        cls.out, cls.warnings, cls.report = dx.convert(cls.src)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_output_folder_named_after_file(self):
        self.assertEqual(self.out, os.path.join(self.tmp.name, "song"))
        self.assertEqual(self.warnings, [])

    def test_midi_is_byte_exact(self):
        with open(os.path.join(self.out, "song.mid"), "rb") as f:
            self.assertEqual(f.read(), self.midi)

    def test_clip_layout(self):
        got = read_wav(os.path.join(self.out, "02 left.wav"))
        want = [0] * 5 + [100] * 10 + [200] * 6 + [0] * 3 + [300] * 4
        self.assertEqual(got, want)
        self.assertEqual(read_wav(os.path.join(self.out, "03 right.wav")),
                         [300] * 8)

    def test_rpp(self):
        with open(os.path.join(self.out, "song.RPP"), encoding="utf-8") as f:
            rpp = f.read()
        depth = 0
        for line in rpp.splitlines():
            s = line.strip()
            if s.startswith("<"):
                depth += 1
            elif s == ">":
                depth -= 1
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)
        self.assertIn('NAME "piano"', rpp)
        self.assertIn("E 0 b0 00 05", rpp)          # bank select kept
        self.assertIn("E 0 c0 10", rpp)             # program kept
        self.assertIn('FILE "02 left.wav"', rpp)
        self.assertIn("VOLPAN 0.5 -1 -1 -1 1", rpp)  # vol 32, pan hard left
        self.assertIn("PT 0 120 1 262148", rpp)      # tempo map with 4/4
        self.assertIn("PT 1 100 1", rpp)             # 960 ticks at 120 = 1 s
        self.assertLess(rpp.index('"piano"'), rpp.index('"left"'))
        right = rpp[rpp.index('NAME "right"'):]
        left = rpp[rpp.index('NAME "left"'):rpp.index('NAME "right"')]
        self.assertIn("MUTESOLO 1 0 0", right)       # muted in DOP
        self.assertNotIn("MUTESOLO", left)

    def test_folder_batch(self):
        with tempfile.TemporaryDirectory() as out:
            rc = dx.main([self.tmp.name, "--out", out, "-q"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(out, "song", "song.RPP")))


if __name__ == "__main__":
    unittest.main()
