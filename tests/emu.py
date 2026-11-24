"""
Emulated radio link for tests and tools/fake_radio.py (no GNU Radio): two directions, each a
transmitter with the same three priority queues as the real framer (control > stream > data),
a fixed air rate, a little latency and random frame loss. Frames are delivered as UDP to the
other node exactly like the radio does.
"""
import collections
import heapq
import random
import socket
import threading
import time

import config

MAGIC, T_DATA, T_STREAM = 0xA5, 1, 6


class Direction(threading.Thread):
    def __init__(self, listen_port, deliver_port, byte_rate, latency=0.004, loss=0.0,
                 max_stream=48, max_queue=2048):
        super().__init__(daemon=True)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.s.bind(("127.0.0.1", listen_port))
        self.s.settimeout(0.0005)
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dst = ("127.0.0.1", deliver_port)
        self.rate, self.lat, self.loss = byte_rate, latency, loss
        self.q = (collections.deque(), collections.deque(), collections.deque())
        self.max_stream, self.max_queue = max_stream, max_queue
        self.air_free = 0.0
        self.heap = []
        self.n = self.lost = self.dropped = 0
        self.tap = None                  # tap(pkt): sees every frame put on the air (attacker)
        self.running = True

    def _enqueue(self, pkt):
        typ = pkt[1] if len(pkt) > 1 and pkt[0] == MAGIC else T_DATA
        if typ == T_STREAM:
            q = self.q[1]
            while len(q) >= self.max_stream:
                q.popleft()
                self.dropped += 1
        else:
            q = self.q[2] if typ == T_DATA else self.q[0]
            if len(q) >= self.max_queue:
                self.dropped += 1
                return
        q.append(pkt)

    def inject(self, pkt):
        """an attacker transmitting on this frequency."""
        heapq.heappush(self.heap, (time.perf_counter() + self.lat, -1, pkt))

    def run(self):
        k = 0
        while self.running:
            try:
                while True:
                    self._enqueue(self.s.recv(4096))
            except (socket.timeout, BlockingIOError):
                pass
            except OSError:
                pass
            now = time.perf_counter()
            # put the next frame on the air when the transmitter is free
            while now >= self.air_free:
                pkt = None
                for q in self.q:
                    if q:
                        pkt = q.popleft()
                        break
                if pkt is None:
                    break
                start = max(now, self.air_free)
                self.air_free = start + (len(pkt) + config.PHY_OVERHEAD) / self.rate
                self.n += 1
                if self.tap:
                    self.tap(pkt)
                if random.random() < self.loss:
                    self.lost += 1
                else:
                    k += 1
                    heapq.heappush(self.heap, (self.air_free + self.lat, k, pkt))
            while self.heap and self.heap[0][0] <= now:
                self.out.sendto(heapq.heappop(self.heap)[2], self.dst)


def link(ports1, ports2, byte_rate, loss=0.0, latency=0.004):
    """ports = (tx_port, rx_port) of each node -> (dir 1->2, dir 2->1), started."""
    a = Direction(ports1[0], ports2[1], byte_rate, latency, loss)
    b = Direction(ports2[0], ports1[1], byte_rate, latency, loss)
    a.start()
    b.start()
    return a, b
