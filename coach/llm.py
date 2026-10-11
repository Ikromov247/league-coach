"""Model calls: screen reading, in-game coaching, post-game review."""

import json
import threading

import anthropic

from models import COACH_MODEL, REVIEW_MODEL, VISION_MODEL

from .config import PRICES

client = anthropic.Anthropic()
COACH = COACH_MODEL[1]
REVIEW = REVIEW_MODEL[1]
VISION = VISION_MODEL[1]

# A declined coach or review request is re-run server-side on Anthropic's default
# fallback model instead of coming back empty.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default"}

_usage_lock = threading.Lock()
usage: dict[str, list[int]] = {}


def _track(model: str, resp) -> None:
    u = resp.usage
    cached = (u.cache_read_input_tokens or 0) + (
        u.cache_creation_input_tokens or 0)
    with _usage_lock:
        total = usage.setdefault(model, [0, 0])
        total[0] += u.input_tokens + cached
        total[1] += u.output_tokens


def cost_report() -> str:
    lines, total = [], 0.0
    for model, (tokens_in, tokens_out) in usage.items():
        price_in, price_out = PRICES.get(model, (0, 0))
        cost = (tokens_in * price_in + tokens_out * price_out) / 1e6
        total += cost
        lines.append(f"  {model}: {tokens_in:,} in, {tokens_out:,} out, "
                     f"about ${cost:.2f}")
    return "\n".join(lines + [f"  total: about ${total:.2f}"])


def _schema(**props) -> dict:
    return {"type": "json_schema", "schema": {
        "type": "object", "properties": props,
        "required": list(props), "additionalProperties": False}}


def _json(resp) -> dict | None:
    if resp.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    return json.loads(text) if text else None


STR, BOOL, INT = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}

# --------------------------------------------------------------- screen

SCREEN_FORMAT = _schema(
    gameplay_visible=BOOL,
    my_location=STR,
    wave={"type": "string", "enum": [
        "pushing_to_enemy", "even", "pushing_to_me", "under_my_tower",
        "under_enemy_tower", "no_wave", "unknown"]},
    ally_minions=INT,
    enemy_minions=INT,
    enemy_laner_visible=BOOL,
    enemy_laner_hp_pct=INT,
    enemy_jungler_location=STR,
    enemies_on_minimap={"type": "array", "items": STR},
    allies_on_minimap={"type": "array", "items": STR},
    river_ward_near_me={"type": "string", "enum": ["yes", "no", "unknown"]},
    objectives=STR,
    notes=STR,
)

SCREEN_PROMPT = """\
These are two captures from one moment of a League of Legends match: the \
full screen, then an enlarged crop of the bottom-right corner where the \
minimap normally sits. Your description feeds a coach that cannot see the \
screen, so report only what is actually visible and use "unknown" or \
"unseen" rather than guessing.

The minimap is a square. The blue team's base is its bottom-left corner \
and the red team's base is its top-right corner. Top lane runs up the left \
edge and along the top edge, bot lane runs along the bottom edge and up the \
right edge, mid lane is the diagonal joining the bases, and the river is \
the other diagonal. The player's own position is the centre of the white \
rectangular outline on the minimap, which marks the camera view. Name \
locations from the minimap, never from how the terrain looks in the full \
screen, since jungle, river and lanes look alike up close.

The player is {me} ({role}) on the {side} team. Allies: {allies}. Enemies: {enemies}. The \
enemy jungler is {jungler}. Lane opponent(s): {opponents}.

- gameplay_visible: false if the screen shows the shop, scoreboard, a death \
screen, loading, or anything other than the live game. If false, fill the \
rest with unknown/empty/-1 values.
- my_location: where the player's champion is, e.g. "mid lane, near enemy \
tower" or "bot river".
- wave: where the minion wave in the player's lane is heading.
- ally_minions / enemy_minions: counts in the player's lane, -1 if unclear.
- enemy_laner_hp_pct: lane opponent's health bar as a percentage, -1 if not \
visible.
- enemy_jungler_location: where the enemy jungler appears on the minimap or \
screen, or "unseen".
- enemies_on_minimap / allies_on_minimap: "Champion: location" for each \
icon you can identify on the minimap.
- objectives: dragon/baron/other objective icons or timers showing on the \
minimap, or "none visible".
- notes: anything else a coach would want in one short sentence, or "".\
"""


def read_screen(frame_b64: str, minimap_b64: str, state: dict) -> dict | None:
    def names(players):
        return ", ".join(p["champion"] for p in players) or "unknown"

    def image(data):
        return {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": data}}

    jungler = [e for e in state["enemies"] if e["role"] == "JUNGLE"]
    prompt = SCREEN_PROMPT.format(
        me=state["me"]["champion"], role=state["me"]["role"],
        side=state["me"]["side"],
        allies=names(state["allies"]), enemies=names(state["enemies"]),
        jungler=names(jungler), opponents=names(state["lane_opponents"]))
    resp = client.messages.create(
        model=VISION, max_tokens=1500,
        thinking={"type": "disabled"},
        output_config={"effort": "low", "format": SCREEN_FORMAT},
        messages=[{"role": "user", "content": [
            image(frame_b64), image(minimap_b64),
            {"type": "text", "text": prompt}]}],
    )
    _track(VISION, resp)
    return _json(resp)


# --------------------------------------------------------------- coach

BRIEF_SYSTEM = """\
You are a League of Legends coach preparing one player just as a match \
begins. You get both team compositions, the player's champion and role, \
notes on the game mode, and, when one exists, a profile of this player's \
recurring habits from earlier games.

Produce two things.

`spoken` is read aloud by text-to-speech while the player walks to lane, so \
it has to land on one hearing: at most about 60 words, plain sentences, no \
lists. Give the two or three things that matter most for this specific \
matchup, such as which levels or items make the opponent dangerous, whether \
to look for early trades or play for scaling, and what the enemy jungler is \
likely to do. If the profile names a habit that applies here, include it.

`lane_plan` is a written reference that the in-game coach will reread for \
the whole match, so it can be longer (up to about 200 words) and specific: \
trading pattern, power spikes on both sides by level and item, wave \
management that suits the matchup, gank exposure, and this team's win \
condition after laning.

Your knowledge of champions and patches may be out of date. Where the mode \
notes conflict with what you remember, follow the notes.\
"""

BRIEF_FORMAT = _schema(spoken=STR, lane_plan=STR)

COACH_SYSTEM = """\
You are a League of Legends coach sitting behind one player during a live \
match. Whatever you put in `advice` is read aloud by text-to-speech while \
they play. They hear it once, mid-action, a few seconds after the data was \
captured, and cannot reread it.

Each request gives you exact data from the game client (time, levels, \
items, CS, K/D/A, gold, deaths), sometimes a partial description of the \
screen from a separate vision model, the reasons this check was triggered, \
what you said recently, and how long ago you last spoke.

Most checks should end in silence. Set `speak` to true only when the player \
should do something different in the next 30 to 60 seconds from what a \
competent player in their position would already be doing, and you have not \
already told them. A coach who talks every fifteen seconds gets tuned out, \
and one or two lines a minute is already a lot. Once you have given a piece \
of advice (recall and spend gold, ward river, respect a power spike), treat \
it as delivered. Saying it again in different words is noise unless \
something material has changed since, and the player may have a good reason \
for not acting on it yet.

Set `urgent` to true only for something that cannot wait half a minute: \
real danger to the player right now, or a short window about to close. A \
non-urgent line is dropped automatically if you spoke within the last 40 \
seconds, so do not spend lines on small things.

When you speak, use one sentence of 12 words or fewer with the instruction \
first. Leave out numbers the player can already see, such as their own gold, \
health and level. Both teams can have the same champion, so say "their \
Pyke" or "your Jinx" for anyone other than the player.

The request names the phase, and the two phases are coached differently.

During laning, the player's world is their own lane: themselves, their lane \
partner if they have one, their lane opponents, and the two junglers. Coach \
that lane: whether to trade aggressively or play safe given levels, items, \
health and the lane plan; wave management (when shoving is dangerous, when \
to freeze, slow push, or crash and recall); gank risk; where to ward; and \
when the gold on hand justifies a recall. What happens elsewhere on the map \
matters only through what it changes for this lane. A teammate dying top or \
mid is not a reason for the player to back off, group or leave lane, and \
most such deaths deserve silence. It earns a line only when it concretely \
changes the threat here, for example the enemy mid laner is now free to \
roam toward this lane, or the enemy jungler just died and the player can \
play forward until they respawn.

After laning, shift to macro: upcoming objectives and setting up for them, \
which part of the map the player should be on, numbers advantages after \
kills, grouping versus side-laning, and resetting before a fight. Here \
kills anywhere on the map do matter.

Client data is exact. The screen description is not: the vision model reads \
small icons, misses most things, and cannot reliably tell where the player \
is standing. Never tell the player where they are or that they are out of \
position because of it; assume they are where their role and the phase put \
them. Use the description only as a hint, and prefer advice that still \
holds if it is wrong ("ward river before you push" over "their jungler is \
top, all in"). You cannot see enemy cooldowns, so do not state them.

Your knowledge of champions and patches may be out of date. The match \
context below includes notes on this game mode; follow them where they \
conflict with what you remember.\
"""

ADVICE_FORMAT = _schema(
    speak=BOOL,
    urgent=BOOL,
    advice=STR,
    category={"type": "string", "enum": [
        "trading", "wave", "gank_risk", "vision", "recall", "objective",
        "positioning", "teamfight", "other", "none"]},
)

REVIEW_SYSTEM = """\
You are a League of Legends coach reviewing one player's match right after \
it ended. Your aim is for this player to get better over many games, so \
look for the few habits that cost the most and say concretely what to do \
differently, with timestamps from this game as evidence.

You get the match context, a timeline of game-client snapshots and events, \
periodic screen descriptions from a vision model (useful but sometimes \
wrong), the advice the live coach spoke during the game, and the player's \
existing profile if there is one. The client data shows outcomes such as \
CS, deaths, gold and objectives; it does not show mechanics, so keep your \
claims to what the data supports and say so where you are inferring.

Return three things.

`review_markdown`: a short overview of how the game went and why, then a \
prioritized list of three to five tips. Each tip names the habit, points to \
the moments in this game that show it, and gives a concrete thing to \
practice next game. Include one thing the player did well.

`spoken_summary`: at most about 50 words, read aloud right after the game. \
The result in a phrase, then the single most important thing to work on.

`updated_profile_markdown`: the full replacement text for the player's \
long-term profile. Merge this game into the existing profile: keep \
recurring weaknesses with a note of how many games they have appeared in, \
add new ones, mark ones that improved, and drop anything that has not \
shown up for several games. Keep it under about 300 words, since it is fed \
to the coach at the start of every match.\
"""

REVIEW_FORMAT = _schema(review_markdown=STR, spoken_summary=STR,
                        updated_profile_markdown=STR)


def pregame_brief(context: str) -> dict | None:
    resp = client.beta.messages.create(
        model=COACH, max_tokens=8000, system=BRIEF_SYSTEM,
        output_config={"effort": "medium", "format": BRIEF_FORMAT},
        messages=[{"role": "user", "content": context}],
        **FALLBACK,
    )
    _track(COACH, resp)
    return _json(resp)


def live_advice(context: str, situation: str) -> dict | None:
    resp = client.beta.messages.create(
        model=COACH, max_tokens=4000,
        system=[
            {"type": "text", "text": COACH_SYSTEM},
            {"type": "text", "text": context,
             "cache_control": {"type": "ephemeral"}},
        ],
        output_config={"effort": "low", "format": ADVICE_FORMAT},
        messages=[{"role": "user", "content": situation}],
        **FALLBACK,
    )
    _track(COACH, resp)
    return _json(resp)


def postgame_review(material: str) -> dict | None:
    with client.beta.messages.stream(
        model=REVIEW, max_tokens=32000, system=REVIEW_SYSTEM,
        output_config={"effort": "high", "format": REVIEW_FORMAT},
        messages=[{"role": "user", "content": material}],
        **FALLBACK,
    ) as stream:
        resp = stream.get_final_message()
    _track(REVIEW, resp)
    return _json(resp)
