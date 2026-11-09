"""Project terms: the words that make a question about THIS project (build prompt 02, section 2.6).

Deterministic and offline: a Unicode-aware tokenizer (Devanagari matras stay inside their word), English, Hindi and
Hinglish stop lists, a short list of words that every app idea contains (``app``, ``users``, ``business``...) and a
light plural/possessive stemmer. What is left of an idea ("a tiffin delivery service in Pune") are its proper nouns,
place names and domain nouns: ``tiffin``, ``delivery``, ``pune``.

* ``extract_terms(text)`` -> ordered, de-duplicated terms of one text.
* ``project_terms(idea_text, facts, glossary, extra)`` -> the set a question is checked against.
* ``contains_project_term(text, terms)`` -> word-aware, case-folded; ``pharmacies`` matches ``pharmacy``, ``Pune's``
  matches ``pune``, ``start`` does not match ``art``.
* ``jaccard(a, b)`` -> overlap of two term sets (0.0 when either is empty).

Hindi inflection is not stemmed (no cheap safe rule); a Devanagari term matches only its own spelling.
Every scan is a single pass over at most ``MAX_CHARS`` characters; no regular expressions.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Iterable

from mde_compass.domain.interview import Fact
from mde_compass.engine.quotes import normalize

MAX_CHARS = 20_000
MIN_TOKEN_CHARS = 2
_APOSTROPHE = "'"


def _words(block: str) -> list[str]:
    return block.split()


_EN_STOP = _words(
    """
a about above after again against all almost also although always am among an and another any anybody anyone
anything are aren't around as at away back be became because become been before being below between both but by
came can cannot can't come could couldn't did didn't do does doesn't doing don't done down during each either else
enough etc even ever every everyone everything few for from further get gets getting give go goes going gone got
had hadn't has hasn't have haven't having he her here hers herself him himself his how however i i'd if i'll i'm in
into is isn't it it's its itself i've just keep know let lets like likes liked little look looking made make makes
many may maybe me might more most much must my myself near need needed needs neither never no nobody none nor not
nothing now of off often oh ok okay on once one ones only onto or other others our ours ourselves out over own per
perhaps please quite rather really same say see seem shall she should shouldn't since so some somebody someone
something still such sure take than thank thanks that the their theirs them themselves then there these they they'd
they'll they're they've thing things think this those though through thus to together too toward under until up upon
us use used uses using very via want wanted wants was wasn't way ways we we'd well we'll we're were weren't we've
what whatever when where whether which while who whom whose why will with within without won't would wouldn't yes
yet you you'd you'll your you're yours yourself you've hello hi hey
"""
)

# Words every app idea contains: they never make a question specific to one project.
_GENERIC = _words(
    """
app apps application applications mobile phone phones smartphone android ios iphone website web platform system
systems software tool tools product products project projects idea ideas business businesses company companies
startup service services solution solutions user users customer customers client clients people person persons
feature features screen screens page pages account accounts login logins signup design simple easy good best great
better nice first second third version release launch online digital manage management manager managers
interface experience option options function functions functionality new build building builds built create
creating develop developing help helps helping
"""
)

_HI_STOP = _words(
    """
का की के को में से पर और या है हैं था थी थे हो होगा होगी होंगे हूँ हूं मैं मुझे मेरा मेरी मेरे हम हमारा हमारी
हमारे आप आपका आपकी आपके तुम यह ये वह वे इस उस इन उन जो कि कुछ सब सभी भी तो ही नहीं न कर करना करने करें करो
करता करती चाहिए चाहिये चाहता चाहती चाहते लिए लिये साथ बारे एक दो बहुत अब जब तब कौन क्या कैसे कहाँ कहां कब
क्यों कितना कितने कितनी बनाना बनाने बनाएं बनाना वाला वाली वाले द्वारा तक अपने अपना अपनी लोग लोगों रहा रही
रहे गया गई गए जा सकता सकती सकते अगर लेकिन मगर फिर अभी बस सिर्फ ऐसे जैसे वैसे यहाँ वहाँ हाँ जी
ऐप एप ऐप्लिकेशन एप्लिकेशन सर्विस सेवा ग्राहक उपयोगकर्ता यूज़र बिज़नेस व्यापार कंपनी
"""
)

_HINGLISH_STOP = _words(
    """
hai hain ka ki ke ko mein me se par pe aur ya tha thi the ho hoga hogi honge hu hoon main mai mujhe mujhko mera
meri mere hum hamara hamari hamare humko aap aapka aapki aapke tum yeh ye woh wo is us in un jo kuch sab sabhi bhi
to toh hi nahi nahin na kar karna karne karo karein karta karti kijiye chahiye chahta chahti chahte liye lie
saath baare ek do bahut ab jab tab kaun kya kaise kahan kab kyun kyon kitna kitne kitni banana banani banane
banao wala wali wale dwara tak apna apni apne log logon unko isko usko lekin magar agar phir abhi bas sirf aise
jaise waise yahan wahan theek thik accha achha haan ji rha rahi rahe gaya gayi gaye ja sakta sakti sakte
"""
)

STOPWORDS: frozenset[str] = frozenset(
    w.replace("'", "") for w in (*_EN_STOP, *_HI_STOP, *_HINGLISH_STOP)
)
GENERIC_WORDS: frozenset[str] = frozenset(_GENERIC)
_DROP: frozenset[str] = STOPWORDS | GENERIC_WORDS

_IRREGULAR = {
    "children": "child",
    "people": "person",
    "men": "man",
    "women": "woman",
    "feet": "foot",
    "teeth": "tooth",
    "mice": "mouse",
}
_ES_ENDINGS = ("ches", "shes", "sses", "xes", "zes")
_KEEP_S = ("ss", "us", "is")


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def _clean(token: str) -> str:
    token = token.removesuffix("'s") if token.endswith("'s") else token
    return token.replace(_APOSTROPHE, "")


def tokenize(text: str) -> list[str]:
    """Case-folded words of ``text`` in order (not de-duplicated, stop words kept).

    Hyphens and punctuation split words; an apostrophe between two letters stays inside the word and a trailing
    possessive is cut (``children's`` -> ``children``). Devanagari vowel signs stay inside their word.
    """
    s = normalize(text[:MAX_CHARS])
    tokens: list[str] = []
    buf: list[str] = []
    last = len(s) - 1
    for i, ch in enumerate(s):
        inner_apostrophe = ch == _APOSTROPHE and bool(buf) and i < last and s[i + 1].isalnum()
        if _is_word_char(ch) or inner_apostrophe:
            buf.append(ch)
        elif buf:
            tokens.append(_clean("".join(buf)))
            buf = []
    if buf:
        tokens.append(_clean("".join(buf)))
    return [t for t in tokens if t]


def stem(token: str) -> str:
    """A light plural stem for matching only (``pharmacies`` -> ``pharmacy``, ``appointments`` -> ``appointment``)."""
    irregular = _IRREGULAR.get(token)
    if irregular is not None:
        return irregular
    if not token.isascii() or len(token) <= 3:
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(_ES_ENDINGS):
        return token[:-2]
    if token.endswith("s") and not token.endswith(_KEEP_S):
        return token[:-1]
    return token


def _keep(token: str) -> bool:
    return len(token) >= MIN_TOKEN_CHARS and not token.isdigit() and token not in _DROP


def extract_terms(text: str, *, language: str = "en") -> list[str]:
    """The project-specific words of ``text``: stop words and words every app idea contains dropped, duplicates
    removed, first-seen order kept.

    ``language`` is accepted for the API; the English, Hindi and Hinglish stop lists always all apply, because
    Hinglish mixes them freely.
    """
    del language
    return list(dict.fromkeys(t for t in tokenize(text) if _keep(t)))


def project_terms(
    idea_text: str,
    facts: Iterable[Fact] = (),
    glossary: Iterable[str] = (),
    extra: Iterable[str] = (),
) -> frozenset[str]:
    """Terms a question is checked against: the idea text, the ACTIVE facts (statements and entities), the user's
    glossary and any extra words (for example a document's title). Superseded facts no longer count, so a pivot
    drops the old idea's words."""
    terms: dict[str, None] = dict.fromkeys(extract_terms(idea_text))
    for fact in facts:
        if fact.status != "active":
            continue
        terms.update(dict.fromkeys(extract_terms(fact.statement)))
        for entity in fact.entities:
            terms.update(dict.fromkeys(extract_terms(entity)))
    for source in (*glossary, *extra):
        terms.update(dict.fromkeys(extract_terms(source)))
    return frozenset(terms)


def contains_project_term(text: str, terms: Collection[str]) -> bool:
    """True if a whole word of ``text`` (case-folded, plural and possessive forms folded) is one of ``terms``.

    A term that is itself several words (``ride sharing``) must appear as those words in a row.
    """
    singles: set[str] = set()
    phrases: list[list[str]] = []
    for term in terms:
        parts = [stem(t) for t in tokenize(term)]
        if len(parts) == 1:
            singles.add(parts[0])
        elif parts:
            phrases.append(parts)
    if not singles and not phrases:
        return False
    words = [stem(t) for t in tokenize(text)]
    if any(w in singles for w in words):
        return True
    for phrase in phrases:
        size = len(phrase)
        if any(words[i : i + size] == phrase for i in range(len(words) - size + 1)):
            return True
    return False


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """|a and b| / |a or b|; 0.0 when either side is empty (no terms means no shared meaning)."""
    sa, sb = frozenset(a), frozenset(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
