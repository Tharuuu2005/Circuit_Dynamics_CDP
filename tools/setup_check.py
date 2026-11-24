"""
Run once per PC by Setup.bat (inside the Python that has GNU Radio: radioconda or your own conda
environment): installs what the app needs and is missing, WITHOUT touching numpy, Qt or GNU
Radio (pip --no-deps), then checks everything the app uses and tells you what is missing.
"""
import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

# module -> pip package (installed only if the import fails)
NEEDED = [
    ("cffi", "cffi"),
    ("cryptography", "cryptography"),
]


def try_import(mod):
    try:
        importlib.import_module(mod)
        return True, ""
    except Exception as ex:                  # ImportError, or a DLL that won't load
        return False, str(ex).splitlines()[0][:120]


def main():
    print("SDR Link setup check\n")
    for mod, pkg in NEEDED:
        ok, err = try_import(mod)
        if ok:
            print(f"  ok        {mod}")
            continue
        print(f"  install   {pkg}  ({err})")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--disable-pip-version-check", pkg])
        if r.returncode != 0:
            print(f"  FAILED    pip install {pkg}")
    print()
    problems = []
    for mod, why in (("PyQt5.QtWidgets", "the app window"), ("numpy", "the radio"),
                     ("gnuradio.gr", "the radio (GNU Radio)"), ("gnuradio.iio", "the E200 (gr-iio)"),
                     ("cryptography", "link encryption")):
        ok, err = try_import(mod)
        print(f"  {'ok' if ok else 'MISSING':8}  {why:28} ({mod}{'' if ok else ': ' + err})")
        if not ok:
            problems.append(why)
    try:
        r = subprocess.run(["iio_info", "-u", "ip:192.168.1.10"], capture_output=True, text=True, timeout=8)
        print("\n  E200 at ip:192.168.1.10: " + ("found" if r.returncode == 0 else "not reachable right now "
                                                  "(check the Ethernet cable and the PC's IP 192.168.1.100)"))
    except Exception:
        print("\n  E200 check skipped (iio_info not found)")
    print()
    if problems:
        print("NOT READY: " + ", ".join(problems))
        return 1
    print("READY. Double-click 'SDR Link.exe' (or the desktop shortcut) to start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
