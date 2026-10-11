The coach is an AI model, accessed by API. API keys for several providers are saved in the system and listed in models.py. The pre-game brief and in-game advice use `COACH_MODEL` (Claude Sonnet 5.5), the post-game review uses `REVIEW_MODEL` (Claude Opus 5.5), and screen reading uses `VISION_MODEL` (Claude Haiku 5.5), all set in models.py.

## Running

- `uv run python -m coach` waits for a game, then coaches it. Mode (classic or swiftplay) is detected automatically; force it with `--mode swiftplay`.
- `uv run python -m coach --brief-only` speaks the pre-game brief only, with no advice during the game. The game is still logged and reviewed.
- `uv run python -m coach --review data/games/<folder>` reruns the post-game review for a logged game.

## Layout

- `coach/liveclient.py` reads Riot's local Live Client Data API (port 2999).
- `coach/capture.py` takes the screenshot and minimap crop.
- `coach/triggers.py` decides, without a model, when to ask the coach.
- `coach/llm.py` holds all prompts and model calls.
- `coach/config.py` holds timings, thresholds and per-mode objective timers. Objective timers change between patches.
- `data/games/<timestamp>/` holds each game's log and review, plus `frames/` with a screenshot and minimap crop every 30 seconds for checking what the vision model was given; `data/profile.md` is the long-term player profile fed into every game.
