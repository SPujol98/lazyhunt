"""Guided first-run setup in Telegram (onboarding-v2, ``/setup``).

- :mod:`cva.onboarding.flow`: the conversation (steps, buttons, state in
  ``user_data``, extension points for later setup steps);
- :mod:`cva.onboarding.answers`: validation of the answers and their mapping
  to ``preferences.toml``, ``profile.toml`` and ``onboarding_answers.json``;
- :mod:`cva.onboarding.portals`: the portals' terms warnings (the extension's
  own texts).

See ``docs/onboarding.md``.
"""
