# .claude/sessions: shared chat history

Condensed record of the Claude Code chats on this project, so a Claude on another laptop gets the context after `git pull`.

- `INDEX.md`: list of sessions, newest first. **Read this, then the newest files in `log/`.**
- `log/<yyyymmdd>_<id8>.md`: one session (user and Claude text, tool names; tool output and thinking dropped; secrets, emails and home paths redacted).
- `memory/`: copy of the per-user auto-memory notes.
- `export_sessions.py`: the exporter. Run `python3 -I .claude/sessions/export_sessions.py` at the end of a chat, then commit this folder.

Self-contained: nothing else in the repo depends on it. Delete the folder to remove the feature.
