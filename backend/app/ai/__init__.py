"""AI provider abstraction: agents use `AIProvider`; Gemini is the MVP implementation.

    AIProvider (base.py) ── GeminiProvider (gemini_provider.py) ── GeminiKeyPool (key_pool.py)

Get the shared instance with `app.ai.factory.get_ai_provider()`. Ollama + Qwen is a planned
future provider (see docs/architecture.md); it is not implemented.

Owner: AI/Agents.
"""
