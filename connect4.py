"""
4 in a Row (Connect Four) for two radio nodes.

Rules: 7 columns x 6 rows; players take turns dropping a disc into a column; the first to
line up 4 (across, down or diagonally) wins; a full board is a draw.

Over the radio every message is one small verified transfer (kind "game"):
    {"g": "c4", "t": "new",  "id": game id, "first": node that moves first}
    {"g": "c4", "t": "move", "id": game id, "n": move number (0, 1, ...), "col": 0..6}
    {"g": "c4", "t": "resign", "id": game id}
Moves carry their number, so a move that arrives early waits for the one before it and a
duplicate is ignored. If both players start a new game at the same moment, both sides
keep the game with the larger id. The player who did not start the last game starts the
next one.
"""
import random

COLS, ROWS = 7, 6


class Board:
    def __init__(self):
        self.cells = [[0] * COLS for _ in range(ROWS)]     # row 0 = bottom; 0 empty, else node id

    def can_drop(self, col):
        return 0 <= col < COLS and self.cells[ROWS - 1][col] == 0

    def drop(self, col, who):
        """returns the row the disc landed in."""
        for r in range(ROWS):
            if self.cells[r][col] == 0:
                self.cells[r][col] = who
                return r
        raise ValueError("column full")

    def full(self):
        return all(self.cells[ROWS - 1][c] for c in range(COLS))

    def winner_line(self, r, c):
        """the 4+ cells through (r, c) in a line of the same player, or None."""
        who = self.cells[r][c]
        if not who:
            return None
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            line = [(r, c)]
            for sgn in (1, -1):
                rr, cc = r + sgn * dr, c + sgn * dc
                while 0 <= rr < ROWS and 0 <= cc < COLS and self.cells[rr][cc] == who:
                    line.append((rr, cc))
                    rr, cc = rr + sgn * dr, cc + sgn * dc
            if len(line) >= 4:
                return sorted(line)
        return None


class Game:
    """one node's view of the match. send(obj) puts a message on the radio."""

    def __init__(self, me, peer, send):
        self.me, self.peer, self.send = me, peer, send
        self.id = None
        self.board = Board()
        self.first = None
        self.moves = []                  # columns, in order
        self.waiting = {}                # move number -> col (arrived early)
        self.result = None               # None, "win", "loss", "draw", "resigned", "peer resigned"
        self.line = None
        self.last = None                 # (row, col) of the last disc
        self.score = {me: 0, peer: 0}
        self.last_first = None
        self.started_by = None

    # ---------------------------------------------------------------- state
    @property
    def active(self):
        return self.id is not None and self.result is None

    @property
    def turn(self):
        """node to move now (None if no game)."""
        if self.id is None or self.result:
            return None
        other = self.peer if self.first == self.me else self.me
        return self.first if len(self.moves) % 2 == 0 else other

    def my_turn(self):
        return self.turn == self.me

    # ---------------------------------------------------------------- local actions
    def new_game(self):
        """start a new game (the player who did not start the last game moves first)."""
        if self.last_first is None:
            first = self.me
        else:
            first = self.peer if self.last_first == self.me else self.me
        gid = random.randint(1, 2 ** 31 - 1)
        self._reset(gid, first, self.me)
        self.send({"g": "c4", "t": "new", "id": gid, "first": first})
        return first

    def play(self, col):
        """my move. Returns an error text, or None if the move was made."""
        if not self.active:
            return "no game running: start a new one"
        if not self.my_turn():
            return "wait for the other player's move"
        if not self.board.can_drop(col):
            return "that column is full"
        n = len(self.moves)
        self._apply(col, self.me)
        self.send({"g": "c4", "t": "move", "id": self.id, "n": n, "col": col})
        return None

    def resign(self):
        if self.active:
            self.result = "resigned"
            self.score[self.peer] += 1
            self.send({"g": "c4", "t": "resign", "id": self.id})

    # ---------------------------------------------------------------- radio
    def on_message(self, m):
        """apply a message from the peer. Returns a short event text for the chat, or None."""
        t = m.get("t")
        if t == "new":
            gid = int(m["id"])
            if self.id is not None and gid == self.id:
                return None
            if self.active and not self.moves and self.started_by == self.me and self.id > gid:
                return None              # both started at once: the larger id wins on both sides
            self._reset(gid, int(m["first"]), self.peer)
            return "new game" + (": you move first" if self.first == self.me else ": they move first")
        if m.get("id") != self.id:
            return None
        if t == "resign" and self.active:
            self.result = "peer resigned"
            self.score[self.me] += 1
            return "they resigned: you win"
        if t == "move":
            n, col = int(m["n"]), int(m["col"])
            if n < len(self.moves):
                return None              # duplicate
            self.waiting[n] = col
            out = None
            while len(self.moves) in self.waiting and self.active:
                c = self.waiting.pop(len(self.moves))
                if self.turn != self.peer or not self.board.can_drop(c):
                    break                # not a legal move for them: ignore
                self._apply(c, self.peer)
                out = self._result_text()
            return out
        return None

    # ---------------------------------------------------------------- internals
    def _reset(self, gid, first, started_by):
        self.id, self.first, self.started_by = gid, first, started_by
        self.last_first = first
        self.board = Board()
        self.moves, self.waiting = [], {}
        self.result, self.line, self.last = None, None, None

    def _apply(self, col, who):
        r = self.board.drop(col, who)
        self.moves.append(col)
        self.last = (r, col)
        line = self.board.winner_line(r, col)
        if line:
            self.line = line
            self.result = "win" if who == self.me else "loss"
            self.score[who] += 1
        elif self.board.full():
            self.result = "draw"

    def _result_text(self):
        return {"win": "you win!", "loss": "they win", "draw": "draw: the board is full"}.get(self.result)
