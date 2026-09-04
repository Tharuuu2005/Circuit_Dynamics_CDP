import socket

# ---------------------------------
# Configuration
# ---------------------------------
HOST = "127.0.0.1"      # Same computer as tx_flowgraph.py
PORT = 52001             # Change this to the port used by your GNU Radio TCP Source

# ---------------------------------
# Connect to GNU Radio
# ---------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HOST, PORT))

print("Connected to GNU Radio transmitter.")
print("Type a message and press Enter.")
print("Type 'exit' to quit.\n")

try:
    while True:
        message = input("TX > ") 

        if message.lower() == "exit":
            break

        # Optional: keep a copy of the last message
        with open("message.txt", "w", encoding="utf-8") as f:
            f.write(message)

        # Send message to GNU Radio
        sock.sendall(message.encode("utf-8"))

        print("✓ Sent")

except KeyboardInterrupt:
    print("\nStopping...")

finally:
    sock.close()