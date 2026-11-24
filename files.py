"""
File transfer between the two nodes, on top of core.Node (encrypted SR-ARQ transport).

A file is sent in 512 KB blocks, each one verified transfer, 2 blocks on the air at a time
so the link stays full between blocks. Why blocks:
  * a link drop: the transport keeps its transfers through the drop and continues with the
    missing frames when the link is back; a block that fails anyway (e.g. the other app was
    restarted) is sent again, never the whole file;
  * files of any size (only a few blocks are in memory at a time).
When the last block arrives the receiver checks the SHA-256 of the whole file, saves it and
tells the sender ("Delivered, verified").

Messages (kind "file", reliable, encrypted):
    offer  {fid, name, size, sha256, bsize, blocks, kind, chat}   sender -> receiver
    done   {fid, ok}                                              receiver -> sender
    cancel {fid}                                                  either way
Blocks: transfer kind "fblock", meta {fid, i}.
Folders are zipped first (stored as <folder>.zip).
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile

import media

BLOCK = 512 * 1024
IN_FLIGHT = 2


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Item:
    """one file, sending or receiving (what the Files page shows)."""

    def __init__(self, fid, direction, name, size, kind="file", chat=False):
        self.fid, self.direction, self.name, self.size = fid, direction, name, size
        self.kind, self.chat = kind, chat
        self.status = "preparing" if direction == "out" else "receiving"
        self.path = None              # what goes on the air (out; a folder's zip) or saved file (in)
        self.source = None            # what the user picked (out)
        self.sha256 = None
        self.blocks = 0
        self.done_blocks = set()
        self.partial = {}             # block -> bytes so far (progress inside a block)
        self.t_start = time.time()
        self.t_first = None           # first byte on the air
        self.t_end = None
        self.resends = 0              # blocks sent again after a link drop
        self.frames = self.retx = 0   # radio frames of this file (sender side)
        self.error = None

    def block_size(self, i):
        return max(0, min(BLOCK, self.size - i * BLOCK))

    @property
    def bytes_done(self):
        b = sum(self.block_size(i) for i in self.done_blocks)
        return min(self.size, b + sum(self.partial.values()))

    @property
    def progress(self):
        return 1.0 if self.size == 0 else self.bytes_done / self.size

    @property
    def active(self):
        return self.status in ("preparing", "waiting", "sending", "receiving", "verifying")

    def record(self):
        dt = (self.t_end or time.time()) - (self.t_first or self.t_start)
        return dict(fid=self.fid, dir=self.direction, name=self.name, size=self.size, kind=self.kind,
                    status=self.status, path=self.source or self.path, start=self.t_start, end=self.t_end,
                    seconds=round(dt, 2), rate=round(self.size / dt) if dt > 0 and self.status == "done" else None,
                    resends=self.resends, frames=self.frames, retx=self.retx, error=self.error)


class FileManager:
    def __init__(self, node, recv_dir, on_change=None):
        self.node = node
        self.recv_dir = recv_dir
        self.on_change = on_change or (lambda item: None)
        self.items = {}                # fid -> Item (this session)
        self.order = []                # fids, oldest first
        self._queue = []               # out fids waiting / sending, in order
        self._todo = {}                # fid -> blocks still to send (list)
        self._xid = {}                 # transport xfer -> (fid, block)
        self._rx_x = {}                # incoming xfer -> bytes so far (progress)
        self._lock = threading.RLock()
        self.data = node.data_dir
        self.inc_dir = os.path.join(self.data, "incoming")
        self.tmp_dir = os.path.join(self.data, "outgoing")
        os.makedirs(self.inc_dir, exist_ok=True)
        os.makedirs(self.tmp_dir, exist_ok=True)
        os.makedirs(recv_dir, exist_ok=True)
        self.hist_path = os.path.join(self.data, "transfers.jsonl")
        self.history = self._load_history()
        self._clean_old_partials()

    def _clean_old_partials(self, days=7):
        """unfinished incoming files nobody resumed for a week."""
        for f in os.listdir(self.inc_dir):
            p = os.path.join(self.inc_dir, f)
            try:
                if time.time() - os.path.getmtime(p) > days * 86400:
                    os.remove(p)
            except OSError:
                pass

    # ------------------------------------------------------------------ public
    def send_paths(self, paths, chat=False):
        """files and folders; returns the new fids."""
        fids = []
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            name = os.path.basename(p.rstrip("\\/")) + (".zip" if os.path.isdir(p) else "")
            size = os.path.getsize(p) if os.path.isfile(p) else 0
            it = Item(uuid.uuid4().hex[:16], "out", name, size,
                      "file" if os.path.isdir(p) else media.detect_kind(p), chat)
            it.path = it.source = p
            with self._lock:
                self.items[it.fid] = it
                self.order.append(it.fid)
            threading.Thread(target=self._prepare, args=(it, p), daemon=True).start()
            fids.append(it.fid)
            self.on_change(it)
        return fids

    def cancel(self, fid):
        with self._lock:
            it = self.items.get(fid)
            if not it or not it.active:
                return
            it.status, it.t_end = "cancelled", time.time()
            self._drop(fid)
        self.node.send_msg("file", {"t": "cancel", "fid": fid})
        if it.direction == "in":
            self._remove_partial(fid)
        self._finish(it)

    def retry(self, fid):
        """send a failed or cancelled file again (as a new transfer)."""
        it = self.items.get(fid)
        src = it.source if it else None
        if it and it.direction == "out" and not it.active and src and os.path.exists(src):
            return self.send_paths([src], it.chat)
        return []

    def link_up(self):
        """the link came back: put blocks that failed on the air again."""
        self._pump()

    # ------------------------------------------------------------------ sender
    def _prepare(self, it, p):
        try:
            if os.path.isdir(p):
                it.status = "preparing"
                z = os.path.join(self.tmp_dir, it.fid + ".zip")
                with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for root, _, files in os.walk(p):
                        for f in files:
                            full = os.path.join(root, f)
                            zf.write(full, os.path.relpath(full, os.path.dirname(p.rstrip("\\/"))))
                it.path = z
                it.size = os.path.getsize(z)
            it.sha256 = _sha256(it.path)
            it.blocks = max(1, (it.size + BLOCK - 1) // BLOCK)
        except OSError as ex:
            it.status, it.error, it.t_end = "failed", f"could not read the file ({ex})", time.time()
            self._finish(it)
            return
        with self._lock:
            if it.status == "cancelled":
                return
            it.status = "waiting"
            self._todo[it.fid] = list(range(it.blocks))
            self._queue.append(it.fid)
        self.node.send_msg("file", {"t": "offer", "fid": it.fid, "name": it.name, "size": it.size,
                                    "sha256": it.sha256, "bsize": BLOCK, "blocks": it.blocks,
                                    "kind": it.kind, "chat": it.chat})
        self._pump()
        self.on_change(it)

    def _read_block(self, it, i):
        with open(it.path, "rb") as f:
            f.seek(i * BLOCK)
            return f.read(BLOCK)

    def _pump(self):
        """keep IN_FLIGHT blocks on the air, oldest file first."""
        if self.node.tr.link_state != "up":
            return
        with self._lock:
            busy = len(self._xid)
            for fid in list(self._queue):
                if busy >= IN_FLIGHT:
                    break
                it, todo = self.items[fid], self._todo.get(fid, [])
                while todo and busy < IN_FLIGHT:
                    i = todo.pop(0)
                    try:
                        data = self._read_block(it, i)
                    except OSError as ex:
                        it.status, it.error, it.t_end = "failed", f"file can't be read any more ({ex})", time.time()
                        self._drop(fid)
                        self._finish(it)
                        break
                    xid = self.node.send_data(data, {"kind": "fblock", "fid": fid, "i": i})
                    self._xid[xid] = (fid, i)
                    it.status = "sending"
                    if it.t_first is None:
                        it.t_first = time.time()
                    busy += 1

    def _drop(self, fid):
        self._todo.pop(fid, None)
        if fid in self._queue:
            self._queue.remove(fid)
        for xid, (f, _) in list(self._xid.items()):
            if f == fid:
                self.node.tr.cancel(xid)
                del self._xid[xid]

    # ------------------------------------------------------------------ events from the node
    def on_event(self, e):
        """returns True if the event belonged to a file transfer."""
        t = e["type"]
        if t == "msg" and e["kind"] == "file":
            self._on_msg(e["obj"])
            return True
        if t == "file" and e["meta"].get("kind") == "fblock":
            self._on_block(e["meta"], e["data"], e.get("xfer"))
            return True
        if t in ("sent", "failed", "progress", "tx_stats"):
            return self._on_tx(e)
        if t == "rx_progress":
            self._rx_x[e["xfer"]] = e["bytes"]
            it = self._receiving()
            if it:
                it.partial = {("x", k): v for k, v in self._rx_x.items()}
                self.on_change(it)
            return False
        if t == "link" and e["state"] == "up":
            self.link_up()
        return False

    def _on_tx(self, e):
        with self._lock:
            key = self._xid.get(e["xfer"])
            if key is None:
                return False
            fid, i = key
            it = self.items.get(fid)
            if it is None:
                return True
            if e["type"] == "progress":
                it.partial[i] = int(e["pct"] / 100 * len(range(i * BLOCK, min(it.size, (i + 1) * BLOCK))))
            elif e["type"] == "sent":
                del self._xid[e["xfer"]]
                it.partial.pop(i, None)
                it.done_blocks.add(i)
                it.frames += e.get("frames", 0)
                it.retx += e.get("retx", 0)
                if len(it.done_blocks) == it.blocks and it.status == "sending":
                    it.status = "verifying"          # all blocks ACKed: wait for the receiver's check
                    if fid in self._queue:
                        self._queue.remove(fid)
            elif e["type"] == "failed":
                del self._xid[e["xfer"]]
                it.partial.pop(i, None)
                if it.active:
                    self._todo.setdefault(fid, []).insert(0, i)   # again once the link is back
                    it.resends += 1
        self._pump()
        self.on_change(it)
        return True

    def _on_msg(self, m):
        t, fid = m.get("t"), m.get("fid")
        if t == "offer":
            self._on_offer(m)
        elif t == "done":
            it = self.items.get(fid)
            if it and it.direction == "out" and it.active:
                it.status = "done" if m.get("ok") else "failed"
                it.error = None if m.get("ok") else "the copy at the other node failed its check"
                it.t_end = time.time()
                self._finish(it)
        elif t == "cancel":
            it = self.items.get(fid)
            if it and it.active:
                with self._lock:
                    self._drop(fid)
                it.status, it.t_end = "cancelled", time.time()
                it.error = "cancelled at the other node"
                if it.direction == "in":
                    self._remove_partial(fid)
                self._finish(it)

    # ------------------------------------------------------------------ receiver
    def _state_path(self, fid):
        return os.path.join(self.inc_dir, fid + ".json")

    def _part_path(self, fid):
        return os.path.join(self.inc_dir, fid + ".part")

    def _on_offer(self, m):
        fid = m["fid"]
        with self._lock:
            it = self.items.get(fid)
            if it is None:
                it = Item(fid, "in", os.path.basename(str(m.get("name", "file"))), int(m["size"]),
                          m.get("kind", "file"), bool(m.get("chat")))
                it.sha256, it.blocks = m["sha256"], int(m["blocks"])
                it.t_first = time.time()        # the blocks follow the offer at once
                st = self._load_state(fid)
                if st:
                    it.done_blocks = set(st["got"])
                self.items[fid] = it
                self.order.append(fid)
                if not os.path.exists(self._part_path(fid)):
                    with open(self._part_path(fid), "wb") as f:
                        f.truncate(it.size)
                self._save_state(it)
        self.on_change(it)

    def _receiving(self):
        for fid in reversed(self.order):
            it = self.items[fid]
            if it.direction == "in" and it.status == "receiving":
                return it
        return None

    def _on_block(self, meta, data, xfer):
        fid, i = meta.get("fid"), int(meta.get("i", -1))
        it = self.items.get(fid)
        self._rx_x.clear()
        if it is None:
            st = self._load_state(fid)          # this app was restarted during the transfer
            if st is None:
                self.node.send_msg("file", {"t": "cancel", "fid": fid})
                return
            it = Item(fid, "in", st["name"], st["size"])
            it.sha256, it.blocks, it.done_blocks = st["sha256"], st["blocks"], set(st["got"])
            with self._lock:
                self.items[fid] = it
                self.order.append(fid)
        if it is None or it.direction != "in" or it.status != "receiving" or not 0 <= i < it.blocks:
            return
        with open(self._part_path(fid), "r+b") as f:
            f.seek(i * BLOCK)
            f.write(data)
        if it.t_first is None:
            it.t_first = time.time()
        it.partial = {}
        it.done_blocks.add(i)
        self._save_state(it)
        if len(it.done_blocks) == it.blocks:
            it.status = "verifying"
            self.on_change(it)
            threading.Thread(target=self._complete, args=(it,), daemon=True).start()
        else:
            self.on_change(it)

    def _complete(self, it):
        part = self._part_path(it.fid)
        ok = _sha256(part) == it.sha256
        if ok:
            dest = self._unique(os.path.join(self.recv_dir, it.name))
            shutil.move(part, dest)
            it.path, it.status = dest, "done"
        else:
            it.status, it.error = "failed", "the file failed its SHA-256 check"
            os.remove(part)
        try:
            os.remove(self._state_path(it.fid))
        except OSError:
            pass
        it.t_end = time.time()
        self.node.send_msg("file", {"t": "done", "fid": it.fid, "ok": ok})
        self._finish(it)

    @staticmethod
    def _unique(path):
        base, ext = os.path.splitext(path)
        k, p = 1, path
        while os.path.exists(p):
            p = f"{base} ({k}){ext}"
            k += 1
        return p

    def _save_state(self, it):
        try:
            with open(self._state_path(it.fid), "w") as f:
                json.dump({"name": it.name, "size": it.size, "sha256": it.sha256, "blocks": it.blocks,
                           "got": sorted(it.done_blocks)}, f)
        except OSError:
            pass

    def _load_state(self, fid):
        try:
            with open(self._state_path(fid)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _remove_partial(self, fid):
        for p in (self._part_path(fid), self._state_path(fid)):
            try:
                os.remove(p)
            except OSError:
                pass

    # ------------------------------------------------------------------ history
    def _finish(self, it):
        rec = it.record()
        self.history.append(rec)
        try:
            with open(self.hist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        if it.direction == "out" and it.path and it.path.startswith(self.tmp_dir):
            try:
                os.remove(it.path)                  # the zip of a folder
            except OSError:
                pass
        self.node.note(f"file {it.direction} {it.name}: {it.status}"
                       + (f" ({it.error})" if it.error else "")
                       + (f", {rec['seconds']} s, {rec['rate'] / 1e3:.0f} KB/s" if rec.get("rate") else ""))
        self.on_change(it)

    def _load_history(self):
        out = []
        try:
            with open(self.hist_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        return out[-500:]
