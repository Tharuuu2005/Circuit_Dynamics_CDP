"""
4 in a Row logic + its radio protocol (no radios needed).

    python tests/game_test.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import connect4 as C  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}", flush=True)


def wins(moves):
    """play columns alternately (player 1 first); return winner or None."""
    b = C.Board()
    for i, c in enumerate(moves):
        who = 1 if i % 2 == 0 else 2
        r = b.drop(c, who)
        if b.winner_line(r, c):
            return who
    return None


check("vertical four wins", wins([0, 1, 0, 1, 0, 1, 0]) == 1)
check("horizontal four wins", wins([0, 0, 1, 1, 2, 2, 3]) == 1)
check("diagonal / wins", wins([0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3]) == 1)
check("diagonal \\ wins", wins([6, 5, 5, 4, 4, 3, 4, 3, 3, 0, 3]) == 1)
check("three in a row is not a win", wins([0, 6, 1, 6, 2]) is None)
b = C.Board()
for c in range(C.COLS):
    for r in range(C.ROWS):
        b.cells[r][c] = 1 + ((c // 2 + r) % 2)        # no four anywhere
check("full board detected", b.full())
check("full column refused", not C.Board().can_drop(7))


# two players connected by a "radio" that can delay and reorder messages
class Net:
    def __init__(self):
        self.q = {1: [], 2: []}

    def sender(self, me):
        dst = 2 if me == 1 else 1
        return lambda obj: self.q[dst].append(dict(obj))

    def deliver(self, games, shuffle=False, dup=False):
        for n in (1, 2):
            msgs = self.q[n]
            self.q[n] = []
            if dup:
                msgs = msgs + msgs
            if shuffle:
                random.shuffle(msgs)
            for m in msgs:
                games[n].on_message(m)


net = Net()
g = {1: C.Game(1, 2, net.sender(1)), 2: C.Game(2, 1, net.sender(2))}
first = g[1].new_game()
net.deliver(g)
check("new game reaches the other player, same first mover", g[2].active and g[2].first == first == 1)
check("only the player whose turn it is can move", g[2].play(3) is not None and g[1].play(3) is None)
# node 2 makes two moves that arrive out of order and twice
net.deliver(g)
g[2].play(4)
g[1].on_message({"g": "c4", "t": "move", "id": g[1].id, "n": 2, "col": 4})   # never happens: illegal early move
net.deliver(g, shuffle=True, dup=True)
check("duplicates ignored, boards equal", g[1].moves == g[2].moves == [3, 4])
g[1].play(3); g[1].play(3)
check("second move by the same player refused", len(g[1].moves) == 3)
# random full games with reordering and duplicates: both boards always agree
random.seed(5)
agree = True
for _ in range(30):
    g[random.choice((1, 2))].new_game()
    net.deliver(g, shuffle=True, dup=True)
    while g[1].active and g[2].active:
        p = g[1].turn
        cols = [c for c in range(C.COLS) if g[p].board.can_drop(c)]
        g[p].play(random.choice(cols))
        net.deliver(g, shuffle=random.random() < 0.5, dup=random.random() < 0.3)
    net.deliver(g)
    agree &= g[1].board.cells == g[2].board.cells and g[1].moves == g[2].moves
    agree &= {g[1].result, g[2].result} in ({"win", "loss"}, {"draw"})
check("30 random games: same board and opposite results on both sides", agree)
check("scores agree on both sides", g[1].score == g[2].score)

# both press "New game" at the same moment: both end up in the same game
net = Net()
g = {1: C.Game(1, 2, net.sender(1)), 2: C.Game(2, 1, net.sender(2))}
g[1].new_game(); g[2].new_game()
net.deliver(g)
check("simultaneous new games converge to one game", g[1].id == g[2].id and g[1].first == g[2].first)
g[g[1].first].play(0)
net.deliver(g)
check("... and play continues", g[1].moves == g[2].moves == [0])
g[2].resign()
net.deliver(g)
check("resign ends the game on both sides", not g[1].active and not g[2].active and g[1].score[1] == 1)

print("RESULT:", "PASS" if ok else "FAIL")
