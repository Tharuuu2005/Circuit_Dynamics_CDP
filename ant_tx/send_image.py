import socket
import os

HOST = "127.0.0.1"
PORT = 52001

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HOST, PORT))

image_path = input("Enter image filename: ") #replece this with the path

if not os.path.exists(image_path):
    print("File not found.")
    sock.close()
    exit()

with open(image_path, "rb") as f:
    while True:
        chunk = f.read(1024)
        if not chunk:
            break
        sock.sendall(chunk)

print("Image sent successfully.")
sock.close()
