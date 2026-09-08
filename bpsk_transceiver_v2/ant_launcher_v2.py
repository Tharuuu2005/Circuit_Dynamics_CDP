# ==========================================================
# ANTSDR WiFi Stack V2.0
# Selective Repeat ARQ + TEXT + IMAGE SUPPORT
# ==========================================================

import socket
import threading
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from tkinter import filedialog
import time
import os
import base64

from PIL import Image, ImageTk

# ==========================================================
# NODE CONFIGURATION (CHANGE FOR COMPUTER B)
# ==========================================================
MY_ADDR = "02"           # Computer A = "01", Computer B = "02"
DEST_ADDR = "01"         # Target Node Address

TX_HOST = "127.0.0.1"
TX_PORT = 52001
RX_HOST = "127.0.0.1"
RX_PORT = 52002

# ==========================================================
# SELECTIVE REPEAT ARQ SETTINGS
# ==========================================================
WINDOW_SIZE = 8
ACK_TIMEOUT = 0.40
MAX_RETRIES = 6

BEACON_INTERVAL = 0.10
BEACON_TIMEOUT = 0.60
DEBUG_ARQ = False

# ==========================================================
# IMAGE SETTINGS
# ==========================================================
MTU_PAYLOAD = 64
image_extension_buffer = {}

# ==========================================================
# SOCKET SETUP
# ==========================================================
tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_socket.bind((RX_HOST, RX_PORT))

# ==========================================================
# SHARED STATE
# ==========================================================
global_msg_id = 0

connected = False

last_beacon = {}

tx_queue = {}

rx_reconstruction_buffer = {}

seen_chunk_keys = set()

# ==========================================================
# GUI SETUP
# ==========================================================
root = tk.Tk()
root.title(f"ANTSDR Node {MY_ADDR} (Selective Repeat ARQ + Image)")
root.geometry("760x600")

status_label = tk.Label(
    root,
    text="🟡 Searching...",
    fg="orange",
    font=("Arial",12,"bold")
)
status_label.pack(pady=5)

chat_box = ScrolledText(
    root,
    width=85,
    height=22,
    state="disabled"
)
chat_box.pack(padx=10,pady=10)

bottom = tk.Frame(root)
bottom.pack(fill="x",padx=10,pady=10)

entry = tk.Entry(bottom,width=55)
entry.pack(side=tk.LEFT,fill="x",expand=True,padx=(0,8))

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================
def add_message(text):

    chat_box.config(state="normal")
    chat_box.insert(tk.END,text+"\n")
    chat_box.see(tk.END)
    chat_box.config(state="disabled")


def raw_send(packet_str):

    tx_socket.sendto(packet_str.encode(),(TX_HOST,TX_PORT))

# ==========================================================
# IMAGE DISPLAY
# ==========================================================
def display_image(filename):

    try:
        img = Image.open(filename)

        img.thumbnail((250,250))

        photo = ImageTk.PhotoImage(img)

        label = tk.Label(root,image=photo)
        label.image = photo
        label.pack(pady=5)

    except Exception as e:
        add_message(f"❌ Display error : {e}")

# ==========================================================
# UNIVERSAL PAYLOAD SENDER
# ==========================================================
def send_payload(payload_data,packet_type="TEXT",file_ext=""):

    global global_msg_id

    global_msg_id += 1
    seq = global_msg_id

    # ---------------- TEXT ----------------
    if packet_type=="TEXT":

        chunks=[payload_data]

    # ---------------- IMAGE ----------------
    elif packet_type=="IMAGE":

        encoded = base64.b64encode(payload_data).decode()

        chunks = [
            encoded[i:i+MTU_PAYLOAD]
            for i in range(0,len(encoded),MTU_PAYLOAD)
        ]

        image_extension_buffer[str(seq)] = file_ext

        add_message(
            f"🖼️ Sending image... ({len(chunks)} chunks)"
        )

    else:
        return

    total_chunks=len(chunks)

    for chunk_idx,chunk_data in enumerate(chunks):

        packet_key=f"{seq}-{chunk_idx}"

        payload=chunk_data

        if packet_type=="IMAGE":
            payload=f"{file_ext}:{chunk_data}"

        packet_str=(
            f"{seq}|{MY_ADDR}|{DEST_ADDR}|"
            f"{packet_type}|{chunk_idx}|"
            f"{total_chunks}|{payload}"
        )

        tx_queue[packet_key]={
            "packet":packet_str,
            "tx_time":0.0,
            "retries":0,
            "seq":seq,
            "chunk_idx":chunk_idx,
            "sent":False
        }

    if packet_type=="TEXT":
        add_message(f"🟢 You → Node {DEST_ADDR}: {payload_data}")

# ==========================================================
# IMAGE PICKER
# ==========================================================
def send_image():

    filepath=filedialog.askopenfilename(
        title="Select Image",
        filetypes=[
            ("Images","*.png *.jpg *.jpeg *.bmp")
        ]
    )

    if not filepath:
        return

    try:

        with open(filepath,"rb") as f:
            image_bytes=f.read()

        ext=os.path.splitext(filepath)[1].lower()

        send_payload(
            image_bytes,
            packet_type="IMAGE",
            file_ext=ext
        )

    except Exception as e:
        add_message(f"❌ Image send failed : {e}")

# ==========================================================
# SELECTIVE REPEAT TRANSMITTER
# ==========================================================
def selective_repeat_tx_service():

    while True:

        now=time.time()

        active_in_window=[
            k for k,v in tx_queue.items()
            if v["sent"]
        ]

        # Send new packets
        for pkey,info in list(tx_queue.items()):

            if not info["sent"] and len(active_in_window)<WINDOW_SIZE:

                info["sent"]=True
                info["tx_time"]=now

                raw_send(info["packet"])

                active_in_window.append(pkey)

                if DEBUG_ARQ:
                    print("[TX]",pkey)

        # Timeout / Retransmission
        to_remove=[]

        for pkey, info in list(tx_queue.items()):

            if info["sent"] and (now-info["tx_time"]>=ACK_TIMEOUT):

                if info["retries"]<MAX_RETRIES:

                    info["retries"]+=1
                    info["tx_time"]=now

                    raw_send(info["packet"])

                    if DEBUG_ARQ:
                        print("[RETX]",pkey)

                else:
                    to_remove.append(pkey)
                    root.after(
                        0,
                        add_message,
                        f"⚠️ Delivery failed : {pkey}"
                    )

        for pkey in to_remove:
            tx_queue.pop(pkey,None)

        time.sleep(0.02)

# ==========================================================
# BEACON SERVICE
# ==========================================================
def beacon_service():

    while True:

        beacon=f"0|{MY_ADDR}|FF|BEACON|0|1|ONLINE"

        raw_send(beacon)

        time.sleep(BEACON_INTERVAL)

# ==========================================================
# CONNECTION MONITOR
# ==========================================================
def connection_monitor():

    global connected

    while True:

        now=time.time()

        if DEST_ADDR in last_beacon and (
            now-last_beacon[DEST_ADDR] <= BEACON_TIMEOUT
        ):

            if not connected:

                connected=True

                root.after(
                    0,
                    lambda: status_label.config(
                        text="🟢 Connected",
                        fg="green"
                    )
                )

        else:

            if connected or DEST_ADDR not in last_beacon:

                connected=False

                txt="🔴 Connection Lost" if DEST_ADDR in last_beacon else "🟡 Searching..."
                clr="red" if DEST_ADDR in last_beacon else "orange"

                root.after(
                    0,
                    lambda: status_label.config(
                        text=txt,
                        fg=clr
                    )
                )

        time.sleep(0.20)

# ==========================================================
# RECEIVER
# ==========================================================
def receiver():

    while True:

        try:

            data,_=rx_socket.recvfrom(4096)

            packet=data.decode(errors="ignore")

            parts=packet.split("|",6)

            if len(parts)<7:
                continue

            seq,src,dst,ptype,chunk_idx_s,total_chunks_s,payload=parts

            seq=seq.strip()
            src=src.strip()
            dst=dst.strip()
            ptype=ptype.strip()

            chunk_idx=int(chunk_idx_s)
            total_chunks=int(total_chunks_s)

            if src==MY_ADDR:
                continue

            # ---------------- BEACON ----------------
            if ptype=="BEACON":
                last_beacon[src]=time.time()
                continue

            if dst!=MY_ADDR:
                continue

            # ---------------- ACK ----------------
            if ptype=="ACK":

                ack_key=f"{seq}-{chunk_idx}"

                if ack_key in tx_queue:
                    del tx_queue[ack_key]

                continue

            # ---------------- TEXT / IMAGE ----------------
            if ptype in ["TEXT","IMAGE"]:

                ack_packet=(
                    f"{seq}|{MY_ADDR}|{src}|"
                    f"ACK|{chunk_idx}|{total_chunks}|OK"
                )

                raw_send(ack_packet)

                chunk_key=f"{src}-{seq}-{chunk_idx}"
                msg_key=f"{src}-{seq}"

                if chunk_key in seen_chunk_keys:
                    continue

                seen_chunk_keys.add(chunk_key)

                if msg_key not in rx_reconstruction_buffer:

                    rx_reconstruction_buffer[msg_key]={
                        "total_chunks":total_chunks,
                        "chunks":{},
                        "ext":".png"
                    }

                if ptype=="IMAGE":

                    if ":" in payload:
                        ext,payload=payload.split(":",1)
                        rx_reconstruction_buffer[msg_key]["ext"]=ext

                rx_reconstruction_buffer[msg_key]["chunks"][chunk_idx]=payload

                buf=rx_reconstruction_buffer[msg_key]

                if len(buf["chunks"])==buf["total_chunks"]:

                    full_payload="".join(
                        buf["chunks"][i]
                        for i in sorted(buf["chunks"].keys())
                    )

                    # ---------------- TEXT COMPLETE ----------------
                    if ptype=="TEXT":

                        root.after(
                            0,
                            add_message,
                            f"🔵 Node {src}: {full_payload}"
                        )

                    # ---------------- IMAGE COMPLETE ----------------
                    elif ptype=="IMAGE":

                        try:

                            image_bytes=base64.b64decode(full_payload)

                            filename=f"received_{seq}{buf['ext']}"

                            with open(filename,"wb") as f:
                                f.write(image_bytes)

                            root.after(
                                0,
                                add_message,
                                f"🖼️ Image received : {filename}"
                            )

                            root.after(
                                0,
                                display_image,
                                filename
                            )

                        except Exception as e:

                            root.after(
                                0,
                                add_message,
                                f"❌ Image decode failed : {e}"
                            )

                    del rx_reconstruction_buffer[msg_key]

        except Exception:
            continue

# ==========================================================
# GUI EVENTS
# ==========================================================
def on_send_click(event=None):

    msg=entry.get().strip()

    if msg:
        send_payload(msg,"TEXT")
        entry.delete(0,tk.END)

send_button=tk.Button(
    bottom,
    text="📤 Send",
    width=10,
    command=on_send_click
)
send_button.pack(side=tk.LEFT,padx=(0,5))

image_button=tk.Button(
    bottom,
    text="🖼️ Image",
    width=10,
    command=send_image
)
image_button.pack(side=tk.LEFT)

entry.bind("<Return>",on_send_click)

# ==========================================================
# START THREADS
# ==========================================================
threading.Thread(
    target=receiver,
    daemon=True
).start()

threading.Thread(
    target=beacon_service,
    daemon=True
).start()

threading.Thread(
    target=connection_monitor,
    daemon=True
).start()

threading.Thread(
    target=selective_repeat_tx_service,
    daemon=True
).start()

# ==========================================================
# MAIN LOOP
# ==========================================================
root.mainloop()