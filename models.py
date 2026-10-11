PROVIDER_MODELS = {
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-5.2-chat-latest", "gpt-5.1-chat-latest"],
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "env_key": "ANTHROPIC_API_KEY",
    },
    "google": {
        "name": "Google",
        "models": ["gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"],
        "env_key": "GOOGLE_API_KEY",
    },
    "xai": {
        "name": "xAI",
        "models": ["grok-3", "grok-3-mini", "grok-4-1-fast-non-reasoning"],
        "env_key": "XAI_API_KEY",
    }
}

COACH_MODEL = ("anthropic", "claude-sonnet-5-5")   # brief + in-game advice
REVIEW_MODEL = ("anthropic", "claude-opus-5-5")    # post-game review
VISION_MODEL = ("anthropic", "claude-haiku-5-5")