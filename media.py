"""Application-layer helpers for text / image / audio / generic files."""
import io
import os
import subprocess
import sys
import time

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma"}

try:
    from PIL import Image  # optional (pip install pillow); only needed for compression/preview of JPEG
    HAVE_PIL = True
except Exception:  # pragma: no cover
    HAVE_PIL = False


def detect_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return "file"


def load_for_send(path, compress=False, quality=70, max_dim=1920):
    """
    Returns (data_bytes, meta). Files are sent as raw binary (no base64).
    compress=True re-encodes images as JPEG (lossy, much smaller). Default is lossless:
    the exact original file is delivered and verified with SHA-256.
    """
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    kind = detect_kind(path)
    meta = {"kind": kind, "name": name, "orig_size": len(data)}
    if kind == "image" and compress:
        if not HAVE_PIL:
            raise RuntimeError("Image compression needs Pillow: pip install pillow")
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=int(quality), optimize=True)
        new = buf.getvalue()
        if len(new) < len(data):
            data = new
            meta["name"] = os.path.splitext(name)[0] + ".jpg"
            meta["compressed"] = f"jpeg q{quality} max{max_dim}px"
    return data, meta


def save_received(data, meta, folder="received"):
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(meta.get("name") or f"rx_{int(time.time())}.bin")
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    i = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{i}{ext}")
        i += 1
    with open(path, "wb") as f:
        f.write(data)
    return os.path.abspath(path)


def open_file(path):
    """Open with the OS default app (plays audio, shows images)."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as ex:
        print("could not open", path, ex)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
