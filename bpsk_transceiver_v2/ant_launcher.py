import socket
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
import time

# ==========================================================
# CHANGE ONLY THESE TWO LINES ON COMPUTER B
# ==========================================================
MY_ADDR = "02"          # Computer A = "01"
DEST_ADDR = "01"        # Computer B = "01"

TX_HOST = "127.0.0.1"
TX_PORT = 52001         # Python -> GNU Radio Socket PDU

RX_HOST = "127.0.0.1"
RX_PORT = 52002         # GNU Radio Socket PDU -> Python

# ==========================================================
# PHY / MAC SETTINGS
# ==========================================================
BEACON_INTERVAL = 0.10      # 100 ms heartbeat
BEACON_TIMEOUT = 0.60       # Lost after 600 ms
DEBUG_BEACON = False        # True = print every beacon

# ==========================================================
# SOCKET SETUP
# ==========================================================
tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

rx_socket.bind((RX_HOST, RX_PORT))

# ==========================================================
# SHARED VARIABLES
# ==========================================================
seq_num = 0
connected = False

seen_packets = set()
last_beacon = {}

# ==========================================================
# GUI
# ==========================================================
root = tk.Tk()
root.title(f"ANTSDR Node {MY_ADDR}")
root.geometry("700x520")

status_label = tk.Label(
    root,
    text="🟡 Searching...",
    fg="orange",
    font=("Arial", 12, "bold")
)
status_label.pack(pady=5)

chat_box = ScrolledText(root, width=80, height=22, state="disabled")
chat_box.pack(padx=10, pady=10)

bottom = tk.Frame(root)
bottom.pack(fill="x", padx=10, pady=10)

entry = tk.Entry(bottom, width=55)
entry.pack(side=tk.LEFT, fill="x", expand=True, padx=(0,10))

# ==========================================================
# CHAT WINDOW UPDATE
# ==========================================================
def add_message(text):
    chat_box.config(state="normal")
    chat_box.insert(tk.END, text + "\n")
    chat_box.see(tk.END)
    chat_box.config(state="disabled")

# ==========================================================
# SEND PACKET
# ==========================================================
def send_packet(packet):
    tx_socket.sendto(packet.encode(), (TX_HOST, TX_PORT))

# ==========================================================
# SEND CHAT MESSAGE
# ==========================================================
def send_message(event=None):
    global seq_num

    msg = entry.get().strip()

    if msg == "":
        return

    seq_num += 1

    packet = f"{seq_num}|{MY_ADDR}|{DEST_ADDR}|TEXT|{msg}"

    send_packet(packet)

    add_message(f"🟢 You → Node {DEST_ADDR}: {msg}")

    entry.delete(0, tk.END)

# ==========================================================
# BEACON THREAD (FDD VERSION)
# ==========================================================
def beacon_service():

    while True:

        beacon = f"0|{MY_ADDR}|FF|BEACON|ONLINE"

        send_packet(beacon)

        time.sleep(BEACON_INTERVAL)

# ==========================================================
# CONNECTION MONITOR
# ==========================================================
def connection_monitor():

    global connected

    while True:

        now = time.time()

        if DEST_ADDR in last_beacon:

            elapsed = now - last_beacon[DEST_ADDR]

            if elapsed <= BEACON_TIMEOUT:

                if not connected:

                    connected = True

                    root.after(
                        0,
                        lambda: status_label.config(
                            text="🟢 Connected",
                            fg="green"
                        )
                    )

            else:

                if connected:

                    connected = False

                    root.after(
                        0,
                        lambda: status_label.config(
                            text="🔴 Connection Lost",
                            fg="red"
                        )
                    )

        else:

            if connected:

                connected = False

            root.after(
                0,
                lambda: status_label.config(
                    text="🟡 Searching...",
                    fg="orange"
                )
            )

        time.sleep(0.2)

# ==========================================================
# RECEIVER THREAD
# ==========================================================
def receiver():

    while True:

        try:

            data, _ = rx_socket.recvfrom(4096)

            packet = data.decode(errors="ignore")

            seq, src, dst, packet_type, payload = packet.split("|",4)

            # Ignore our own packets
            if src == MY_ADDR:
                continue

            # ---------------- BEACON ----------------
            if packet_type == "BEACON":

                last_beacon[src] = time.time()

                if DEBUG_BEACON:
                    root.after(
                        0,
                        add_message,
                        f"📡 Beacon from Node {src}"
                    )

                continue

            # Ignore packets not for me
            if dst != MY_ADDR:
                continue

            # Duplicate protection
            packet_id = f"{src}-{seq}"

            if packet_id in seen_packets:
                continue

            seen_packets.add(packet_id)

            # Prevent unlimited memory growth
            if len(seen_packets) > 200:
                seen_packets.clear()

            # ---------------- TEXT ----------------
            if packet_type == "TEXT":

                root.after(
                    0,
                    add_message,
                    f"🔵 Node {src}: {payload}"
                )

            elif packet_type == "IMAGE":

                root.after(
                    0,
                    add_message,
                    "🖼️ Image packet received."
                )

            elif packet_type == "AUDIO":

                root.after(
                    0,
                    add_message,
                    "🎤 Audio packet received."
                )

            elif packet_type == "ACK":

                root.after(
                    0,
                    add_message,
                    "✅ ACK received."
                )

        except Exception:
            continue

# ==========================================================
# SEND BUTTON
# ==========================================================
send_button = tk.Button(
    bottom,
    text="📤 Send",
    width=10,
    command=send_message
)
send_button.pack(side=tk.LEFT)

entry.bind("<Return>", send_message)

# ==========================================================
# START BACKGROUND THREADS
# ==========================================================
threading.Thread(target=receiver, daemon=True).start()
threading.Thread(target=beacon_service, daemon=True).start()
threading.Thread(target=connection_monitor, daemon=True).start()

# ==========================================================
# RUN GUI
# ==========================================================
root.mainloop()