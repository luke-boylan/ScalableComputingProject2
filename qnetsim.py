"""
qnetsim.py — QIS-NET Project 2 simulation harness (PROVIDED; do not modify).

A small discrete-event simulator for entanglement distribution networks.
Physics model (see Project 2 specification, section 2):

  * links: Poisson attempt process (rate a), per-attempt success p
  * pairs: born with fidelity F0, decay F(t) = 0.25 + (F0-0.25) exp(-t/T2)
  * swap: consumes two pairs, succeeds w.p. q_swap,
          output fidelity = F_left * F_right * F_swap
  * purification (BBPSSW on depolarising pairs): consumes two pairs,
          instantaneous, succeeds w.p. P_succ, output fidelity F'
  * requests: Poisson arrivals for end-to-end pairs

Deterministic under a given numpy Generator seed.
Only standard library + numpy.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------- events


class EventQueue:
    """Min-heap of (time, seq, callback, payload)."""

    def __init__(self):
        self._heap = []
        self._seq = 0

    def push(self, time, callback, payload=None):
        heapq.heappush(self._heap, (time, self._seq, callback, payload))
        self._seq += 1

    def pop(self):
        return heapq.heappop(self._heap)

    def __len__(self):
        return len(self._heap)


# ---------------------------------------------------------------- physics


def decay_fidelity(f0: float, dt: float, t2: float) -> float:
    """Depolarising decay of Bell-pair fidelity toward the 1/4 floor.
    dt in the same time units as t2 (milliseconds throughout the harness)."""
    if t2 is None or math.isinf(t2):
        return f0
    return 0.25 + (f0 - 0.25) * math.exp(-dt / t2)


def bbpssw(f: float):
    """One recurrence round on two identical depolarising pairs of fidelity f.
    Returns (F', P_success)."""
    e = (1.0 - f) / 3.0
    p_succ = f * f + 2 * f * e + 5 * e * e
    f_out = (f * f + e * e) / p_succ
    return f_out, p_succ


# ---------------------------------------------------------------- entities


@dataclass
class Pair:
    link_id: int          # which link produced it
    born: float           # ms
    f0: float             # fidelity at birth

    def fidelity(self, now: float, t2: float) -> float:
        return decay_fidelity(self.f0, now - self.born, t2)


@dataclass
class Link:
    link_id: int
    nodes: tuple          # (u, v)
    attempt_rate: float   # attempts per ms
    p_success: float      # per attempt
    f_link: float         # fidelity of a fresh pair


@dataclass
class DeliveredPair:
    request_id: int
    delivered_at: float
    fidelity: float
    consumed: int         # raw pairs consumed along the way


@dataclass
class NetworkState:
    """Live inventory handed to the policy at each event."""
    now: float
    pairs: dict = field(default_factory=dict)   # link_id -> [Pair]
    t2: float = math.inf
    q_swap: float = 1.0
    f_swap: float = 1.0
    purification_allowed: bool = False
    rng: object = None
    stats: dict = field(default_factory=lambda: {
        "generated": 0, "swapped_ok": 0, "swapped_fail": 0,
        "purified_ok": 0, "purified_fail": 0, "discarded": 0,
    })

    def current_fidelity(self, pair: Pair) -> float:
        return pair.fidelity(self.now, self.t2)


# ---------------------------------------------------------------- simulator


class Simulator:
    """Discrete-event core. Students drive it via the template runner
    (simulator.py) and their policy (policy.py); they never edit this file."""

    def __init__(self, links, t2, q_swap, f_swap,
                 purification_allowed, seed):
        self.links = {l.link_id: l for l in links}
        self.state = NetworkState(
            now=0.0, t2=t2, q_swap=q_swap, f_swap=f_swap,
            purification_allowed=purification_allowed,
        )
        import numpy as np
        self.rng = np.random.default_rng(seed)
        self.state.rng = self.rng
        self.eq = EventQueue()
        self.delivered = []          # list[DeliveredPair]
        self._next_request_id = 0

    # -- link generation process -------------------------------------

    def start_link(self, link_id):
        self._schedule_next_attempt(link_id, 0.0)

    def _schedule_next_attempt(self, link_id, now):
        link = self.links[link_id]
        dt = self.rng.exponential(1.0 / link.attempt_rate)  # ms
        self.eq.push(now + dt, self._on_attempt, link_id)

    def _on_attempt(self, link_id):
        link = self.links[link_id]
        now = self.state.now
        if self.rng.random() < link.p_success:
            pair = Pair(link_id=link_id, born=now, f0=link.f_link)
            self.state.pairs.setdefault(link_id, []).append(pair)
            self.state.stats["generated"] += 1
            if self.policy is not None:
                self.policy.on_pair_ready(link, pair, self.state, self)
        self._schedule_next_attempt(link_id, now)

    # -- operations available to the policy ---------------------------

    def purify(self, link_id):
        """Consume the two oldest pairs on link_id; on success replace them
        with one purified pair. Returns True on success."""
        inv = self.state.pairs.get(link_id, [])
        if len(inv) < 2:
            return False
        p1, p2 = inv.pop(0), inv.pop(0)
        now = self.state.now
        f_now = min(self.state.current_fidelity(p1),
                    self.state.current_fidelity(p2))
        f_out, p_succ = bbpssw(f_now)
        if self.rng.random() < p_succ:
            inv.append(Pair(link_id=link_id, born=now, f0=f_out))
            self.state.stats["purified_ok"] += 1
            return True
        self.state.stats["purified_fail"] += 1
        return False

    def swap(self, left_link_id, right_link_id, request_id=None,
             left_index=0, right_index=0):
        """Consume one pair on each link (default: the oldest; pass indices
        to select, e.g., the freshest); on success deliver an end-to-end
        pair (two-link chain). Returns the fidelity or None."""
        lin = self.state.pairs.get(left_link_id, [])
        rin = self.state.pairs.get(right_link_id, [])
        if not lin or not rin:
            return None
        pl = lin.pop(min(left_index, len(lin) - 1))
        pr = rin.pop(min(right_index, len(rin) - 1))
        now = self.state.now
        if self.rng.random() < self.state.q_swap:
            f = (self.state.current_fidelity(pl)
                 * self.state.current_fidelity(pr)
                 * self.state.f_swap)
            self.state.stats["swapped_ok"] += 1
            self.delivered.append(DeliveredPair(
                request_id=request_id if request_id is not None else -1,
                delivered_at=now, fidelity=f, consumed=2))
            return f
        self.state.stats["swapped_fail"] += 1
        return None

    def discard(self, link_id, index=0):
        inv = self.state.pairs.get(link_id, [])
        if inv:
            inv.pop(index)
            self.state.stats["discarded"] += 1

    # -- main loop ------------------------------------------------------

    def run(self, sim_time_ms, policy=None):
        self.policy = policy
        for link_id in self.links:
            self.start_link(link_id)
        while len(self.eq):
            t, _, cb, payload = self.eq.pop()
            if t > sim_time_ms:
                break
            self.state.now = t
            cb(payload)
        return self.delivered


# ---------------------------------------------------------------- metrics


def summarise(delivered, sim_time_ms, requests=None):
    """Standard metrics dict from a list of DeliveredPair."""
    out = {
        "delivered": len(delivered),
        "rate": len(delivered) / (sim_time_ms / 1000.0),  # pairs per second
    }
    if delivered:
        fids = [d.fidelity for d in delivered]
        out["mean_fidelity"] = sum(fids) / len(fids)
        lats = sorted(d.delivered_at for d in delivered)
        p95 = lats[min(len(lats) - 1, int(math.ceil(0.95 * len(lats))) - 1)]
        out["p95_delivery_time_ms"] = p95
    else:
        out["mean_fidelity"] = 0.0
        out["p95_delivery_time_ms"] = float("inf")
    return out
