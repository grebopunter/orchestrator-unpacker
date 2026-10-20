# The Digital Orchestrator .orc format

Notes on the project file format of Voyetra / Turtle Beach **Digital
Orchestrator Plus / Pro** (Windows, mid-1990s), as decoded for dop-extract
from 20 sample files: Voyetra's own demo songs and user projects at 11,025,
22,050 and 44,100 Hz with 1–45 audio tracks.

All of this was worked out by examining files.  Anything marked *assumed*
was not confirmed.

## Container

An .orc file is two RIFF forms written back to back:

```
RIFF 'RMID'
  data        standard MIDI file (MThd/MTrk), format 1
RIFF 'WAVE'
  fmt         plain PCM WAVEFORMATEX (all samples: mono, 16-bit)
  voyl        Voyetra chunk: audio block table, overviews, clip lists
  data        all recorded audio for the whole project, as one PCM stream
```

## MIDI part

A normal type-1 MIDI file (480 ticks per quarter note in every sample).
Track 0 holds tempo (FF 51), time signature (FF 58), SMPTE offset and
key signature.  Each further MTrk is one Digital Orchestrator track, *audio
tracks included* — those carry no channel events, only settings.

Track settings are stored as sequencer-specific meta events
`FF 7F len 03 00 <id> <value...>` at tick 0:

| id   | meaning                                           |
|------|---------------------------------------------------|
| 0x01 | track number (2 bytes; not always contiguous)     |
| 0x14 | `01` = this track is a digital-audio track        |
| 0x03 | `01` = track muted (seen on audio tracks only)   |
| 0x08 | volume, 0–127 (new audio tracks start at 64)      |
| 0x09 | pan, 0 = left, 64 = centre, 127 = right           |
| 0x02, 0x04, 0x07, 0x0a, 0x0b, 0x15 | not decoded |
| 0x06, 0x0c, 0x0d, 0x1d | on track 0 only; not decoded          |

Bank select (CC0/CC32) and program changes are ordinary channel events and
come through untouched.  An empty meta event type 0x2E appears on audio
tracks in some files.

Volume 64 is taken to be unity gain, on a linear scale (*assumed*).

## WAVE `data`

Every recording in the project, concatenated.  It's divided into
**blocks**, listed in the `voyl` chunk.  Recordings are cut into blocks of
at most 4 seconds at 44.1 kHz (176,400 frames); edits create shorter
blocks.  Blocks are not necessarily stored in playing order.

## `voyl` chunk

Numbers marked VLQ are big-endian base-128, continuation bit 0x80, exactly
like MIDI delta times.

```
u16 BE   version            0x000B in all samples
VLQ      block count  N
N times:
  VLQ    start              first frame of the block in WAVE data
  VLQ    length             frames
  VLQ    overview count  P
  VLQ    overview factor    100 in all samples (frames per overview point)
  P*4    overview           min/max sample pairs for drawing; not needed
u8       0                  0 in all samples
VLQ      audio track count  T   (equals the number of MIDI tracks with id 0x14 = 01)
T times:
  VLQ    entry count  E
  E times:
    VLQ  a
    VLQ  b
```

Audio tracks in this list are in the same order as the audio tracks in the
MIDI file.

Each track's entries are played back to back starting at song time 0:

| entry        | meaning                                                     |
|--------------|-------------------------------------------------------------|
| `(a, 0)`     | `a` frames of silence                                       |
| `(0, b)`     | block `b` (1-based), its full length                        |
| `(a, b)`     | block `b` in a slot of `a` frames: the block plays, silence fills the rest of the slot |

In the samples `a` is never shorter than its block.  If it were,
dop-extract plays only the first `a` frames.

How this was checked:

- The parse uses every byte of the chunk in all 20 samples.
- Every block is used by at least one track.
- Left/right halves of stereo pairs (same name, pan 0 and 127) correlate at
  zero offset (up to r = 1.000) when laid out this way.
- Loop lengths fall exactly on the beat at the song's MIDI tempo; for
  example, 21,168 frames is one beat at 125 BPM.
- Against recordings of Voyetra's demo songs played in Digital Orchestrator
  (DEMOSONG, DEMO_X), clips land where they are heard; reading `(a, b)` as
  "a frames of silence, then block b" instead puts them about a second late.

The mute flag (0x03) was identified from user projects: it marks the raw
loop/source tracks whose material was copied onto the arranged tracks,
and never appears in the demo songs, where every track is heard.

## Not yet decoded

- The single byte after the block table (always 0).
- The remaining sequencer-specific setting ids listed above.
- Whether stereo recordings exist; every sample is mono.  dop-extract
  handles any channel count and bit depth the `fmt` chunk declares.
