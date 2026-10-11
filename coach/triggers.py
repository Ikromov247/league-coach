"""Decides, without any model, when the coach is worth asking."""

import math

from .config import (
    GOLD_ALERT, HEARTBEAT_LANING, HEARTBEAT_LATER, LEVEL_SPIKES, LOW_HP_PCT,
    OBJECTIVE_WARNING,
)
from .liveclient import describe_event

# FirstBlood and Multikill only restate a ChampionKill.
NOISE_EVENTS = {"GameStart", "MinionsSpawning", "FirstBlood", "Multikill"}


class Triggers:
    def __init__(self, mode: dict):
        self.mode = mode
        self.prev: dict | None = None
        self.last_kill: dict[str, float] = {}
        self.announced: set[tuple[str, int]] = set()
        self.low_hp = False
        self.gold_flagged = False
        self.last_heartbeat = 0.0

    def laning(self, t: float) -> bool:
        return t < self.mode["laning_ends"]

    def _next_spawn(self, obj: dict, t: float) -> float | None:
        first = obj["first"]
        if t < first:
            return first
        if "every" in obj:
            return first + math.ceil((t - first) / obj["every"]) * obj["every"]
        killed = self.last_kill.get(obj["event"])
        return killed + obj["respawn"] if killed is not None else None

    def _concerns_player(self, event: dict, state: dict) -> bool:
        """In lane, a kill matters only if this lane or a jungler is in it."""
        lane = set(state["lane_names"])
        if (event.get("EventName") != "ChampionKill" or not lane
                or not self.laning(state["time"])):
            return True
        involved = {event.get("KillerName"), event.get("VictimName"),
                    *event.get("Assisters", [])}
        return bool(involved & lane)

    def check(self, state: dict, events: list[dict],
              names: dict[str, str]) -> list[str]:
        """Reasons to consult the coach now; empty means stay quiet."""
        t, me, reasons = state["time"], state["me"], []

        for e in events:
            if e.get("EventName") in NOISE_EVENTS:
                continue
            self.last_kill[e["EventName"]] = e.get("EventTime", t)
            if self._concerns_player(e, state):
                reasons.append("event: " + describe_event(e, names))

        if self.prev:
            reasons += self._changes(self.prev, state)
        self.prev = state

        low = (me["hp_pct"] is not None and me["hp_pct"] < LOW_HP_PCT
               and not me["dead"])
        if low and not self.low_hp:
            reasons.append(f"your health dropped to {me['hp_pct']}%")
        self.low_hp = low

        if me["gold"] >= GOLD_ALERT and not self.gold_flagged:
            reasons.append(f"you hold {me['gold']} unspent gold")
            self.gold_flagged = True

        for obj in self.mode["objectives"]:
            spawn = self._next_spawn(obj, t)
            if spawn is None or not 0 < spawn - t <= OBJECTIVE_WARNING:
                continue
            key = (obj["name"], round(spawn))
            if key not in self.announced:
                self.announced.add(key)
                reasons.append(f"{obj['name']} spawns in {round(spawn - t)}s")

        beat = HEARTBEAT_LANING if self.laning(t) else HEARTBEAT_LATER
        if reasons or t - self.last_heartbeat >= beat:
            self.last_heartbeat = t
            reasons = reasons or ["routine check"]
        return reasons

    def _changes(self, prev: dict, state: dict) -> list[str]:
        out, me, was = [], state["me"], prev["me"]
        if me["level"] > was["level"] and me["level"] in LEVEL_SPIKES:
            out.append(f"you reached level {me['level']}")
        if me["items"] != was["items"]:
            self.gold_flagged = False
        if was["dead"] and not me["dead"]:
            out.append("you respawned")

        if self.laning(state["time"]):
            before = {o["champion"]: o for o in prev["lane_opponents"]}
            for opp in state["lane_opponents"]:
                old, name = before.get(opp["champion"]), opp["champion"]
                if not old:
                    continue
                if opp["level"] > old["level"] and opp["level"] in LEVEL_SPIKES:
                    out.append(f"enemy {name} reached level {opp['level']}")
                if opp["items"] != old["items"]:
                    out.append(f"enemy {name} now has "
                               f"{', '.join(opp['items']) or 'no items'}")
            return out

        def alive(s):
            allies = sum(not p["dead"] for p in s["allies"] + [s["me"]])
            return allies, sum(not p["dead"] for p in s["enemies"])

        if alive(state) != alive(prev):
            a, e = alive(state)
            if a != e:
                out.append(f"players alive: {a} allies vs {e} enemies")
        return out
