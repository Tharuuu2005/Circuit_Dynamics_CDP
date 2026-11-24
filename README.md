# SDR Link 7: encrypted file transfer between two PCs over ANTSDR E200s

EN2130 Communication Design Project. Two PCs, one ANTSDR E200 each, BPSK FDD at 3300 / 3375 MHz.
**Main use:** sending files and folders from one PC to the other over the radio, encrypted,
checked on arrival, and continuing by themselves after a link drop. **Alongside:** messages
(text, photos, files) and 4 in a Row.

Built on the v5.3 engine: quiet FDD, Selective Repeat ARQ, CPU-light radio, `lean` profile and
the radio watchdog. Still BPSK only.

```
 PC 1 (node 1)                                                     PC 2 (node 2)
 app: Files · Messages · 4 in a Row · hidden radio-link panel      (the same app)
   files.py   512 KB blocks, SHA-256 per file, continue after drops
   core.py    radio + watchdog, outbox, telemetry (1 s CSV)
   transport  SR-ARQ (SACK, pacing, quiet FDD), every frame AES-256-GCM with addresses
   phy        framer ─ BPSK ─ E200    TX F1 3300 MHz  ───────────►  RX F1
                                      RX F2 3375 MHz  ◄───────────  TX F2
```

## 1. Install and start (each PC)

1. **Hardware:** connect the E200 by Ethernet, with the PC adapter set to `192.168.1.100 /
   255.255.255.0`.
2. **Copy the files:** put the `sdr_link_v7` folder on the PC.
3. **Run `Setup.bat` once.** It picks the Python to use, in this order:
   * your **own conda environment**, if you run `Setup.bat` from *Anaconda Prompt* after
     `conda activate <env>` and that environment has GNU Radio;
   * otherwise **radioconda**;
   * otherwise any Anaconda / Miniconda / Miniforge environment that has GNU Radio.

   It then adds the one missing package (`cryptography`) without touching numpy, Qt or GNU
   Radio, checks the E200, and puts an **SDR Link** shortcut on the desktop.
4. **Double-click `SDR Link.exe`.** The first start asks:
   * **Which node is this PC?** Make one PC node 1 and the other node 2.
   * **Pair the two PCs.** On one PC choose *Create a new pairing key* and *Save a copy for
     the other PC…* (a USB stick is fine). On the other PC choose *Load the key from the other
     PC…*. Both show the same **pairing code**.
5. **Wait for the link.** The radio starts by itself, and the link connects as soon as both
   PCs run.

If Windows blocks the unsigned `.exe`, use `Start SDR Link (no exe).bat`; it does the same.

**With the GRC flowgraph instead of the built-in radio (viva):**
1. Open `grc/sdr_link_phy_bpsk.grc`.
2. Set `node`, `duplex`, `f1` and `f2` to match the app.
3. Run it, then start `python app.py --no-radio`.

## 2. Using it

**Files (main page).**
* **Sending:** drop files or folders on the window, or press **Ctrl+O** (files) or
  **Ctrl+Shift+O** (a folder; it arrives as a `.zip`).
* **On the way:** shows progress and time left, with **Cancel**. If the link drops, the
  row says *Paused: link down*, and the file continues where it stopped when the link is
  back. Nothing has to be resent from the start.
* **Earlier:** every file sent or received is listed. *Delivered, verified* means the
  receiver's SHA-256 matched. There are **Open**, **Show in folder** and **Send again**
  buttons.
* **Where files go:** `data\received` (change it in Settings).
* **No link for a while:** a card says why, in plain words. For example: nothing heard from
  the other PC, lost contact, a different pairing key, or this PC's radio not running.

**Messages:** text with a ✓ when delivered. Attach photos or files with the paper clip
(**Ctrl+Shift+A**); photos show in the chat.

**4 in a Row:** **Ctrl+N** starts a new game, and **1–7** or a click drops a disc.

**Settings:**
* node number (with *Switch to node 1/2*);
* pairing code, and saving or loading the key;
* the folder for received files.

**Everywhere:** **Ctrl+1…4** switch pages, **F1** lists every shortcut, **F11** switches to
full screen.

## 3. The hidden radio-link panel: Ctrl+Shift+D

Nothing about the radio appears on the normal screens. Everything is here, over the middle of
the window (**Esc** closes it).

| tab | what it shows |
|---|---|
| **Overview** | **Node:** both addresses, pairing code. **Radio:** TX / RX frequency, modulation (BPSK, sample rate, samples per symbol, bit rate), RF bandwidth, duplex (and burst + listen mode), TX attenuation / RX gain, receiver and transmitter speed (× real time), TX queue, dropped frames, overflows / underflows, automatic restarts. **Link:** state, round trip (beacon, data, minimum, timeout), speed in and out (file data and on the air), pacing rate and queueing delay, frames getting through each way, first-try delivery, frame payload size, frames per second, totals (resent, timeouts, duplicates), rejected frames by reason. |
| **Graphs** | last 5 minutes, once a second: speed (data and on the air, each way), round trip, frames getting through each way, radio speed |
| **Transfers** | every file (size, seconds, KB/s, radio frames, frames resent, blocks sent again, result) and every radio transfer underneath (file blocks, messages, game moves) |
| **Log** | link, radio and transfer events. **All the numbers above are also saved every second** to `data\telemetry\<date>_node<N>.csv` (40 columns, opens in Excel) for your report. |
| **Radio** | F1 / F2 / TX attenuation / RX gain, applied live; profile and duplex for the next start; restart this PC's radio |

## 4. Requirements and where they are met

| requirement | how |
|---|---|
| Unique addressing | Each node has a random 32-bit address from the pairing. Every frame carries destination and source addresses in its authenticated header. Frames from any other address are dropped and counted. |
| Encryption of everything | AES-256-GCM on every frame: file data, ACKs, beacons, messages, game moves. Replay protection uses a per-frame counter and a saved start epoch (section 5). |
| Application | File transfer: files and folders, any size, SHA-256 per file, cancel, history, continues after link drops. Messages and 4 in a Row alongside. |
| Link back up automatically | The app starts the radio itself. The watchdog restarts it when the process dies, stops reporting, or the link stays down after it had worked. Transfers wait out a drop and continue with the missing frames. |
| Double-click executable | `SDR Link.exe` finds the right Python (`Setup.bat` notes it) and starts the app with no console window. It shows a clear message if something is missing. |
| Telecom details hidden but complete | The hidden panel (section 3) plus the 1-second telemetry CSV |
| Clear, modern UI | Files page with drag and drop, plain-language status, shortcuts for everything |
| BPSK only | unchanged radio |

## 5. How it works

**Encryption and addressing.**
```
on the air:  A5 | type | dst addr(4) | src addr(4) | epoch(4) | counter(4) | AES-GCM(frame) | tag(16)
```
* The key and both addresses come from `data\pairing.key`. The app never shows the key; the
  pairing code is a short hash that both PCs show for comparison.
* **Nonce:** source address | epoch | counter, which never repeats.
* **Receiver checks:** addresses, then the tag, then that the frame is new (a 4096-frame
  window; older start epochs are rejected). A recorded frame played back is dropped, also
  after a restart.
* **Cost:** 32 bytes per frame. File transfer measured about 105–108 KB/s with encryption on.

**File transfer (`files.py`).**
* **Blocks:** a file goes as 512 KB blocks, each one verified transfer, two on the air at a
  time, so the link stays full between blocks.
* **Offer first:** the receiver learns the name, size and SHA-256 before the blocks arrive,
  and writes them into a `.part` file. When everything is there it checks the SHA-256, saves
  the file and replies *done*.
* **Link drop:** the transport stops sending while it hears nothing (sending would only be
  lost) and keeps its transfers. When the link is back it sends only the missing frames.
  Measured: 4 MB file, link cut for 25 s at 35 %, finished with 1.09× the frames of one copy.
* **Restart mid-file:** if the receiving app was restarted, it picks the file up from its
  saved state.

**Transport changes since v5.3:**
* no sending while the link is down;
* the give-up clock only runs while the link is up;
* a drop does not count as a lossy link (frame size stays at 977 bytes);
* the ACK clocks restart when the link comes back.

## 6. Test results (no radios; `python tests/<name>.py`)

| test | result |
|---|---|
| `files_test.py`: 5 items (text, empty file, photo, 3 MB, a folder), link cut mid-file for 25 s, cancel; encrypted, 2 % frame loss | PASS. 3 MB at **104 KB/s** of 125 raw. After the drop the file continued with 0 blocks resent (1.09× frames). Cancel removes the partial file at the receiver. |
| `secure_test.py`: encryption, tampering, wrong key, replay (also after restarts) | PASS. 1 MB + 0.5 MB both ways at 108 KB/s; 20 replayed frames all rejected |
| `e2e_sim_test.py`: through the **real GNU Radio modem**, encrypted, files both ways + messages | PASS. 0 rejected frames, messages 94 ms median (sim at ¼ of the hardware rate) |
| `arq_emulated_test.py` | PASS, 116 KB/s (4 MB) with 115 KB/s the other way at the same time |
| `deaf_emulated_test.py` | PASS, quiet FDD 101–116 KB/s both ways with a self-deafening radio |
| `phy_burst_test.py`, `phy_per_test.py`, `game_test.py`, `watchdog_test.py` | PASS |
| CLI over the emulated link | 1.4 MB file at 107 KB/s, byte-identical |

Not run here: the `.exe` and `.bat` files on Windows, and the real E200s.

## 7. Troubleshooting

| what you see | what to do |
|---|---|
| *Nothing heard from the other PC yet* | Is SDR Link running on the other PC? Is one PC node 1 and the other node 2 (Settings)? Same pairing code? Then Ctrl+Shift+D on both: same F1 / F2, profile and duplex; *Radio health* should read about 1.00× |
| *The other PC uses a different pairing key* | Settings: save the key on one PC, load it on the other |
| *This PC's radio isn't running* | E200 plugged in and switched on? Ctrl+Shift+D → Log shows why (for example *Unable to create buffer*: another program holds the E200) |
| Radio health red: *transmitter below 1.00×* | Ctrl+Shift+D → Radio → profile `lean` on both PCs, save, restart both apps |
| One direction slow | Ctrl+Shift+D → Overview: *frames getting through*. Raise TX attenuation on the radio that receives badly, separate the antennas (appendix A) |
| *radioconda was not found* / *The app stopped at start* | Run `Setup.bat` (from Anaconda Prompt with your environment active, if you use your own); details in `data\error.log` |

## 8. Files

```
SDR Link.exe, Setup.bat   start / one-time setup (launcher source in tools/launcher/)
app.py, ui/               the app (Qt): files, messages, game, settings, hidden radio-link panel
files.py                  file transfer (blocks, SHA-256, continue after drops, history)
core.py                   link engine: radio + watchdog, outbox, settings, telemetry
secure.py                 pairing, addresses, AES-256-GCM, replay protection
transport.py              SR-ARQ transport
radio.py, phy/, grc/      the BPSK radio (GNU Radio)
cli.py                    headless: listen / text / send / bench, same key and link as the app
connect4.py, media.py     game rules, file kinds
data/                     made at first start: key, settings, received files, transfers.jsonl, telemetry/
tests/, tools/            tests above; tools/fake_radio.py runs both nodes on one PC
```

---

# Appendix: the radio and transport layers (from v5.x)

## A. The one-slow-direction problem and quiet FDD

**A second possible cause.** In the old FDD mode each radio transmits *all the time*: when it has nothing to
send, it sends idle symbols. If one E200's own transmitter leaks into its receiver
(self-desensitisation; the README of the 2-node link already saw this on one board),
that radio is partly deaf **all the time**. Data *into* it crawls; data *out of* it is fine,
because the few ACKs that get through are enough. This reproduces the symptom: with
radio 2 losing 75 % of the frames that arrive while it transmits, the old mode gives
22 KB/s into radio 2 and 109 KB/s out of it.

**Fix: quiet FDD (`--duplex fdd`, now the default).** Both directions still work at the
same time, but a radio is **silent when it has nothing to send**:

* **Framer** (`phy/framer_block.py`):
  * After a data frame it keeps sending idle symbols for 2048 symbols (2 ms), so
    back-to-back frames still need only the 4-byte preamble.
  * Then it goes silent. After an ACK or beacon it goes silent at once.
  * A frame that starts after silence gets a 128-byte burst preamble, so the far
    receiver re-locks. The preamble is 0x55 and then fixed pseudo-random bytes: in
    simulation a long 0x55-only preamble let the timing loop settle half a symbol off,
    and only about 50 % of frames sent alone after a silence decoded.
* **Receiver ACKs less often.** It ACKs per 32 KB of data instead of every frame, plus a
  double ACK when the sender pauses. A deaf receiver then loses few incoming frames to
  its own ACKs.
* **A sender that stops hearing ACKs** while it transmits (it is the deaf one) switches
  to "burst, then listen" for 15 s, so the ACKs reach it. The app tells you when this
  happens.
* **Both directions measured.** Every beacon carries frame counters, so both apps show
  the share of frames getting through **to** and **from** the other node. A lossy
  direction automatically gets shorter frames.

**Results** (no radios; *emulated* = frame-level emulator, *modem sim* = the real GNU
Radio modem with simulated air, CPU-limited on the test machine):

| test | old continuous FDD | quiet FDD |
|---|---|---|
| emulated, radio 2 loses 75 % of frames while it transmits: into radio 2 / out of radio 2 | 22 / 109 KB/s | **103 / 117 KB/s** |
| emulated, radio 2 completely deaf while it transmits | 0 / 0 (fails) | **99 / 102 KB/s** |
| modem sim, radio 2 completely deaf (−4 dB SINR while it transmits) | fails both ways | 59 % / 44 % of raw rate |
| modem sim, no deaf radio | 97 % / 76 % | 94 % / 97 % |
| modem sim, both directions at once (raw 31 KB/s in the sim) | 29 / 29 KB/s | 29 / 28 KB/s |

On your radios the problem is partial deafness (your slow direction still did 8 KB/s),
which is the 75 % row. **This is not yet measured on the E200s.**

If one direction is still slow on air:
1. Look at "frames getting through" in the app (top bar) on both PCs.
2. Raise **TX atten** on the radio that is slow to *receive*: its own transmitter
   leaks less.
3. Lower TX atten on the other radio.
4. Separate the TX and RX antennas.
5. Move F1 / F2 further apart.
6. `--duplex tdd` is the fallback (slower: one direction at a time; 57–88 KB/s in your
   earlier TDD test).


## B. v5.1 and v5.2: a PC whose transmitter can't keep up

**Seen on air with v5.0** (both PCs running `cli.py bench --mb 4` at the same time):
* Node 2 → node 1: 3.8 MB at 113 KB/s.
* Node 1 → node 2: failed ("no ACK progress").
* Both receivers ran at 1.00× real time.
* **Node 1's transmit queue grew to about 420 frames** (node 2's never passed 33). That
  means node 1 got data out at only about 0.55× real time. The E200 then runs out of
  samples mid-frame, the frames break up, and node 2 can't decode them.

**Cause:** CPU. The pulse shaper on the transmit side was GNU Radio's *Polyphase
Arbitrary Resampler*, used for a plain ×4. It is the most expensive block in the radio:
8.5 Msps on the test machine. The receive AGC was a Python block, which shares the
Python lock with the Python transmit framer.

**Fix:**
* **Transmit pulse shaping:** an *Interpolating FIR Filter* with the same RRC pulse
  (51.6 Msps; in-band shape identical within 0.06 dB, same output level).
* **Receive AGC:** the same look-ahead AGC built from C++ blocks (`agc_block.FastAGC`).
  `--agc py` brings back the Python one.
* **Result:** receive + transmit together run at **3.20× real time** on the test
  machine, against **1.31×** with the Claude_3 / v5.0 code. On a laptop half as fast,
  the old code falls below real time and the new one keeps a 1.6× margin.

**New diagnostics:**
* **`python tools/speed_check.py`:** run it on each PC; you want `RESULT: OK` (1.5× or
  better).
* **The radio line** now also shows *transmitter N× real time*, underflows and the TX
  queue, and turns red if either side falls behind or the queue keeps growing.

The GRC flowgraphs use the same blocks.

### v5.2: the transmitter can't get its samples to the E200 (`--profile lean`)

**Seen on air with v5.1 on node 1:**
* receiver 1.00× real time;
* transmitter 0.3–0.45×, **even when idle** with an empty queue;
* node 2 received only 68 % of node 1's frames, and node 2 → node 1 ran at 74 KB/s.

**What it means:** the CPU keeps up (the receive chain is the heavier one). What can't keep
up is moving the transmit samples to the E200. At `robust`, each direction carries
4 Msps × 4 bytes = 16 MB/s (128 Mbit/s). Receive got all of it, transmit got about a third,
about 170–190 Mbit/s in total. That is typical of a **USB 2.0 port or a USB 2.0 Ethernet
adapter**. A real Gigabit port carries both directions easily.

**Software fix: `--profile lean`.** The same 1 Mbit/s BPSK signal on the air, made with
2 instead of 4 samples per symbol. That is 2 Msps, half the traffic between the PC and the
E200.

**Simulation results:**
* back-to-back frames 200/200 at 20, 10 and 8 dB;
* bursts 80/80 at 20 dB and 71/80 at 8 dB;
* both directions at once 97 % of the raw rate;
* speed_check 4.6× real time.

Use the same profile on both PCs.

**Hardware fix:** connect that laptop to its E200 through a Gigabit port. Windows shows
the speed under Settings → Network → Ethernet → adapter properties ("Link speed
1000/1000 Mbps"). A USB adapter should be USB 3 in a USB 3 port.


## C. Review of the original flowgraph: what changed and why

| block / setting | problem | change |
|---|---|---|
| `preamble_size` = 1024 bytes | ~60 % of airtime was preamble | 4 bytes between back-to-back frames, 128 after silence (quiet FDD), 64 in TDD |
| PDU→Tagged Stream, Stream CRC32, Protocol Formatter, Tagged Stream Mux, Vector Sources, Tag Gate | The PlutoSDR Sink pushes only **full** buffers, so the last frames and ACKs of a burst sat in a half-full buffer → spurious ARQ timeouts | One **Embedded Python framer**: framing + CRC32 + whitening + differential encoding. The sample stream never stops (silence or idle symbols between frames) |
| Constellation Modulator | Byte-domain buffers held **~700 ms** of samples at 1 Msps | Framer outputs symbol indices → Chunks to Symbols → RRC Polyphase Arb Resampler → gain, small buffers |
| *(missing)* scrambler | Long runs of identical bits (silence in WAV, flat image areas) gave the loops no transitions: all-zero frames failed 25 of 30 | Whitening in the framer; RX **Additive Scrambler** (mask 0x8A, seed 0x7F, len 7, 8 bits/byte, reset tag `packet_len`) before CRC32 |
| *(missing)* AGC | Loop gains depended on distance | **Look-ahead (feed-forward) AGC**, 512 samples, O(1) per sample (`phy/agc_block.py`) |
| PlutoSDR Source gain | – | Manual 50 dB (the AD9361's own AGC can be pulled down by the board's own transmitter) |
| Symbol Sync BW 0.045, TED gain 0.1 / Costas BW 0.0628 | Jittery / noisy | 0.01, TED gain 1.0 / 0.02 |
| Correlate Access Code threshold 0 | Differential decoding turns 1 bit error into 2 | 3 |
| Repack Bits | GRC defaults: no length tag, LSB first → CRC always fails | Length tag `packet_len`, MSB first |
| Continuous FDD idle fill | A radio deafened by its own transmitter was deaf all the time → one slow direction | **Quiet FDD** (section 2) |
| Polyphase Arbitrary Resampler (TX RRC, ×4) | Most expensive block (8.5 Msps); a slower PC's transmitter fell behind real time | **Interpolating FIR Filter**, same RRC pulse (51.6 Msps) |
| Python look-ahead AGC | Shares the Python lock with the Python framer | **Same AGC from C++ blocks** |


## D. Transport protocol (Selective Repeat ARQ)

```
DATA : A5 | 01 | src | dst | xfer(2) | seq(4) | total(4) | txn(1) | up to 1009 bytes
ACK  : A5 | 02 | src | dst | xfer(2) | cum(4) | nrecv(4) | flags | echo_seq(4) | echo_txn | SACK bitmap
HELLO: A5 | 03 | src | FF  | 0(2)    | t_send | t_echo | echo_age | frames sent(4) | share of peer's frames received(2)
```
A transfer = `meta_len(2) | JSON {kind, name, size, sha256} | bytes`; kinds: text, image,
audio, file, game.

* **Selective Repeat with SACK.** Every ACK is a full snapshot (cumulative point plus a
  512-frame bitmap), so a lost ACK costs nothing.
* **Loss detection without timeouts.** The radio delivers frames in order: if a later
  frame is ACKed and an earlier one isn't, the earlier one is resent at once.
* **Exact RTT.** Every ACK echoes the exact transmission that triggered it.
* **Timeouts.** Adaptive RTO only for tail losses.
* **Pacing** at 98 % of the PHY rate, with a delay-based safety valve.
* **Priority and fairness.** ACKs and beacons jump ahead of queued data. Up to 4
  transfers run round-robin, so a chat line or a game move doesn't wait behind a
  photo.
* **Quiet-FDD additions:** see section 2.

