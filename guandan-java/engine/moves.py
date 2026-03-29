"""Java JAR bridge and legal move list."""

import atexit
import itertools
import json
import os
import shlex
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from .types import Move


def jar_path() -> Path:
    o = os.getenv("GUANDAN_JAVA_JAR", "").strip()
    if o:
        return Path(o)
    root = Path(__file__).resolve().parent.parent
    return root / "guandan-java-action.jar"


class _Bridge:
    def __init__(self):
        self._jar = jar_path()
        self._cmd = os.getenv("GUANDAN_JAVA_ACTION_CMD", "").strip()
        self._mode = os.getenv("GUANDAN_JAVA_MODE", "worker").strip().lower()
        if self._mode not in ("worker", "oneshot"):
            self._mode = "worker"
        self._lock = threading.Lock()
        self._proc = None
        self._req_id = itertools.count(1)
        self._cache_size = int(os.getenv("GUANDAN_JAVA_CACHE_SIZE", "20000"))
        self._cache = OrderedDict()
        atexit.register(self.close)

    def _args(self):
        if self._cmd:
            return shlex.split(self._cmd, posix=False)
        return ["java", "-jar", str(self._jar)]

    def _cache_get(self, key):
        v = self._cache.get(key)
        if v is None:
            return None
        self._cache.move_to_end(key)
        return v

    def _cache_put(self, key, value):
        if self._cache_size <= 0:
            return
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    @staticmethod
    def _key(payload):
        mode = payload.get("mode")
        cards = payload.get("cards")
        if mode == "first" and isinstance(cards, list):
            return ("first", tuple(cards), payload.get("heartsNum"), payload.get("currentRank"))
        if mode == "second" and isinstance(cards, list):
            g = payload.get("greaterAction")
            if isinstance(g, list) and len(g) == 3:
                gc = g[2]
                gk = tuple(gc) if isinstance(gc, list) else gc
                return ("second", tuple(cards), payload.get("heartsNum"), payload.get("currentRank"), g[0], g[1], gk)
        return ("json", json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def _start(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            self._args() + ["--worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False, bufsize=1,
        )

    def _oneshot(self, payload):
        p = subprocess.run(
            self._args(), input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            capture_output=True, text=True, shell=False,
        )
        if p.returncode != 0:
            raise RuntimeError("Java move engine failed: {}".format(p.stderr.strip()))
        try:
            r = json.loads(p.stdout)
        except Exception as e:
            raise RuntimeError("Bad JSON from Java: {}".format(p.stdout)) from e
        a = r.get("actions")
        if not isinstance(a, list):
            raise RuntimeError("Missing actions: {}".format(r))
        return a

    def _send(self, request):
        self._start()
        assert self._proc and self._proc.stdin and self._proc.stdout
        rid = next(self._req_id)
        request = dict(request)
        request["id"] = rid
        self._proc.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = ""
            if self._proc.stderr:
                try:
                    err = self._proc.stderr.read()
                except Exception:
                    pass
            raise RuntimeError("Worker died: {}".format(err.strip()))
        try:
            r = json.loads(line)
        except Exception as e:
            raise RuntimeError("Bad worker JSON: {}".format(line)) from e
        if r.get("id") != rid:
            raise RuntimeError("id mismatch req={} resp={}".format(rid, r.get("id")))
        if not r.get("ok", False):
            raise RuntimeError("Worker error: {}".format(r.get("error")))
        return r

    def invoke(self, payload):
        k = self._key(payload)
        if self._mode == "oneshot":
            with self._lock:
                c = self._cache_get(k)
                if c is not None:
                    return c
            a = self._oneshot(payload)
            with self._lock:
                self._cache_put(k, a)
            return a
        with self._lock:
            c = self._cache_get(k)
            if c is not None:
                return c
            r = self._send(payload)
            a = r.get("actions")
            if not isinstance(a, list):
                raise RuntimeError("Missing actions: {}".format(r))
            self._cache_put(k, a)
            return a

    def close(self):
        with self._lock:
            if self._proc is None:
                return
            try:
                if self._proc.poll() is None and self._proc.stdin and self._proc.stdout:
                    self._proc.stdin.write(json.dumps({"id": -1, "cmd": "shutdown"}, ensure_ascii=False) + "\n")
                    self._proc.stdin.flush()
                    self._proc.stdout.readline()
            except Exception:
                pass
            finally:
                try:
                    if self._proc.poll() is None:
                        self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
            self._cache.clear()


_J = _Bridge()


def _hand_int(hand_cards):
    out = [0 for _ in range(15)]
    suit_row = {"H": 0, "S": 1, "C": 2, "D": 3}
    rank_idx = {"2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6, "9": 7,
                "T": 8, "J": 9, "Q": 10, "K": 11, "A": 12, "B": 13, "R": 14}
    for item in [str(c) for c in hand_cards]:
        if item == "SB":
            row, col = 0, 13
        elif item == "HR":
            row, col = 0, 14
        else:
            row, col = suit_row[item[0]], rank_idx[item[1]]
        lo, hi = 1 << row, 1 << (row + 4)
        old = out[col]
        out[col] = old | hi if old & lo else old | lo
    return out


class Moves:
    def __init__(self):
        self.valid_range = range(0, 1)
        self.action_list = []

    def __len__(self):
        return len(self.action_list)

    def __getitem__(self, item):
        return Move(*self.action_list[item])

    def parse_first_action(self, hand_cards, hearts_num, current_rank):
        if len(hand_cards) == 0:
            self.action_list = []
            return
        self.action_list = _J.invoke({
            "mode": "first", "cards": _hand_int(hand_cards),
            "heartsNum": hearts_num, "currentRank": current_rank,
        })
        self.valid_range = range(0, len(self.action_list))

    def parse_second_action(self, hand_cards, hearts_num, current_rank, greater_action, rank_order, number_order):
        self.action_list = _J.invoke({
            "mode": "second",
            "cards": _hand_int(hand_cards),
            "heartsNum": hearts_num,
            "currentRank": current_rank,
            "greaterAction": [
                greater_action.type, greater_action.rank,
                [str(c) for c in greater_action.cards] if isinstance(greater_action.cards, list) else greater_action.cards,
            ],
        })
        self.valid_range = range(0, len(self.action_list))


# compat
resolve_default_jar_path = jar_path
