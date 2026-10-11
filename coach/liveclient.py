"""Reads Riot's local Live Client Data API and compacts it for the coach."""

import json
import ssl
import urllib.request

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
# The game serves this on localhost with a self-signed Riot certificate.
_CTX = ssl._create_unverified_context()

BOT_ROLES = {"BOTTOM", "UTILITY"}


def fetch(timeout: float = 1.5) -> dict | None:
    """Full game data, or None when no game is running or it is loading."""
    try:
        with urllib.request.urlopen(URL, timeout=timeout, context=_CTX) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    if not data.get("allPlayers") or "activePlayer" not in data:
        return None
    return data


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _player(p: dict) -> dict:
    s = p.get("scores", {})
    spells = p.get("summonerSpells", {})
    return {
        "champion": p.get("championName", "?"),
        "role": p.get("position") or "unknown",
        "level": p.get("level", 1),
        "kda": f"{s.get('kills', 0)}/{s.get('deaths', 0)}/{s.get('assists', 0)}",
        "cs": s.get("creepScore", 0),
        "ward_score": round(s.get("wardScore", 0)),
        "items": [i.get("displayName", "?") for i in p.get("items", [])],
        "spells": [v.get("displayName", "?") for v in spells.values()],
        "dead": bool(p.get("isDead")),
        "respawn_in": round(p.get("respawnTimer", 0)),
    }


def _pct(value, maximum) -> int | None:
    return round(100 * value / maximum) if maximum else None


def _names(p: dict) -> set[str]:
    keys = ("riotId", "riotIdGameName", "summonerName")
    return {p[k] for k in keys if p.get(k)}


def summarize(data: dict) -> dict | None:
    """Compact state from the active player's point of view."""
    active = data["activePlayer"]
    names = {active.get("riotId"), active.get("summonerName")} - {None, ""}
    raw_me = next((p for p in data["allPlayers"] if _names(p) & names), None)
    if raw_me is None:
        return None

    me = _player(raw_me)
    stats = active.get("championStats", {})
    me["level"] = active.get("level", me["level"])
    me["side"] = "blue" if raw_me.get("team") == "ORDER" else "red"
    me["gold"] = round(active.get("currentGold", 0))
    me["hp_pct"] = _pct(stats.get("currentHealth", 0), stats.get("maxHealth"))
    me["resource_pct"] = _pct(
        stats.get("resourceValue", 0), stats.get("resourceMax"))

    if me["role"] == "unknown":
        my_roles = set()
    else:
        my_roles = BOT_ROLES if me["role"] in BOT_ROLES else {me["role"]}

    # Names the event feed uses for everyone who matters to this lane: the
    # player, lane partner, lane opponents and both junglers.
    lane_names = _names(raw_me)
    allies, enemies = [], []
    for p in data["allPlayers"]:
        if p is raw_me:
            continue
        same_team = p.get("team") == raw_me.get("team")
        (allies if same_team else enemies).append(_player(p))
        if p.get("position") in my_roles | {"JUNGLE"}:
            lane_names |= _names(p)

    return {
        "time": data["gameData"].get("gameTime", 0.0),
        "me": me,
        "lane_allies": [a for a in allies if a["role"] in my_roles],
        "lane_opponents": [e for e in enemies if e["role"] in my_roles],
        "allies": allies,
        "enemies": enemies,
        "lane_names": sorted(lane_names) if my_roles else [],
    }


def state_text(state: dict, prev: dict | None = None) -> str:
    """The state as a few lines of text, far smaller than its JSON.

    Given `prev`, a player's items are listed only if they changed since.
    Both teams can field the same champion, so everything is keyed by side.
    """
    def items(s):
        return {(side, p["champion"]): p["items"]
                for side in ("allies", "enemies") for p in s[side]}

    old = items(prev) if prev else {}
    partners = {p["champion"] for p in state["lane_allies"]}
    opponents = {p["champion"] for p in state["lane_opponents"]}

    def line(p, side=None, tag=""):
        text = f"{p['champion']} L{p['level']} {p['kda']} {p['cs']}cs{tag}"
        if p["dead"]:
            text += f" DEAD {p['respawn_in']}s"
        before = old.get((side, p["champion"])) if side else (
            prev["me"]["items"] if prev else None)
        if before != p["items"]:
            text += f" [{', '.join(p['items']) or 'no items'}]"
        return text

    me = state["me"]
    return "\n".join([
        f"You: {line(me)}, {me['gold']} gold, {me['hp_pct']}% health, "
        f"{me['resource_pct']}% resource, ward score {me['ward_score']}",
        "Allies: " + "; ".join(
            line(p, "allies", " (lane partner)" * (p["champion"] in partners))
            for p in state["allies"]),
        "Enemies: " + "; ".join(
            line(p, "enemies",
                 " (lane opponent)" * (p["champion"] in opponents))
            for p in state["enemies"]),
    ])


def new_events(data: dict, last_id: int) -> list[dict]:
    events = data.get("events", {}).get("Events", [])
    return [e for e in events if e.get("EventID", -1) > last_id]


def champion_names(data: dict) -> dict[str, str]:
    """Maps every player name the event feed may use to a labelled champion."""
    mine = _names(data["activePlayer"])
    me = next((p for p in data["allPlayers"] if _names(p) & mine), {})
    names = {}
    for p in data["allPlayers"]:
        champion = p.get("championName", "?")
        if p is me:
            label = f"{champion} (you)"
        elif p.get("team") == me.get("team"):
            label = f"ally {champion}"
        else:
            label = f"enemy {champion}"
        for name in _names(p):
            names[name] = label
    return names


def describe_event(event: dict, names: dict[str, str]) -> str:
    def who(value):
        if isinstance(value, list):
            return "[" + ", ".join(who(v) for v in value) + "]"
        return names.get(value, str(value))

    detail = ", ".join(
        f"{k}={who(v)}" for k, v in event.items()
        if k not in ("EventID", "EventName", "EventTime") and v not in ("", [])
    )
    text = f"{clock(event.get('EventTime', 0))} {event.get('EventName')}"
    return f"{text} ({detail})" if detail else text
