"""
policy.py — QIS-NET Project 2 policy (STUDENT FILE: replace the baseline).

The policy decides, each time a fresh pair arrives on a link, what to do:
swap immediately, purify first, hold, or discard aged pairs. Your variant's
SLOs (rate, fidelity, p95 latency) will typically NOT be met by the
baseline policy below.

Design space (see spec section 3.2):
  * purify while the freshest link fidelity is below a threshold AND a
    second pair is available on the same link;
  * hold vs swap rules (e.g., wait for a purified partner);
  * discard pairs whose current fidelity has fallen below a floor.
"""

import math


class BaselinePolicy:
    """Swap-as-soon-as-possible; never purify. PROVIDED as the baseline.
    It meets the reference scenarios' expectations but is deliberately
    insufficient for most variant SLOs."""

    def __init__(self, params):
        self.params = params
        # map each repeater node to its adjacent link pairs for swapping
        self.chains = params["policy"]["swap_chains"]  # list of [left, right]

    def on_pair_ready(self, link, pair, state, sim):
        for left, right in self.chains:
            if state.pairs.get(left) and state.pairs.get(right):
                sim.swap(left, right)


def make_policy(params):
    """Return your policy object. Replace BaselinePolicy with your own class
    (same constructor signature and on_pair_ready hook)."""
    return BaselinePolicy(params)
