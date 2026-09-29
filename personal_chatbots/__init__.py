"""personal-chatbots — a chatbot that answers from tables.

The runtime is deterministic: it answers from SQLite rows synced out of
``content/*.yaml`` plus GitHub metadata. No model runs while serving.

The maintenance agent is an ordinary coding agent. It edits ``content/``, runs
``pc build && pc test``, and opens a pull request. See AGENTS.md.
"""

__version__ = "0.1.0"
