import socket
import os

# ---------------------------------
# Configuration
# ---------------------------------
HOST = "127.0.0.1"
PORT = 52001

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((HOST, PORT))

print("Connected to GNU Radio transmitter.")

try:
    image_name = input("Enter image filename (example: cat.jpg): ")

    if not os.path.exists(image_name):
        print("File not found!")
        exit()

    # Get image size
    file_size = os.path.getsize(image_name)

    # Send file size first (8 bytes)
    sock.sendall(file_size.to_bytes(8, "big"))

    # Send image data
    with open(image_name, "rb") as f:
        while True:
            data = f.read(1024)
            if not data:
                break
            sock.sendall(data)

    print(f"✓ {image_name} sent successfully!")

finally:
    sock.close()
