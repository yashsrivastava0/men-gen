"""Typed intake errors: every failure the user can fix has a code and a plain-language message.

``IntakeError`` is raised by the parsers and the URL reader; the pipeline catches it and reports it as a
skipped source, so one bad file never stops the rest (IN-14).
"""

from __future__ import annotations

from typing import Literal

IntakeErrorCode = Literal[
    "password_protected",
    "corrupt",
    "unsupported",
    "too_large",
    "too_many_pages",
    "too_many_files",
    "session_too_large",
    "too_many_urls",
    "private_link",
    "blocked_url",
    "too_many_redirects",
    "fetch_failed",
]

_FRIENDLY: dict[str, str] = {
    "password_protected": (
        "{name} is password protected, so I could not open it. "
        "Please send an unprotected copy; I carried on with the rest."
    ),
    "corrupt": (
        "{name} looks damaged and I could not read it. Please send another copy; "
        "I carried on with the rest."
    ),
    "unsupported": "{name} is a kind of file I cannot read yet. Please paste the text or send a PDF, Word, Excel, PowerPoint, CSV or text file.",
    "too_large": "{name} is too large for me to read. Please send a smaller file or split it.",
    "too_many_pages": "{name} has too many pages for one file. Please split it or send the parts that matter.",
    "too_many_files": "That is more files than I can take in one project. Please send the most important ones first.",
    "session_too_large": "That would take the total size of your files over my limit. Please send fewer or smaller files.",
    "too_many_urls": "That is more web links than I can read in one project. Please pick the most useful ones.",
    "private_link": "{name} needs a sign-in; please paste the text or upload the file.",
    "blocked_url": "I cannot open {name}: it is not a public web page.",
    "too_many_redirects": "{name} sends me round in circles; please paste the text or upload the file.",
    "fetch_failed": "I could not open {name} right now. Please check the link, or paste the text or upload the file.",
}


class IntakeError(Exception):
    """A source could not be read. ``code`` is machine readable, ``user_message`` is shown to the user."""

    def __init__(self, code: IntakeErrorCode, name: str = "", detail: str = "") -> None:
        self.code: IntakeErrorCode = code
        self.name = name
        self.detail = detail
        super().__init__(f"{code}: {name} {detail}".strip())

    @property
    def user_message(self) -> str:
        label = self.name or "That file"
        return _FRIENDLY[self.code].format(name=label)
