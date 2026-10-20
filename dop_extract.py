#!/usr/bin/env python3
"""dop-extract: convert Voyetra / Turtle Beach Digital Orchestrator Plus/Pro
.orc projects into plain MIDI + WAV files and a REAPER project (.RPP).

For every input file  song.orc  a folder  song/  is written containing:

    song.mid                the project's MIDI data, byte-for-byte as stored
                            (bank select and program changes kept as-is)
    NN <track name>.wav     one WAV per audio track, rendered to full song
                            time (clips placed, gaps filled with silence),
                            so every file lines up when dropped in at 0:00;
                            tracks muted in Digital Orchestrator are
                            extracted too and come into REAPER muted
    song.RPP                a REAPER project with the tempo map, every MIDI
                            track (no instruments loaded) and every audio
                            track with its volume and pan
    conversion-report.txt   what was found and anything that looked wrong

Only the Python 3 standard library is used.  See FORMAT.md for the file
format as decoded for this tool.

Usage:
    python dop_extract.py FILE_OR_FOLDER [...] [--out DIR] [--recursive]
                          [--extract-only]
"""

import argparse
import base64
import math
import os
import struct
import sys
import wave

__version__ = "1.2.0"


# --------------------------------------------------------------------------
# small helpers

class OrcError(Exception):
    pass


class Cancelled(Exception):
    """Raised by a progress callback to stop a conversion part-way."""


def read_vlq(buf, pos):
    """Big-endian base-128 number (as used by MIDI and by the voyl chunk)."""
    value = 0
    while True:
        if pos >= len(buf):
            raise OrcError("number runs past end of data")
        c = buf[pos]
        pos += 1
        value = (value << 7) | (c & 0x7F)
        if c < 0x80:
            return value, pos


def safe_name(name, fallback):
    bad = '<>:"/\\|?*'
    out = "".join("_" if (ch in bad or ord(ch) < 32) else ch for ch in name)
    out = out.strip().rstrip(".")
    return out or fallback


def rpp_str(s):
    """Quote a string for an RPP file."""
    if '"' not in s:
        return '"%s"' % s
    if "'" not in s:
        return "'%s'" % s
    return '"%s"' % s.replace('"', "'")


def fmt_num(x):
    s = "%.10f" % x
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# --------------------------------------------------------------------------
# RIFF container

def riff_chunks(data, start, end):
    """List of (id, payload_offset, size, form_or_None) at one level."""
    out = []
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if body + size > len(data):
            size = len(data) - body  # truncated file: take what is there
        form = data[body:body + 4] if cid in (b"RIFF", b"LIST") else None
        out.append((cid, body, size, form))
        pos = body + size + (size & 1)
    return out


# --------------------------------------------------------------------------
# MIDI

class MidiTrack:
    def __init__(self, index):
        self.index = index
        self.name = ""
        self.events = []       # (abs_tick, bytes) channel + sysex events
        self.voyetra = {}      # sequencer-specific 03 00 <id> settings
        self.end_tick = 0
        self.tempos = []       # (tick, usec_per_quarter)
        self.timesigs = []     # (tick, num, denom)
        self.dropped_sysex = 0

    @property
    def is_audio(self):
        return self.voyetra.get(0x14, b"\x00")[:1] == b"\x01"

    @property
    def muted(self):
        return self.voyetra.get(0x03, b"\x00")[-1:] == b"\x01"

    def setting(self, key):
        v = self.voyetra.get(key)
        return v[-1] if v else None


def parse_midi(m):
    if m[:4] != b"MThd":
        raise OrcError("MIDI data does not start with MThd")
    hlen = struct.unpack(">I", m[4:8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", m[8:14])
    if division & 0x8000:
        raise OrcError("SMPTE-timed MIDI is not supported")
    pos = 8 + hlen
    tracks = []
    for t in range(ntracks):
        if m[pos:pos + 4] != b"MTrk":
            raise OrcError("MIDI track %d header missing" % t)
        tlen = struct.unpack(">I", m[pos + 4:pos + 8])[0]
        b = m[pos + 8:pos + 8 + tlen]
        pos += 8 + tlen
        tr = MidiTrack(t)
        i = 0
        tick = 0
        running = 0
        while i < len(b):
            delta, i = read_vlq(b, i)
            tick += delta
            status = b[i]
            if status == 0xFF:
                mtype = b[i + 1]
                ln, j = read_vlq(b, i + 2)
                payload = b[j:j + ln]
                i = j + ln
                if mtype == 0x03 and not tr.name:
                    tr.name = payload.decode("latin-1")
                elif mtype == 0x51 and ln == 3:
                    tr.tempos.append((tick, int.from_bytes(payload, "big")))
                elif mtype == 0x58 and ln >= 2:
                    tr.timesigs.append((tick, payload[0], 2 ** payload[1]))
                elif mtype == 0x7F and payload[:2] == b"\x03\x00" and ln >= 3:
                    tr.voyetra[payload[2]] = payload[3:]
                elif mtype == 0x2F:
                    break
            elif status in (0xF0, 0xF7):
                ln, j = read_vlq(b, i + 1)
                raw = b[j:j + ln]
                i = j + ln
                if status == 0xF0:
                    tr.events.append((tick, b"\xF0" + raw))
                else:
                    tr.dropped_sysex += 1
            else:
                if status & 0x80:
                    running = status
                    i += 1
                if not running:
                    raise OrcError("bad MIDI data in track %d" % t)
                n = 1 if (running & 0xF0) in (0xC0, 0xD0) else 2
                tr.events.append((tick, bytes([running]) + b[i:i + n]))
                i += n
        tr.end_tick = tick
        tracks.append(tr)
    return fmt, division, tracks


class TempoMap:
    def __init__(self, tracks, division):
        self.division = division
        tempos = sorted(t for tr in tracks for t in tr.tempos)
        sigs = sorted(s for tr in tracks for s in tr.timesigs)
        if not tempos or tempos[0][0] != 0:
            tempos.insert(0, (0, 500000))
        if not sigs or sigs[0][0] != 0:
            sigs.insert(0, (0, 4, 4))
        dedup = {}
        for tick, us in tempos:
            dedup[tick] = us
        self.tempos = sorted(dedup.items())
        dsig = {}
        for tick, num, den in sigs:
            dsig[tick] = (num, den)
        self.sigs = sorted((k, v[0], v[1]) for k, v in dsig.items())

    def seconds(self, tick):
        sec = 0.0
        prev_tick, prev_us = 0, self.tempos[0][1]
        for t, us in self.tempos[1:]:
            if t >= tick:
                break
            sec += (t - prev_tick) * prev_us / 1e6 / self.division
            prev_tick, prev_us = t, us
        return sec + (tick - prev_tick) * prev_us / 1e6 / self.division

    @staticmethod
    def bpm(us):
        return 60000000.0 / us


# --------------------------------------------------------------------------
# audio (WAVE + voyl)

class AudioPool:
    def __init__(self):
        self.channels = 1
        self.rate = 44100
        self.bits = 16
        self.block_align = 2
        self.pcm = b""
        self.blocks = [None]   # 1-based: (start_frame, length_frames)
        self.tracks = []       # list of [(a, b), ...] per audio track
        self.version = None


def parse_audio(data, wave_body, wave_size, warnings):
    pool = AudioPool()
    voyl = None
    for cid, body, size, _ in riff_chunks(data, wave_body + 4,
                                         wave_body + wave_size):
        if cid == b"fmt ":
            tag, ch, rate, _, align, bits = struct.unpack(
                "<HHIIHH", data[body:body + 16])
            if tag != 1:
                raise OrcError("audio is not plain PCM (format %d)" % tag)
            pool.channels, pool.rate, pool.bits = ch, rate, bits
            pool.block_align = align or ch * bits // 8
        elif cid == b"voyl":
            voyl = data[body:body + size]
        elif cid == b"data":
            pool.pcm = data[body:body + size]
    if voyl is None:
        raise OrcError("WAVE section has no voyl chunk")

    pool.version = voyl[:2].hex()
    nblocks, i = read_vlq(voyl, 2)
    total_frames = len(pool.pcm) // pool.block_align
    for _ in range(nblocks):
        start, i = read_vlq(voyl, i)
        length, i = read_vlq(voyl, i)
        npeaks, i = read_vlq(voyl, i)
        _factor, i = read_vlq(voyl, i)
        i += npeaks * 4            # waveform overview for drawing: skipped
        if start + length > total_frames:
            warnings.append("audio block %d runs past the end of the audio "
                            "data; cut short" % len(pool.blocks))
            length = max(0, total_frames - start)
        pool.blocks.append((start, length))
    i += 1                         # one byte, 0 in every known file
    ntracks, i = read_vlq(voyl, i)
    for _ in range(ntracks):
        count, i = read_vlq(voyl, i)
        entries = []
        for _ in range(count):
            a, i = read_vlq(voyl, i)
            b, i = read_vlq(voyl, i)
            entries.append((a, b))
        pool.tracks.append(entries)
    if i != len(voyl):
        warnings.append("%d unexplained bytes at the end of the voyl chunk"
                        % (len(voyl) - i))
    return pool


def render_track(pool, entries, warnings, label):
    """Lay one audio track out on the song timeline from 0:00.

    (n, 0)  n frames of silence
    (0, b)  block b, full length
    (n, b)  block b in an n-frame slot (silence fills any remainder;
            if n is shorter than the block, only the first n frames play)
    """
    ba = pool.block_align
    silence_byte = b"\x80" if pool.bits == 8 else b"\x00"
    out = bytearray()
    for a, b in entries:
        if b == 0:
            out += silence_byte * (a * ba)
            continue
        if b >= len(pool.blocks):
            warnings.append("%s refers to audio block %d, which does not "
                            "exist; left silent" % (label, b))
            out += silence_byte * (a * ba)
            continue
        start, length = pool.blocks[b]
        slot = a if a else length
        play = min(slot, length)
        out += pool.pcm[start * ba:(start + play) * ba]
        if slot > play:
            out += silence_byte * ((slot - play) * ba)
    # drop trailing silence so files end at the last sound
    end = len(out)
    zero = silence_byte[0]
    while end > 0 and out[end - 1] == zero:
        end -= 1
    end -= end % ba
    return bytes(out[:end])


def write_wav(path, pool, frames_bytes):
    with wave.open(path, "wb") as w:
        w.setnchannels(pool.channels)
        w.setsampwidth(pool.bits // 8)
        w.setframerate(pool.rate)
        w.writeframes(frames_bytes)


# --------------------------------------------------------------------------
# REAPER project

def volume_gain(v):
    """Voyetra audio track volume (0-127, new tracks start at 64) to gain.
    64 is taken as unity; the scale is assumed linear."""
    if v is None:
        return 1.0
    return v / 64.0


def pan_value(p):
    """Voyetra pan 0 (left) .. 64 (centre) .. 127 (right) to REAPER -1..1."""
    if p is None:
        return 0.0
    if p >= 64:
        return min(1.0, (p - 64) / 63.0)
    return max(-1.0, (p - 64) / 64.0)


def midi_source_lines(track, division, end_tick, indent):
    lines = [indent + "<SOURCE MIDI",
             indent + "  HASDATA 1 %d QN" % division,
             indent + "  CCINTERP 32"]
    last = 0
    for tick, ev in sorted(track.events, key=lambda e: e[0]):
        delta = tick - last
        last = tick
        if ev[0] == 0xF0:
            lines.append(indent + "  <X %d 0" % delta)
            lines.append(indent + "    " + base64.b64encode(ev).decode())
            lines.append(indent + "  >")
        else:
            lines.append(indent + "  E %d %s" % (
                delta, " ".join("%02x" % x for x in ev)))
    lines.append(indent + "  E %d b0 7b 00" % max(0, end_tick - last))
    lines.append(indent + "  IGNTEMPO 0 120 4 4")
    lines.append(indent + ">")
    return lines


def build_rpp(song, tmap, midi_tracks, audio_items, pool_rate):
    first_bpm = TempoMap.bpm(tmap.tempos[0][1])
    num, den = tmap.sigs[0][1], tmap.sigs[0][2]
    L = ['<REAPER_PROJECT 0.1 "6.0" 0',
         "  TITLE " + rpp_str(song),
         "  TEMPO %s %d %d" % (fmt_num(first_bpm), num, den),
         "  SAMPLERATE %d 0 0" % pool_rate]
    if len(tmap.tempos) > 1 or len(tmap.sigs) > 1:
        points = {}
        for tick, us in tmap.tempos:
            points.setdefault(tick, [None, None])[0] = us
        for tick, n, d in tmap.sigs:
            points.setdefault(tick, [None, None])[1] = (n, d)
        L += ["  <TEMPOENVEX", "    ACT 1 -1", "    VIS 1 0 1",
              "    LANEHEIGHT 0 0", "    ARM 0", "    DEFSHAPE 1 -1 -1"]
        cur_us = tmap.tempos[0][1]
        for tick in sorted(points):
            us, sig = points[tick]
            if us is not None:
                cur_us = us
            line = "    PT %s %s 1" % (fmt_num(tmap.seconds(tick)),
                                      fmt_num(TempoMap.bpm(cur_us)))
            if sig is not None:
                line += " %d" % (sig[0] + (sig[1] << 16))
            L.append(line)
        L.append("  >")

    for kind, data in sorted(midi_tracks + audio_items, key=lambda x: x[1]["order"]):
        L.append("  <TRACK")
        L.append("    NAME " + rpp_str(data["name"]))
        if kind == "midi":
            L.append("    VOLPAN 1 0 -1 -1 1")
            tr = data["track"]
            if tr.events:
                L += ["    <ITEM",
                      "      POSITION 0",
                      "      LENGTH %s" % fmt_num(data["length"]),
                      "      NAME " + rpp_str(data["name"])]
                L += midi_source_lines(tr, tmap.division, data["end_tick"],
                                       "      ")
                L.append("    >")
        else:
            L.append("    VOLPAN %s %s -1 -1 1" % (fmt_num(data["gain"]),
                                                  fmt_num(data["pan"])))
            if data["muted"]:
                L.append("    MUTESOLO 1 0 0")
            if data["length"] > 0:
                L += ["    <ITEM",
                      "      POSITION 0",
                      "      LENGTH %s" % fmt_num(data["length"]),
                      "      NAME " + rpp_str(data["name"]),
                      "      <SOURCE WAVE",
                      "        FILE " + rpp_str(data["file"]),
                      "      >",
                      "    >"]
        L.append("  >")
    L.append(">")
    return "\r\n".join(L) + "\r\n"


# --------------------------------------------------------------------------
# one file

def convert(path, out_root=None, make_rpp=True, progress=None):
    """Convert one .orc file.  progress, if given, is called with a
    fraction 0..1 as work proceeds; it may raise Cancelled to stop."""
    def tick(x):
        if progress:
            progress(x)

    tick(0.0)
    song = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(out_root or os.path.dirname(os.path.abspath(path)),
                           song)
    warnings = []
    report = ["dop-extract %s" % __version__, "Source: %s" % path, ""]

    with open(path, "rb") as f:
        data = f.read()
    top = riff_chunks(data, 0, len(data))
    rmid = next((c for c in top if c[0] == b"RIFF" and c[3] == b"RMID"), None)
    wav = next((c for c in top if c[0] == b"RIFF" and c[3] == b"WAVE"), None)
    if rmid is None:
        raise OrcError("not a Digital Orchestrator file (no RIFF RMID part)")
    midi_chunk = next((c for c in riff_chunks(data, rmid[1] + 4,
                                               rmid[1] + rmid[2])
                       if c[0] == b"data"), None)
    if midi_chunk is None:
        raise OrcError("no MIDI data chunk")
    midi_bytes = data[midi_chunk[1]:midi_chunk[1] + midi_chunk[2]]

    _fmt, division, tracks = parse_midi(midi_bytes)
    tmap = TempoMap(tracks, division)
    pool = parse_audio(data, wav[1], wav[2], warnings) if wav else None
    tick(0.05)

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, song + ".mid"), "wb") as f:
        f.write(midi_bytes)

    song_end_tick = max([t.end_tick for t in tracks] + [0])
    song_len = tmap.seconds(song_end_tick)
    report.append("Tempo: %s BPM, %d/%d, %d ticks per beat%s" % (
        fmt_num(round(TempoMap.bpm(tmap.tempos[0][1]), 3)),
        tmap.sigs[0][1], tmap.sigs[0][2], division,
        " (%d tempo changes)" % (len(tmap.tempos) - 1)
        if len(tmap.tempos) > 1 else ""))
    if pool:
        report.append("Audio: %d Hz, %d-bit, %s; %d recorded blocks" % (
            pool.rate, pool.bits,
            "mono" if pool.channels == 1 else "%d channels" % pool.channels,
            len(pool.blocks) - 1))
    report.append("")

    audio_tracks = [t for t in tracks if t.is_audio]
    if pool and len(audio_tracks) != len(pool.tracks):
        warnings.append("MIDI lists %d audio tracks but the audio section has "
                        "%d; matched in order" % (len(audio_tracks),
                                                  len(pool.tracks)))

    midi_items, audio_items = [], []
    used_files = set()
    report.append("Tracks:")
    for order, tr in enumerate(tracks):
        if tr.is_audio:
            k = audio_tracks.index(tr)
            name = tr.name or "Audio %d" % (k + 1)
            if not pool or k >= len(pool.tracks):
                warnings.append("audio track '%s' has no audio data" % name)
                continue
            label = "audio track '%s'" % name
            pcm = render_track(pool, pool.tracks[k], warnings, label)
            tick(0.05 + 0.9 * (k + 1) / max(1, len(audio_tracks)))
            base = "%02d %s" % (order, safe_name(name, "Audio"))
            fname = base + ".wav"
            n = 2
            while fname.lower() in used_files:
                fname = "%s (%d).wav" % (base, n)
                n += 1
            used_files.add(fname.lower())
            write_wav(os.path.join(out_dir, fname), pool, pcm)
            seconds = len(pcm) / pool.block_align / pool.rate
            song_len = max(song_len, seconds)
            vol, pan = tr.setting(0x08), tr.setting(0x09)
            audio_items.append(("audio", {
                "order": order, "name": name, "file": fname,
                "length": seconds, "gain": volume_gain(vol),
                "pan": pan_value(pan), "muted": tr.muted}))
            report.append("  %2d  audio  %-24s %7.1f s  %4d clip entries"
                          "  vol %s  pan %s%s  -> %s" % (
                              order, name, seconds, len(pool.tracks[k]),
                              vol if vol is not None else "-",
                              pan if pan is not None else "-",
                              "  MUTED" if tr.muted else "", fname))
        else:
            chans = sorted({ev[0] & 0x0F for _, ev in tr.events
                            if ev[0] < 0xF0})
            notes = sum(1 for _, ev in tr.events
                        if ev[0] & 0xF0 == 0x90 and len(ev) > 2 and ev[2])
            if tr.dropped_sysex:
                warnings.append("track %d: %d sysex continuation packets "
                                "left out of the RPP" % (order,
                                                         tr.dropped_sysex))
            if order == 0 and not tr.events and not tr.name:
                report.append("   0  tempo/conductor track")
                continue
            name = tr.name or "MIDI %d" % order
            midi_items.append(("midi", {
                "order": order, "name": name, "track": tr,
                "end_tick": song_end_tick, "length": 0.0}))
            report.append("  %2d  midi   %-24s %5d notes  channel %s" % (
                order, name, notes,
                ",".join(str(c + 1) for c in chans) or "-"))

    for _, d in midi_items:
        d["length"] = tmap.seconds(song_end_tick)

    if make_rpp:
        rpp = build_rpp(song, tmap, midi_items, audio_items,
                        pool.rate if pool else 44100)
        with open(os.path.join(out_dir, song + ".RPP"), "w",
                  encoding="utf-8", newline="") as f:
            f.write(rpp)

    report.append("")
    report.append("Song length: %.1f s" % song_len)
    report.append("Warnings:" if warnings else "Warnings: none")
    report += ["  - " + w for w in warnings]
    text = "\n".join(report) + "\n"
    with open(os.path.join(out_dir, "conversion-report.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)
    tick(1.0)
    return out_dir, warnings, text


# --------------------------------------------------------------------------
# command line

def find_inputs(paths, recursive):
    found = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    found += [os.path.join(root, f) for f in sorted(files)
                              if f.lower().endswith(".orc")]
            else:
                found += [os.path.join(p, f) for f in sorted(os.listdir(p))
                          if f.lower().endswith(".orc")]
        else:
            found.append(p)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dop_extract",
        description="Convert Digital Orchestrator Plus/Pro .orc files to "
                    "MIDI + WAV files and a REAPER project.")
    ap.add_argument("inputs", nargs="+",
                    help=".orc files and/or folders containing them")
    ap.add_argument("--out", metavar="DIR",
                    help="put the output folders here instead of next to "
                         "each .orc file")
    ap.add_argument("--recursive", "-r", action="store_true",
                    help="also look in subfolders of the folders given")
    ap.add_argument("--extract-only", action="store_true",
                    help="write the .mid and .wav files but no .RPP")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="print one line per file instead of the full report")
    ap.add_argument("--version", action="version",
                    version="%(prog)s " + __version__)
    args = ap.parse_args(argv)

    files = find_inputs(args.inputs, args.recursive)
    if not files:
        print("No .orc files found.")
        return 1
    failed = 0
    for path in files:
        try:
            out_dir, warnings, text = convert(path, args.out,
                                              not args.extract_only)
        except (OrcError, OSError, IndexError, struct.error) as e:
            failed += 1
            print("FAILED  %s: %s" % (path, e))
            continue
        if args.quiet:
            print("%s  %s -> %s" % ("WARN" if warnings else "OK  ",
                                    path, out_dir))
        else:
            print(text)
            print("Written to: %s" % out_dir)
            print("-" * 60)
    print("%d file(s) converted, %d failed." % (len(files) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
