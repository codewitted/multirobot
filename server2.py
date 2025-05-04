# ------------------------------
# server.py  (Central Agent)
# ------------------------------
"""Central agent that announces tasks, evaluates bids, detects ties and triggers
peer‑to‑peer negotiation between robots.  Run first:

$ python server.py --tasks tasks.json --robots R1:6001 R2:6002
"""

import argparse
import json
import socket
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

HOST = "127.0.0.1"  # local only for coursework
BUFFER = 4096

class CentralAgent:
    def __init__(self, tasks, robot_map, port=5000):
        self.tasks = tasks                 # list of dicts
        self.robot_map = robot_map         # {robot_id: listen_port}
        self.port = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind((HOST, port))
        self.server.listen(len(robot_map))

        self.clients = {}                  # {robot_id: conn}
        self.balances = defaultdict(lambda: 100.0)
        self.task_queue = tasks.copy()
        self.lock = threading.Lock()

    # ------------------------------------------------------------
    # Networking helpers
    # ------------------------------------------------------------
    def _send(self, conn, msg):
        encoded = (json.dumps(msg) + "\n").encode()
        conn.sendall(encoded)

    def _recv(self, conn):
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(BUFFER)
            if not chunk:
                raise ConnectionError("client closed socket")
            data += chunk
        return json.loads(data.decode())

    # ------------------------------------------------------------
    # Auction workflow
    # ------------------------------------------------------------
    def broadcast_cfp(self):
        """Broadcast call‑for‑proposal with full task list."""
        cfp_msg = {
            "performative": "cfp",
            "sender": "central",
            "receiver": list(self.robot_map.keys()),
            "conversation_id": str(uuid.uuid4()),
            "content": {"tasks": self.task_queue},
        }
        for conn in self.clients.values():
            self._send(conn, cfp_msg)

    def run(self):
        print(f"CentralAgent listening on {HOST}:{self.port} …")
        # accept all robot connections first
        for _ in self.robot_map:
            conn, _ = self.server.accept()
            hello = self._recv(conn)
            rid = hello["sender"]
            self.clients[rid] = conn
            print(f"Robot {rid} connected")
        # announce tasks once all are connected
        self.broadcast_cfp()
        # main event loop
        active_bids = defaultdict(dict)     # {task_id: {rid: bid}}
        finished = 0
        while finished < len(self.tasks):
            for rid, conn in list(self.clients.items()):
                try:
                    if conn.recv(1, socket.MSG_PEEK):  # non‑blocking peek
                        msg = self._recv(conn)
                        pf = msg.get("performative")
                        if pf == "propose":
                            self.handle_propose(msg, active_bids)
                        elif pf == "inform-done":
                            finished += 1
                            task_id = msg["content"]["task_id"]
                            self._task_complete(rid, task_id)
                        elif pf == "negotiation-result":
                            self._handle_negotiation(msg)
                except BlockingIOError:
                    continue
            time.sleep(0.05)
        print("All tasks complete. Ledger:")
        for rid, bal in self.balances.items():
            print(f"  {rid}: {bal}")

    def handle_propose(self, msg, active_bids):
        rid = msg["sender"]
        task_id = msg["content"]["task_id"]
        bid = msg["content"]["bid"]
        active_bids[task_id][rid] = bid
        # once we have all bids for this task, evaluate
        if len(active_bids[task_id]) == len(self.robot_map):
            bids = active_bids.pop(task_id)
            winners = self._detect_tie(bids)
            if len(winners) == 1:
                self._award_task(task_id, winners[0], bids[winners[0]])
            else:
                self._trigger_negotiation(task_id, winners, bids)

    # ------------------------------------------------------------
    # Helper functions
    # ------------------------------------------------------------
    def _detect_tie(self, bids):
        low = min(bids.values())
        return [r for r, b in bids.items() if b == low]

    def _award_task(self, task_id, winner, price):
        self.balances[winner] -= price              # tax
        msg = {
            "performative": "accept-proposal",
            "sender": "central",
            "receiver": winner,
            "content": {"task_id": task_id, "price": price},
        }
        self._send(self.clients[winner], msg)
        # losers get rejection
        for rid, conn in self.clients.items():
            if rid != winner:
                rej = {
                    "performative": "reject-proposal",
                    "sender": "central",
                    "receiver": rid,
                    "content": {"task_id": task_id},
                }
                self._send(conn, rej)

    def _trigger_negotiation(self, task_id, winners, bids):
        """Tell tied robots to negotiate directly."""
        for rid in winners:
            peer = [r for r in winners if r != rid][0]
            msg = {
                "performative": "inform-tie",
                "sender": "central",
                "receiver": rid,
                "content": {
                    "task_id": task_id,
                    "peer_id": peer,
                    "peer_port": self.robot_map[peer],
                    "start_price": bids[rid],
                },
            }
            self._send(self.clients[rid], msg)

    def _handle_negotiation(self, msg):
        task_id = msg["content"]["task_id"]
        winner = msg["content"]["winner"]
        price = msg["content"]["price"]
        self._award_task(task_id, winner, price)

    def _task_complete(self, rid, task_id):
        reward = 5.0   # fixed reward for demo
        self.balances[rid] += reward
        ack = {
            "performative": "inform-reward",
            "sender": "central",
            "receiver": rid,
            "content": {"task_id": task_id, "reward": reward, "balance": self.balances[rid]},
        }
        self._send(self.clients[rid], ack)

# ------------------------------
# robot_agent.py  (used by R1 & R2)
# ------------------------------
"""Robot agent that can bid in auctions and negotiate on ties.
Run:
$ python robot_agent.py --id R1 --start 0 0 --listen 6001
$ python robot_agent.py --id R2 --start 9 9 --listen 6002
"""

import argparse
import json
import math
import random
import socket
import threading
import time
from pathlib import Path

HOST = "127.0.0.1"
BUFFER = 4096
DELTA = 0.9          # discount factor per negotiation round
MAX_ROUNDS = 3

class RobotAgent:
    def __init__(self, robot_id, start_x, start_y, central_port, listen_port):
        self.rid = robot_id
        self.pos = (start_x, start_y)
        self.central_port = central_port
        self.listen_port = listen_port
        self.balance = 100.0

        # central connection (REQ‑like)
        self.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conn.connect((HOST, central_port))
        hello = {"performative": "hello", "sender": self.rid}
        self._send(self.conn, hello)

        # listener for peer negotiation
        self.peer_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.peer_server.bind((HOST, listen_port))
        self.peer_server.listen(1)

        # state
        self.tasks = []
        self.current_task = None
        self.poll_thread = threading.Thread(target=self._poll_central, daemon=True)
        self.poll_thread.start()

    # ------------------------------------------------------------------
    # Networking helpers
    # ------------------------------------------------------------------
    def _send(self, conn, msg):
        conn.sendall((json.dumps(msg) + "\n").encode())

    def _recv(self, conn):
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(BUFFER)
            if not chunk:
                raise ConnectionError("socket closed")
            data += chunk
        return json.loads(data.decode())

    # ------------------------------------------------------------------
    # Main FSM
    # ------------------------------------------------------------------
    def _poll_central(self):
        while True:
            msg = self._recv(self.conn)
            pf = msg["performative"]
            if pf == "cfp":
                self.tasks = msg["content"]["tasks"]
                threading.Thread(target=self._bidding_loop, daemon=True).start()
            elif pf == "accept-proposal":
                self._execute_task(msg["content"]["task_id"])
            elif pf == "reject-proposal":
                pass  # just ignore
            elif pf == "inform-tie":
                threading.Thread(target=self._negotiate, args=(msg,), daemon=True).start()
            elif pf == "inform-reward":
                self.balance = msg["content"]["balance"]
                print(f"{self.rid} finished task {msg['content']['task_id']} — new balance {self.balance}")

    def _bidding_loop(self):
        for task in self.tasks:
            bid = self._compute_bid(task)
            proposal = {
                "performative": "propose",
                "sender": self.rid,
                "receiver": "central",
                "content": {"task_id": task["task_id"], "bid": bid},
            }
            self._send(self.conn, proposal)
            # wait until accept/reject/tie comes back in polling thread
            time.sleep(0.2)

    def _compute_bid(self, task):
        tx, ty = task["x"], task["y"]
        dist = abs(tx - self.pos[0]) + abs(ty - self.pos[1])
        base_cost = dist * 0.1 + 0.3
        jitter = random.uniform(0, 0.05)
        return round(base_cost + jitter, 2)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------
    def _execute_task(self, task_id):
        print(f"{self.rid} executing task {task_id} …")
        time.sleep(1.0)  # pretend to navigate & act
        done = {
            "performative": "inform-done",
            "sender": self.rid,
            "receiver": "central",
            "content": {"task_id": task_id},
        }
        self._send(self.conn, done)

    # ------------------------------------------------------------------
    # Negotiation
    # ------------------------------------------------------------------
    def _negotiate(self, tie_msg):
        task_id = tie_msg["content"]["task_id"]
        peer_id = tie_msg["content"]["peer_id"]
        peer_port = tie_msg["content"]["peer_port"]
        offer = tie_msg["content"]["start_price"]

        # decide initiator: lexicographic larger name starts
        initiator = self.rid > peer_id
        if initiator:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((HOST, peer_port))
        else:
            sock, _ = self.peer_server.accept()

        for rnd in range(MAX_ROUNDS):
            if initiator or rnd > 0:  # initiator sends first
                offer = round(offer * DELTA, 2)
                msg = json.dumps({"offer": offer}) + "\n"
                sock.sendall(msg.encode())
            reply = self._recv(sock)
            if "agree" in reply:
                winner = reply["agree"]
                break
            offer = reply["offer"]  # counter from peer
            if rnd == MAX_ROUNDS - 1:
                # timeout — decide randomly
                winner = random.choice([self.rid, peer_id])

        sock.close()
        # tell central who won (only winner notifies)
        if winner == self.rid:
            result = {
                "performative": "negotiation-result",
                "sender": self.rid,
                "receiver": "central",
                "content": {"task_id": task_id, "winner": self.rid, "price": offer},
            }
            self._send(self.conn, result)

# ------------------------------
# R1.py and R2.py thin wrappers
# ------------------------------
"""Run two processes in separate terminals:
$ python R1.py
$ python R2.py
"""

# R1.py
if __name__ == "__main__" and False:
    RobotAgent("R1", 0, 0, 5000, 6001)

# R2.py
if __name__ == "__main__" and False:
    RobotAgent("R2", 9, 9, 5000, 6002)

# Users should instead invoke robot_agent.py with arguments:
#   python robot_agent.py --id R1 --start 0 0 --listen 6001
#   python robot_agent.py --id R2 --start 9 9 --listen 6002

# ------------------------------
# __main__ glue (keeps file import‑friendly)
# ------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="role")

    p_server = sub.add_parser("server")
    p_server.add_argument("--tasks", default="tasks.json")
    p_server.add_argument("--robots", nargs="+", required=True,
                          help="robot spec R1:6001 R2:6002 …")

    p_robot = sub.add_parser("robot")
    p_robot.add_argument("--id", required=True)
    p_robot.add_argument("--start", nargs=2, type=int, metavar=("X", "Y"), required=True)
    p_robot.add_argument("--listen", type=int, required=True)
    p_robot.add_argument("--central", type=int, default=5000)

    args = parser.parse_args()

    if args.role == "server":
        robot_map = {spec.split(":"\
