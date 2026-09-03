import socket

HOST = "127.0.0.1"
PORT = 52001

filename = r"D:\UoM Directory\Semester_3\EN2130 Communication Design Project\Ant_SDR\bpsk_file_transfer\ant_tx\bpsk_transmit.txt"

with socket.socket() as s:
    s.connect((HOST, PORT))

    with open(filename, "rb") as f:
        s.sendall(f.read())

print("File sent successfully.")