"""Tunable settings. Objective timers change between patches: check them."""

POLL_INTERVAL = 1.0        # seconds between local API polls
VISION_INTERVAL = 4.0      # seconds between screenshots
SNAPSHOT_INTERVAL = 10     # game seconds between logged state snapshots
MIN_COACH_GAP = 10.0       # seconds between coach calls
HEARTBEAT_LANING = 20      # game seconds between routine checks in lane
HEARTBEAT_LATER = 45       # ...and after laning
OBJECTIVE_WARNING = 60     # warn this many seconds before a spawn
GOLD_ALERT = 1300          # unspent gold worth a recall check
LOW_HP_PCT = 35
LEVEL_SPIKES = {2, 3, 6, 11, 16}
SPEAK_COOLDOWN = 40        # game seconds of quiet after a line, unless urgent
FRAME_SAVE_INTERVAL = 0   # game seconds between saved debug screenshots; 0 = off

# Minimap is assumed bottom-right; this is its side as a fraction of screen
# height. Raise it if you use a larger minimap scale.
MINIMAP_FRACTION = 0.3
FRAME_WIDTH = 1280
SPEECH_RATE = 205          # words per minute for `say`

# USD per million tokens (input, output), for the end-of-game cost printout.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Objectives: "event" + "respawn" means the timer restarts on that kill
# event; "every" means a fixed schedule. Objectives without a reliable timer
# are left out and the coach relies on the event feed and the screen.
MODES = {
    "classic": {
        "laning_ends": 14 * 60,
        "objectives": [
            {"name": "Dragon", "first": 5 * 60, "event": "DragonKill",
             "respawn": 5 * 60},
            {"name": "Baron", "first": 20 * 60, "event": "BaronKill",
             "respawn": 6 * 60},
        ],
        "notes": (
            "Standard Summoner's Rift (2026 season): minions spawn at 0:30, "
            "Atakhan and Feats of Strength no longer exist, Baron spawns at "
            "20:00."
        ),
    },
    "swiftplay": {
        "laning_ends": 8 * 60,
        "objectives": [
            {"name": "Baron", "first": 12 * 60, "event": "BaronKill",
             "respawn": 6 * 60},
            {"name": "Elder Dragon", "first": 15 * 60, "every": 6 * 60},
        ],
        "notes": (
            "Swiftplay: a shorter, accelerated mode. Everyone starts at "
            "level 3 with bonus starting gold (about 1100), so level 1-2 "
            "cheese does not exist and first items arrive early. Gold and XP "
            "come faster and the losing team gets catch-up gold. There are "
            "no Void Grubs and no Rift Herald. At most two elemental dragons "
            "spawn and a team needs both for Soul. Baron spawns at 12:00. "
            "Elder Dragon spawns at 15:00 and every 6 minutes after. Turret "
            "plates fall earlier and laning is short, so mid-game macro "
            "starts around 8 minutes."
        ),
    },
}


def detect_mode(data: dict) -> str:
    game = data.get("gameData", {})
    if "SWIFT" in str(game.get("gameMode", "")).upper():
        return "swiftplay"
    level = data.get("activePlayer", {}).get("level", 1)
    if game.get("gameTime", 0) < 90 and level >= 3:
        return "swiftplay"
    return "classic"
