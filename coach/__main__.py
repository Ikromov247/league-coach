"""Run with: uv run python -m coach"""

import argparse
import base64
import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import anthropic

from . import capture, llm, voice
from .config import (
    FRAME_SAVE_INTERVAL, MIN_COACH_GAP, MODES, POLL_INTERVAL,
    SNAPSHOT_INTERVAL, SPEAK_COOLDOWN, VISION_INTERVAL,
    detect_mode,
)
from .liveclient import (
    champion_names, clock, describe_event, fetch, new_events, state_text,
    summarize,
)
from .triggers import Triggers

DATA = Path(__file__).resolve().parent.parent / "data"
PROFILE = DATA / "profile.md"


class GameLog:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self._file = (folder / "log.jsonl").open("a")
        self._lock = threading.Lock()

    def write(self, kind: str, t: float, **fields) -> None:
        line = json.dumps({"kind": kind, "t": round(t, 1), **fields})
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()


def match_context(state: dict, mode_name: str, lane_plan: str = "") -> str:
    def line(p):
        return f"{p['champion']} ({p['role']}, {', '.join(p['spells'])})"

    me = state["me"]
    parts = [
        f"Game mode: {mode_name}. {MODES[mode_name]['notes']}",
        f"Player: {line(me)}",
        "Allies: " + "; ".join(line(p) for p in state["allies"]),
        "Enemies: " + "; ".join(line(p) for p in state["enemies"]),
        "Lane opponent(s): " + ("; ".join(
            p["champion"] for p in state["lane_opponents"]) or "unknown"),
    ]
    if PROFILE.exists():
        parts.append("Player profile from earlier games:\n"
                     + PROFILE.read_text())
    if lane_plan:
        parts.append("Lane plan from the pre-game brief:\n" + lane_plan)
    return "\n\n".join(parts)


class Session:
    def __init__(self, data: dict, args):
        self.args = args
        self.state = summarize(data)
        # Fixed for the game: some champions (Neeko) report a disguise's
        # name and level through the API.
        self.champion = self.state["me"]["champion"]
        self.level = self.state["me"]["level"]
        self.mode_name = (args.mode if args.mode != "auto"
                          else detect_mode(data))
        self.triggers = Triggers(MODES[self.mode_name])
        self.log = GameLog(DATA / "games" / datetime.now().strftime(
            "%Y%m%d-%H%M%S"))
        self.context = match_context(self.state, self.mode_name)
        self.screen: dict | None = None
        self.screen_time = 0.0
        self.recent = deque(maxlen=8)
        self.pending: list[str] = []
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.coach_job = None
        self.last_coach = 0.0
        self.last_spoken = -SPEAK_COOLDOWN
        self.running = True
        self.result = "unknown"

    # ---- background work

    def brief(self) -> None:
        try:
            out = llm.pregame_brief(self.context)
        except anthropic.APIError as e:
            print(f"[brief] failed: {e}")
            return
        if not out:
            return
        self.context = match_context(
            self.state, self.mode_name, out["lane_plan"])
        self.log.write("brief", self.state["time"], **out)
        print(f"\n[brief] {out['spoken']}\n\n[lane plan] {out['lane_plan']}\n")
        voice.say(out["spoken"])

    def watch_screen(self) -> None:
        blind, last_saved = 0, -FRAME_SAVE_INTERVAL
        frames = self.log.folder / "frames"
        while self.running:
            started = time.monotonic()
            state = self.state
            if not state["me"]["dead"]:
                try:
                    frame, minimap = capture.grab()
                    out = llm.read_screen(frame, minimap, state)
                except anthropic.APIError as e:
                    print(f"[screen] failed: {e}")
                    out = None
                if out:
                    self.screen, self.screen_time = out, state["time"]
                    self.log.write("screen", state["time"], **out)
                    if (FRAME_SAVE_INTERVAL
                            and state["time"] - last_saved
                            >= FRAME_SAVE_INTERVAL):
                        last_saved = state["time"]
                        frames.mkdir(exist_ok=True)
                        stamp = clock(state["time"]).replace(":", "m")
                        for name, image in (("frame", frame),
                                            ("minimap", minimap)):
                            (frames / f"{stamp}_{name}.jpg").write_bytes(
                                base64.b64decode(image))
                    blind = 0 if out["gameplay_visible"] else blind + 1
                    if blind == 8:
                        print("[screen] The game has not been visible for a "
                              "while. Check that this terminal has Screen "
                              "Recording permission and the game is on the "
                              "primary display.")
            time.sleep(max(0.0, VISION_INTERVAL
                           - (time.monotonic() - started)))

    def coach(self, state: dict, reasons: list[str]) -> None:
        t = state["time"]
        laning = self.triggers.laning(t)
        phase = "after laning"
        if laning:
            def names(players):
                return " and ".join(p["champion"] for p in players)

            me = state["me"]
            partner = names(state["lane_allies"])
            phase = f"laning. The player is {me['role']}" + (
                f" with their {partner}" if partner else "") + (
                f" against {names(state['lane_opponents'])}"
                if state["lane_opponents"] else "")
        # my_location is logged but withheld: it has been wrong too often.
        seen = {k: v for k, v in (self.screen or {}).items()
                if k not in ("my_location", "gameplay_visible")
                and v not in (-1, "", [], "unknown", "unseen",
                              "none visible")}
        screen = (f"(captured at {clock(self.screen_time)})\n"
                  + json.dumps(seen) if seen else "nothing readable")
        quiet = round(t - self.last_spoken)
        said = "\n".join(f"{clock(at)} {text}" for at, text in self.recent)
        situation = "\n\n".join([
            f"Game time {clock(t)}, phase: {phase}.",
            f"You last spoke {quiet} seconds ago.",
            "Why this check was triggered:\n- " + "\n- ".join(reasons),
            "Client data (level, K/D/A, CS, items):\n" + state_text(state),
            "Screen description: " + screen,
            "What you said recently:\n" + (said or "nothing yet"),
        ])
        started = time.monotonic()
        try:
            out = llm.live_advice(self.context, situation)
        except anthropic.APIError as e:
            print(f"[coach] failed: {e}")
            return
        took = round(time.monotonic() - started, 1)
        if not out:
            return
        spoken = bool(out["speak"] and out["advice"]) and (
            out["urgent"] or quiet >= SPEAK_COOLDOWN)
        self.log.write("advice", t, reasons=reasons, latency=took,
                       spoken=spoken, **out)
        if spoken:
            self.last_spoken = t
            print(f"[{clock(t)}] {out['advice']}  ({took}s)")
            self.recent.append((t, out["advice"]))
            voice.say(out["advice"])

    # ---- main loop

    def run(self) -> None:
        print(f"Game found: {self.state['me']['champion']} "
              f"({self.state['me']['role']}), mode {self.mode_name}. "
              f"Logging to {self.log.folder}")
        brief_job = self.pool.submit(self.brief)
        if not self.args.brief_only:
            threading.Thread(target=self.watch_screen, daemon=True).start()

        last_event, last_snapshot, misses = -1, -SNAPSHOT_INTERVAL, 0
        while misses < 8:
            try:
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                print("\nStopped early; reviewing what was logged.")
                break
            data = fetch()
            state = summarize(data) if data else None
            if not state:
                misses += 1
                continue
            misses = 0
            self.level = max(self.level, state["me"]["level"])
            state["me"]["champion"] = self.champion
            state["me"]["level"] = self.level
            self.state = state
            t = state["time"]

            names = champion_names(data)
            events = new_events(data, last_event)
            for e in events:
                last_event = max(last_event, e["EventID"])
                self.log.write("event", e.get("EventTime", t),
                               text=describe_event(e, names))
                if e.get("EventName") == "GameEnd":
                    self.result = e.get("Result", "unknown")
            if t - last_snapshot >= SNAPSHOT_INTERVAL:
                last_snapshot = t
                self.log.write("state", t, state=state)
            if self.result != "unknown":
                break
            if self.args.brief_only:
                continue

            for reason in self.triggers.check(state, events, names):
                if reason not in self.pending:
                    self.pending.append(reason)
            idle = self.coach_job is None or self.coach_job.done()
            if (self.pending and idle and brief_job.done()
                    and time.monotonic() - self.last_coach >= MIN_COACH_GAP):
                self.last_coach = time.monotonic()
                reasons = [r for r in self.pending
                           if r != "routine check"] or self.pending
                self.pending = []
                self.coach_job = self.pool.submit(self.coach, state, reasons)

        self.running = False
        self.pool.shutdown(wait=True)
        self.log.write("end", self.state["time"], result=self.result)
        print(f"\nGame over ({self.result}).")
        review(self.log.folder, self.context)


def review(folder: Path, context: str = "") -> None:
    """Post-game review from a game's log; also updates the profile."""
    lines, last_state, last_screen, shown = [], -30.0, -30.0, None
    for raw in (folder / "log.jsonl").read_text().splitlines():
        rec = json.loads(raw)
        kind, t = rec.pop("kind"), rec.pop("t")
        if kind == "state":
            if t - last_state < 30:
                continue
            last_state = t
            lines.append(f"{clock(t)} state:\n"
                         + state_text(rec["state"], shown))
            shown = rec["state"]
            continue
        elif kind == "screen":
            if t - last_screen < 30 or not rec.get("gameplay_visible"):
                continue
            last_screen = t
            rec = {k: v for k, v in rec.items()
                   if v not in (-1, "", [], "unknown", "unseen")
                   and k != "gameplay_visible"}
        elif kind == "advice":
            if not rec.get("spoken", rec.get("speak")):
                continue
            rec = rec["advice"]
        elif kind == "event":
            rec = rec["text"]
        if not isinstance(rec, str):
            rec = json.dumps(rec)
        lines.append(f"{clock(t)} {kind}: {rec}")
    if not lines:
        print("Nothing was logged, so there is no review.")
        return

    profile = PROFILE.read_text() if PROFILE.exists() else "none yet"
    material = "\n\n".join(filter(None, [
        context and "Match context:\n" + context,
        "Existing player profile:\n" + profile,
        "Timeline:\n" + "\n".join(lines),
    ]))
    print("Writing the post-game review...")
    try:
        out = llm.postgame_review(material)
    except anthropic.APIError as e:
        print(f"[review] failed: {e}")
        out = None
    if out:
        (folder / "review.md").write_text(out["review_markdown"])
        PROFILE.write_text(out["updated_profile_markdown"])
        print("\n" + out["review_markdown"])
        print(f"\nSaved to {folder / 'review.md'}; profile updated.")
        voice.say(out["spoken_summary"])
    print("\nAPI usage this run:\n" + llm.cost_report())


def main() -> None:
    parser = argparse.ArgumentParser(description="League of Legends coach")
    parser.add_argument("--mode", choices=["auto", *MODES], default="auto")
    parser.add_argument(
        "--brief-only", action="store_true",
        help="speak the pre-game brief only; no advice during the game")
    parser.add_argument(
        "--review", type=Path, metavar="GAME_DIR",
        help="rerun the post-game review for a logged game and exit")
    args = parser.parse_args()

    if args.review:
        review(args.review)
        return

    print("Waiting for a game to start (Ctrl+C to quit)...")
    while (data := fetch()) is None or summarize(data) is None:
        time.sleep(2)
    Session(data, args).run()


if __name__ == "__main__":
    main()
