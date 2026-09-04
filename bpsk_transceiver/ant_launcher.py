import socket
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import time

# ======================================================
# CHANGE ONLY THESE TWO LINES ON COMPUTER B
# ======================================================
MY_ADDR = "01"      # Computer A = "01"
DEST_ADDR = "02"    # Computer B = "01"

TX_HOST = "127.0.0.1"
TX_PORT = 52001      # Python -> GNU Radio

RX_HOST = "127.0.0.1"
RX_PORT = 52002      # GNU Radio -> Python
# ======================================================

# ---------------- Socket Setup ----------------
tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_socket.bind((RX_HOST, RX_PORT))

seen_packets = set()
seq_num = 0

# ---------------- GUI ----------------
root = tk.Tk()
root.title(f"Node {MY_ADDR} - ANTSDR Full Duplex Chat")
root.geometry("650x450")

chat_box = ScrolledText(root, width=75, height=20, state="disabled")
chat_box.pack(padx=10, pady=10)

# Bottom frame
bottom_frame = tk.Frame(root)
bottom_frame.pack(fill="x", padx=10, pady=5)

entry = tk.Entry(bottom_frame, width=45)
entry.pack(side=tk.LEFT, padx=(0, 10), fill="x", expand=True)


def add_message(text):
    """Safely print text into chat window."""
    chat_box.config(state="normal")
    chat_box.insert(tk.END, text + "\n")
    chat_box.see(tk.END)
    chat_box.config(state="disabled")


# ---------------- SYNC ----------------
def send_sync():
    """Send synchronization packets to help SDR lock."""
    sync_packet = f"0|{MY_ADDR}|{DEST_ADDR}|SYNC"

    root.after(0, add_message, "🔄 Sending SYNC packets...")

    for _ in range(100):
        tx_socket.sendto(sync_packet.encode(), (TX_HOST, TX_PORT))
        time.sleep(0.05)

    root.after(0, add_message, "✅ Synchronization complete.")


# ---------------- Sender ----------------
def send_message(event=None):
    global seq_num

    msg = entry.get().strip()

    if not msg:
        return

    seq_num += 1

    packet = f"{seq_num}|{MY_ADDR}|{DEST_ADDR}|{msg}"

    tx_socket.sendto(packet.encode(), (TX_HOST, TX_PORT))

    add_message(f"You → Node {DEST_ADDR}: {msg}")

    entry.delete(0, tk.END)


# ---------------- Buttons ----------------
send_button = tk.Button(
    bottom_frame,
    text="📤 Send",
    width=10,
    command=send_message
)
send_button.pack(side=tk.LEFT, padx=5)

sync_button = tk.Button(
    bottom_frame,
    text="🔄 SYNC",
    width=10,
    command=lambda: threading.Thread(target=send_sync, daemon=True).start()
)
sync_button.pack(side=tk.LEFT)

entry.bind("<Return>", send_message)


# ---------------- Receiver ----------------
def receiver():
    while True:
        try:
            data, _ = rx_socket.recvfrom(2048)
            packet = data.decode(errors="ignore")

            # Packet format: SEQ|SRC|DST|PAYLOAD
            seq, src, dst, payload = packet.split("|", 3)

            # Ignore packets not addressed to me
            if dst != MY_ADDR:
                continue

            # Ignore my own transmitted packets
            if src == MY_ADDR:
                continue

            # Ignore SYNC packets
            if payload == "SYNC":
                continue

            # Ignore duplicates
            packet_id = f"{src}-{seq}"

            if packet_id in seen_packets:
                continue

            seen_packets.add(packet_id)

            # Update GUI safely
            root.after(0, add_message, f"Node {src}: {payload}")

        except Exception:
            continue


# ---------------- Start Receiver Thread ----------------
threading.Thread(target=receiver, daemon=True).start()

# ---------------- Run GUI ----------------
root.mainloop()