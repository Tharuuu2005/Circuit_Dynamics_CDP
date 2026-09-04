import socket

HOST = "127.0.0.1"
PORT = 52002

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Allow the port to be reused after restarting the program
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

server.bind((HOST, PORT))
server.listen(1)

print("Waiting for GNU Radio on port 52002...")

conn, addr = server.accept()
print(f"GNU Radio connected: {addr}")

try:
    while True:
        data = conn.recv(4096)

        if not data:
            print("GNU Radio disconnected.")
            break

        print("RX >", data.decode("utf-8", errors="ignore"), end="", flush=True)

except KeyboardInterrupt:
    print("\nStopping receiver...")

finally:
    conn.close()
    server.close()