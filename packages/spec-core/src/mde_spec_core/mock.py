"""Mock modes by code, pools, the naughty-strings allow-list and the fixture safety rules (doc 18 5 and 6; FN-09 to FN-14).

`empty`, `busy`, `extreme`, `broken` and `offline` are made here by code from the hero records, a seed and data
tables (A5, B3); `typical` is the hero content the model wrote (code fills any gap from the pools). Demo accounts,
seeded recipes and behaviours are made here too. No model, no network. All tables are data under `data/pools/`
and `data/blns/`.

Safety (FN-11, FN-12): ID numbers and phone numbers appear only masked; `assert_fixture_safe` fails any value that
passes a python-stdnum `validate()`, any digit run of `digit_run_deny_length` or more, an unmasked phone number, a
plate-like value, an unmasked `name@bank` id, an email outside example.com and a real brand from the deny-list.
"""

from __future__ import annotations

import hashlib
import importlib
import random
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Any

import phonenumbers
import regex
import yaml
from babel import Locale
from babel.numbers import format_currency, format_decimal

from . import vocab
from .canon import sha256_text
from .config import DATA_DIR, SpecConfig, default_config
from .graph import Entity, FieldDef, HeroRecord, SpecGraph

POOLS_DIR = DATA_DIR / "pools"
BLNS_DIR = DATA_DIR / "blns"


@cache
def _pool_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((POOLS_DIR / name).read_text(encoding="utf-8"))


def mock_tables() -> dict[str, Any]:
    return _pool_yaml("mock_tables.yaml")


# ======================================================================== naughty strings (FN-10)


class BlnsError(ValueError):
    """The vendored file is not the pinned one, or its headings changed."""


@dataclass(frozen=True)
class BlnsSection:
    title: str
    entries: tuple[str, ...]


@cache
def blns_meta() -> dict[str, Any]:
    return yaml.safe_load((BLNS_DIR / "blns.meta.yaml").read_text(encoding="utf-8"))


def parse_blns(text: str) -> list[BlnsSection]:
    """Sections of the file by its own `#<tab>Title` headings: a `#` line that follows a blank line (or the file
    start) and is followed by a line that is exactly `#`. The text is split on LF only (some strings hold U+2028)."""
    lines = text.split("\n")
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if (
            line.startswith("#")
            and line[1:].strip()
            and (i == 0 or lines[i - 1] == "")
            and i + 1 < len(lines)
            and lines[i + 1] == "#"
        ):
            heads.append((i, line[1:].strip()))
    sections: list[BlnsSection] = []
    for k, (i, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        entries = tuple(ln for ln in lines[i + 1 : end] if ln != "" and not ln.startswith("#"))
        sections.append(BlnsSection(title, entries))
    return sections


@cache
def blns_sections() -> tuple[BlnsSection, ...]:
    meta = blns_meta()
    raw = (BLNS_DIR / meta["file"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != meta["sha256"]:
        raise BlnsError(
            f"blns.txt is not the pinned file (sha256 {digest[:12]}..., pinned {meta['sha256'][:12]}...)"
        )
    sections = parse_blns(raw.decode("utf-8"))
    titles = [s.title for s in sections]
    if titles != list(meta["headings"]):
        raise BlnsError(f"the headings of blns.txt changed: {titles}")
    return tuple(sections)


_URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|ca|uk|io|in|co)\b", re.I)


def looks_like_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


def _has_control_chars(s: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s)


@cache
def load_allowed_strings() -> dict[str, tuple[str, ...]]:
    """ONLY the allow-listed categories, by the file's own headings, after the entry filters of the meta file.
    Raises `BlnsError` if the pinned file or its headings are not what the meta file records."""
    meta = blns_meta()
    flt = meta["entry_filters"]
    deny_run = default_config().checks.digit_run_deny_length
    wanted = list(meta["allowed_categories"])
    out: dict[str, tuple[str, ...]] = {}
    by_title = {s.title: s for s in blns_sections()}
    for cat in wanted:
        if cat not in by_title:
            raise BlnsError(f"allowed category {cat!r} is not in the file")
        keep: list[str] = []
        for e in by_title[cat].entries:
            if len(e) > int(flt["max_length"]) or _has_control_chars(e):
                continue
            if flt["drop_urls"] and looks_like_url(e):
                continue
            if re.search(rf"\d{{{deny_run},}}", _ascii_digits(e)):
                continue
            if find_unsafe([e], check_urls=True):
                continue
            keep.append(e)
        out[cat] = tuple(keep)
    return out


# ======================================================================== locales and pools (FN-09, FN-14)


class LocaleFallbackError(ValueError):
    """People or places fell back to another script than the app's (a provider silently used English)."""


_SCRIPT_ALIAS = {"cjk": "Han"}


def locale_table() -> dict[str, Any]:
    return _pool_yaml("locales.yaml")


def _lang_row(language: str) -> dict[str, Any]:
    tbl = locale_table()
    base = language.replace("_", "-").split("-")[0].lower()
    return tbl["languages"].get(base) or tbl["languages"][tbl["default"]]


def script_for_language(language: str) -> str:
    return str(_lang_row(language)["script"])


def _letters(text: str) -> list[str]:
    return regex.findall(r"[\p{L}\p{M}]", text)


def script_share(texts: Iterable[str], script: str) -> float:
    """The share of texts whose letters are mostly (50% or more) in `script`. Texts with no letters are skipped.
    A fixture whose people or places fell back to English scores 0 for Kannada (FN-09)."""
    name = _SCRIPT_ALIAS.get(script.lower(), script)
    pat = regex.compile(rf"\p{{Script={name}}}")
    total = hit = 0
    for t in texts:
        letters = _letters(t)
        if not letters:
            continue
        total += 1
        in_script = sum(1 for ch in letters if pat.match(ch))
        if in_script / len(letters) >= 0.5:
            hit += 1
    return hit / total if total else 0.0


def assert_script_share(
    texts: Iterable[str],
    script: str,
    *,
    minimum: float | None = None,
    config: SpecConfig | None = None,
) -> float:
    cfg = config or default_config()
    need = cfg.checks.locale_script_share_min if minimum is None else minimum
    items = list(texts)
    share = script_share(items, script)
    if share < need:
        raise LocaleFallbackError(
            f"only {share:.0%} of {len(items)} texts are in {script} script; {need:.0%} is needed "
            "(a name provider probably fell back to English: use the curated pool)"
        )
    return share


@dataclass(frozen=True)
class Pool:
    language: str
    key: str
    script: str
    people: tuple[str, ...]
    places: tuple[str, ...]
    source: str  # faker | file
    needs_native_review: bool
    rtl: bool
    number_locale: str
    currency: str

    def pick_people(self, n: int, rnd: random.Random) -> list[str]:
        return _draw(self.people, n, rnd)

    def pick_places(self, n: int, rnd: random.Random) -> list[str]:
        return _draw(self.places, n, rnd)


def _draw(items: Sequence[str], n: int, rnd: random.Random) -> list[str]:
    """`n` items, no repeat until the pool is used up (then it is reshuffled): deterministic for a seeded `rnd`."""
    out: list[str] = []
    while len(out) < n:
        batch = list(items)
        rnd.shuffle(batch)
        out.extend(batch[: n - len(out)])
    return out


_FAKER_SEED = 4242
_FAKER_PEOPLE, _FAKER_PLACES = 120, 60


def _faker_lists(locale: str) -> tuple[list[str], list[str]]:
    """People and places from a seeded Faker locale. Only person names and cities: never its ssn, bank or phone
    providers. Generated once and cached, so the same locale always gives the same lists."""
    from faker import Faker

    fake = Faker(locale)
    fake.seed_instance(_FAKER_SEED)
    people: list[str] = []
    seen: set[str] = set()
    tries = 0
    while len(people) < _FAKER_PEOPLE and tries < 2000:
        tries += 1
        name = f"{fake.first_name()} {fake.last_name()}"
        if name not in seen:
            seen.add(name)
            people.append(name)
    places: list[str] = []
    seen_p: set[str] = set()
    tries = 0
    while len(places) < _FAKER_PLACES and tries < 2000:
        tries += 1
        city = fake.city()
        if city not in seen_p:
            seen_p.add(city)
            places.append(city)
    return people, places


@cache
def pool_for_language(language: str) -> Pool:
    """The name and place pool of an app language. Raises `LocaleFallbackError` when the pool is not in the
    language's script (the check that guards Faker's silent fallback to English, FN-09)."""
    row = _lang_row(language)
    base = language.replace("_", "-").split("-")[0].lower()
    if row["source"] == "faker":
        people, places = _faker_lists(row["locale"])
        key, review, rtl, source = row["locale"], False, False, "faker"
    else:
        data = _pool_yaml(row["file"])
        people, places = list(data["people"]), list(data["places"])
        key, review, rtl, source = (
            data["locale"],
            bool(data.get("needs_native_review")),
            bool(data.get("rtl")),
            "file",
        )
    assert_script_share(people + places, row["script"])
    return Pool(
        language=base,
        key=key,
        script=row["script"],
        people=tuple(people),
        places=tuple(places),
        source=source,
        needs_native_review=review,
        rtl=rtl,
        number_locale=row["number_locale"],
        currency=row["currency"],
    )


def _locale(name: str) -> Locale:
    try:
        return Locale.parse(name)
    except Exception:
        return Locale.parse("en_IN")


def format_money(amount: int | float | Decimal, pool: Pool) -> str:
    """Currency in the locale's own format (Indian digit grouping for en_IN, hi_IN, mr_IN; FN-14)."""
    return format_currency(Decimal(str(amount)), pool.currency, locale=_locale(pool.number_locale))


def format_number(value: int | float | Decimal, pool: Pool, *, decimals: int = 0) -> str:
    """A number in the locale's own grouping (the CLDR pattern, so en_IN gives 12,34,567)."""
    loc = _locale(pool.number_locale)
    pattern = str(loc.decimal_formats[None].pattern).split(";")[0]
    pattern = pattern.split(".")[0] + ("." + "0" * decimals if decimals else "")
    return format_decimal(Decimal(str(value)), format=pattern, locale=loc)


# ======================================================================== masking and deny rules (FN-11, FN-12)


@cache
def safety_rules() -> dict[str, Any]:
    return _pool_yaml("fixture_safety.yaml")


def _mask_char() -> str:
    return str(safety_rules()["mask_char"])


def _ascii_digits(text: str) -> str:
    """Digits of any script become ASCII digits (a Devanagari digit run is a digit run)."""
    out = []
    for ch in text:
        if ch.isdigit() and not ch.isascii():
            d = unicodedata.digit(ch, None)  # type: ignore[arg-type]
            out.append(str(d) if d is not None else ch)
        else:
            out.append(ch)
    return "".join(out)


def mask_phone(value: str, default_region: str = "IN") -> str:
    """`+91 98••• ••210`: the first two and the last three digits of the national number, the rest masked."""
    cfg = mock_tables()["phone_mask"]
    first, last = int(cfg["first_digits"]), int(cfg["last_digits"])
    bullet = _mask_char()
    cc: str | None = None
    digits = re.sub(r"\D", "", _ascii_digits(value))
    try:
        num = phonenumbers.parse(value, None if value.strip().startswith("+") else default_region)
        cc, digits = f"+{num.country_code}", str(num.national_number)
    except phonenumbers.NumberParseException:
        pass
    if len(digits) <= first + last:
        return (cc + " " if cc else "") + bullet * max(len(digits), 1)
    hidden = len(digits) - first - last
    g1 = min(3, hidden)
    g2 = hidden - g1
    body = (
        f"{digits[:first]}{bullet * g1} {bullet * g2}{digits[-last:]}"
        if g2
        else f"{digits[:first]}{bullet * g1}{digits[-last:]}"
    )
    return f"{cc} {body}" if cc else body


def mask_id(value: str, keep: int = 4) -> str:
    """`•••• 1234`: an ID number is shown only as its last `keep` characters."""
    compact = re.sub(r"[\s\-.]", "", value)
    return f"{_mask_char() * 4} {compact[-keep:]}" if compact else _mask_char() * 4


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str
    sample: str  # always masked


class FixtureSafetyError(ValueError):
    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = list(violations)
        shown = "; ".join(f"{v.kind}: {v.detail} ({v.sample})" for v in self.violations[:5])
        more = f" (+{len(self.violations) - 5} more)" if len(self.violations) > 5 else ""
        super().__init__(f"fixture is not safe: {shown}{more}")


def iter_strings(obj: Any) -> Iterator[str]:
    """Every string leaf of a nested structure (dict keys are not data and are skipped)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            yield from iter_strings(v)
    elif hasattr(obj, "model_dump"):
        yield from iter_strings(obj.model_dump())


@cache
def _compiled_rules() -> dict[str, Any]:
    r = safety_rules()
    return {
        "plates": [re.compile(p, re.I) for p in r["plate_patterns"]],
        "stdnum": [
            (
                importlib.import_module(c["module"]),
                re.compile(c["pattern"]),
                c["label"],
                c.get("function", "validate"),
            )
            for c in r["stdnum_checks"]
        ],
        "email": re.compile(r"[\w.+\-]+@([\w\-]+(?:\.[\w\-]+)+)", re.I),
        "handle": re.compile(rf"[\w.\-{re.escape(_mask_char())}]{{2,}}@[A-Za-z][A-Za-z0-9]*"),
    }


_GROUPED_AMOUNT = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?")


def _has_phone(text: str, regions: Sequence[str]) -> bool:
    """A number that libphonenumber accepts as valid for any listed region (or written with +country code)."""
    for region in [None, *regions]:
        for _ in phonenumbers.PhoneNumberMatcher(
            text, region, leniency=phonenumbers.Leniency.VALID
        ):
            return True
    return False


def _validates(mod: Any, fn: str, value: str) -> bool:
    """True when python-stdnum accepts the value (it raises its own errors for a bad one)."""
    try:
        return getattr(mod, fn)(value) is not False
    except Exception:
        return False


def _mask_sample(text: str) -> str:
    t = text.strip()
    return t if len(t) <= 4 else f"{t[:2]}{_mask_char() * 3}{t[-1:]}"


def _brand_regex(allowed: Iterable[str]) -> re.Pattern[str] | None:
    """A deny-listed brand is exempt when it is part of a business name the user gave."""
    given = [a.lower() for a in allowed]
    names = [b for b in safety_rules()["brand_denylist"] if not any(b.lower() in g for g in given)]
    if not names:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w])", re.I)


def find_unsafe(
    texts: Iterable[str],
    *,
    allowed_names: Iterable[str] = (),
    check_urls: bool = True,
    config: SpecConfig | None = None,
) -> list[Violation]:
    """Every reason a set of fixture texts is not safe (empty list = safe). `allowed_names` are business names
    the user gave: they may contain a deny-listed word. Samples in the result are masked."""
    cfg = config or default_config()
    rules = _compiled_rules()
    rs = safety_rules()
    brand = _brand_regex(allowed_names)
    allowed_domains = {d.lower() for d in rs["allowed_email_domains"]}
    deny_run = cfg.checks.digit_run_deny_length
    out: list[Violation] = []
    for text in texts:
        if not text:
            continue
        norm = _ascii_digits(text)
        # 1. emails outside the reserved domain; name@bank (UPI-like) ids that are not masked
        for m in rules["email"].finditer(text):
            if m.group(1).lower() not in allowed_domains:
                out.append(
                    Violation(
                        "email_outside_example",
                        "an email must be at an example domain",
                        _mask_sample(m.group()),
                    )
                )
        for m in rules["handle"].finditer(text):
            nxt = text[m.end() : m.end() + 2]
            is_email = nxt[:1] == "." and nxt[1:2].isalnum()
            if not is_email and _mask_char() not in m.group():
                out.append(
                    Violation(
                        "upi_like_id",
                        "an id of the form name@bank is not masked",
                        _mask_sample(m.group()),
                    )
                )
        # 2. a digit run of the deny length or more
        run = re.search(rf"\d{{{deny_run},}}", norm)
        if run:
            out.append(
                Violation(
                    "digit_run",
                    f"a run of {len(run.group())} digits (limit {deny_run - 1})",
                    _mask_sample(run.group()),
                )
            )
        # 3. a phone number that is not masked (grouped amounts like 1,23,456.00 are not phone numbers)
        phone_text = _GROUPED_AMOUNT.sub("N", norm)
        if sum(ch.isdigit() for ch in phone_text) >= 9 and _has_phone(
            phone_text, rs["phone_regions"]
        ):
            out.append(
                Violation(
                    "phone_unmasked",
                    "a phone number appears unmasked",
                    _mask_sample(re.sub(r"\D", "", phone_text)),
                )
            )
        # 4. plate-like values
        for pat in rules["plates"]:
            m = pat.search(text)
            if m:
                out.append(
                    Violation("plate_like", "a vehicle plate-like value", _mask_sample(m.group()))
                )
                break
        # 5. any value that passes a python-stdnum validate()
        for mod, pat, label, fn in rules["stdnum"]:
            for m in pat.finditer(norm):
                compact = re.sub(r"[ .\-]", "", m.group())
                if _validates(mod, fn, compact):
                    out.append(
                        Violation("id_validates", f"passes the {label} check", mask_id(compact))
                    )
        # 6. a real brand
        if brand is not None:
            m = brand.search(text)
            if m:
                out.append(
                    Violation(
                        "real_brand", "a real brand name that the user did not give", m.group()
                    )
                )
        # 7. URLs
        if check_urls and re.search(r"https?://|www\.", text, re.I):
            out.append(Violation("url", "mock text never carries a URL", _mask_sample(text)))
    return out


def assert_fixture_safe(
    texts: Iterable[str] | Any,
    *,
    allowed_names: Iterable[str] = (),
    check_urls: bool = True,
    config: SpecConfig | None = None,
) -> None:
    """Raise `FixtureSafetyError` when any text breaks a rule of `find_unsafe`. Accepts a nested structure."""
    items = (
        list(iter_strings(texts))
        if not isinstance(texts, (list, tuple, set))
        else list(iter_strings(list(texts)))
    )
    bad = find_unsafe(items, allowed_names=allowed_names, check_urls=check_urls, config=config)
    if bad:
        raise FixtureSafetyError(bad)


# ======================================================================== time and values


def rel_time(days: float = 0.0, *, hours: float = 0.0, minutes: float = 0.0) -> str:
    """A moment relative to now, as `now`, `now-12d`, `now+3d`, `now-5h` or `now-30m`. Dates never carry a clock."""
    if days:
        n, unit = days, "d"
    elif hours:
        n, unit = hours, "h"
    elif minutes:
        n, unit = minutes, "m"
    else:
        return "now"
    sign = "-" if n < 0 else "+"
    return f"now{sign}{abs(round(n))}{unit}"


def _words(key: str) -> set[str]:
    return set(key.lower().split("_"))


def _is_future_key(key: str) -> bool:
    fut = set(mock_tables()["date_keys_in_future"])
    return bool(_words(key) & fut) or any(f in key.lower() for f in fut if len(f) > 4)


def status_key(entity: Entity) -> str | None:
    """The field that holds the lifecycle state: the enum whose values are the states, else a field named status."""
    states = set(entity.lifecycle.states)
    for f in entity.fields:
        if f.enum_values and states and set(f.enum_values) == states:
            return f.key
    for f in entity.fields:
        if f.key == "status":
            return f.key
    return None


def _text_kind(key: str) -> str | None:
    hints = mock_tables()["text_key_hints"]
    w = _words(key)
    for kind in ("person", "place"):
        if w & set(hints[kind]):
            return kind
    return None


def _slug(name: str, n: int) -> str:
    latin = re.sub(
        r"[^a-z0-9]+",
        ".",
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower(),
    ).strip(".")
    return latin if len(latin) >= 3 else f"user.{n}"


@dataclass
class _Ctx:
    graph: SpecGraph
    pool: Pool
    rnd: random.Random
    tables: dict[str, Any]
    n: int = 0

    def person(self) -> str:
        return self.rnd.choice(self.pool.people)

    def place(self) -> str:
        return self.rnd.choice(self.pool.places)


def _fallback_value(ctx: _Ctx, e: Entity, f: FieldDef, idx: int) -> str | None:
    """A believable value for one field when the hero content did not write it (typical gap fill, busy and
    extreme volume). Phone and ID-like values are masked; emails are at example.com; secrets are omitted."""
    r, t = ctx.rnd, f.type
    if t in ("id", "secret", "file", "list", "ref"):
        return None
    if t == "phone":
        a, b = r.randint(10, 99), r.randint(100, 999)
        return mask_phone(f"+919{a}00{r.randint(10, 99)}{b}")
    if t == "email":
        return f"{_slug(ctx.person(), idx)}@example.com"
    if t == "money":
        return format_money(r.randint(40, 4800) * 5, ctx.pool)
    if t in ("number", "quantity"):
        return str(r.randint(1, 40))
    if t == "toggle":
        return r.choice(["true", "false"])
    if t == "date":
        return rel_time(r.randint(-90, -1) if not _is_future_key(f.key) else r.randint(1, 30))
    if t in ("choice", "enum"):
        return r.choice(f.enum_values) if f.enum_values else f"option {r.randint(1, 4)}"
    if t == "code":
        prefix = re.sub(r"[^A-Z]", "", e.name.upper())[:4] or "CODE"
        return f"{prefix}{r.randint(10, 99)}"
    if t == "location":
        return ctx.place()
    kind = _text_kind(f.key)
    if kind == "person":
        return ctx.person()
    if kind == "place":
        return ctx.place()
    return f"{e.name} {idx + 1}"


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _scale_number_text(text: str, factor: float, pool: Pool) -> str:
    """Replace the first number in a text by itself times `factor`, in the locale's format."""
    m = _NUM_RE.search(text)
    if not m:
        return text
    raw = m.group().replace(",", "")
    dec = len(raw.split(".")[1]) if "." in raw else 0
    value = Decimal(raw) * Decimal(str(round(factor, 3)))
    new = format_number(
        value.quantize(Decimal(1).scaleb(-dec)) if dec else value.to_integral_value(),
        pool,
        decimals=dec,
    )
    return text[: m.start()] + new + text[m.end() :]


# ======================================================================== rows from hero records


def _hero_by_entity(graph: SpecGraph, rnd: random.Random) -> dict[str, list[HeroRecord]]:
    hero = graph.hero
    if not hero:
        from .hero import mint_hero_skeleton

        hero = mint_hero_skeleton(graph, random.Random(rnd.getrandbits(32)))  # noqa: S311
    out: dict[str, list[HeroRecord]] = {}
    for hid in sorted(hero):
        out.setdefault(hero[hid].entity, []).append(hero[hid])
    return out


def _scope_entities(graph: SpecGraph) -> list[Entity]:
    return [e for _, e in sorted(graph.entities.items()) if not e.later and e.status == "in"]


def _row(
    ctx: _Ctx, e: Entity, rec: HeroRecord, row_id: str, idx: int, *, vary: bool = False
) -> dict[str, Any]:
    """One mock row of an entity from a hero record. `vary` redraws dates, quantities and names (busy volume)."""
    t = ctx.tables["busy"]
    lo, hi = (float(x) for x in t["quantity_scale"])
    fields: dict[str, str] = {}
    skey = status_key(e)
    for f in e.fields:
        if f.key == "id" or f.type in ("id", "secret", "ref") or f.key in rec.relations:
            continue
        if skey and f.key == skey and rec.state:
            fields[f.key] = rec.state
            continue
        have = rec.content.get(f.key)
        if vary and f.type == "date":
            span = int(t["date_spread_days"])
            fields[f.key] = rel_time(
                ctx.rnd.randint(1, 30) if _is_future_key(f.key) else -ctx.rnd.randint(0, span)
            )
        elif vary and f.type in ("number", "quantity", "money") and have:
            fields[f.key] = _scale_number_text(have, ctx.rnd.uniform(lo, hi), ctx.pool)
        elif vary and f.type == "text" and _text_kind(f.key):
            val = _fallback_value(ctx, e, f, idx)
            if val is not None:
                fields[f.key] = val
        elif have:
            fields[f.key] = have
        else:
            val = _fallback_value(ctx, e, f, idx)
            if val is not None:
                fields[f.key] = val
    row: dict[str, Any] = {
        "id": row_id,
        "entity": e.id,
        "state": rec.state,
        "fields": fields,
        "relations": dict(rec.relations),
        "synthetic": True,
    }
    if rec.image_brief:
        row["image_brief"] = rec.image_brief
    return row


def _select_typical(recs: list[HeroRecord], cap: int) -> list[HeroRecord]:
    """Every lifecycle state first (the first record of each), then the rest by id, up to `cap`."""
    chosen: dict[str, HeroRecord] = {}
    seen_states: set[str | None] = set()
    for r in recs:
        if r.state not in seen_states:
            seen_states.add(r.state)
            chosen[r.id] = r
    for r in recs:
        if len(chosen) >= cap:
            break
        chosen.setdefault(r.id, r)
    return [chosen[i] for i in sorted(chosen)]


def _profile_empty(entities: list[Entity]) -> dict[str, Any]:
    return {
        "name": "empty",
        "made_by": "code",
        "first_run": True,
        "entities": {e.id: {"count": 0, "rows": []} for e in entities},
    }


def _profile_typical(
    ctx: _Ctx, entities: list[Entity], heroes: dict[str, list[HeroRecord]]
) -> dict[str, Any]:
    t = ctx.tables["typical"]
    picked: dict[str, list[HeroRecord]] = {}
    for e in entities:
        cap = int(t["rows_cap_primary"] if e.klass == "primary" else t["rows_cap_other"])
        picked[e.id] = _select_typical(heroes.get(e.id, []), cap)
    by_id = {r.id: r for rs in heroes.values() for r in rs}
    have = {r.id for rs in picked.values() for r in rs}
    changed = True
    while changed:  # keep relations whole: a row's parents are in the profile too
        changed = False
        for rs in list(picked.values()):
            for r in list(rs):
                for pid in r.relations.values():
                    parent = by_id.get(pid)
                    if parent is not None and pid not in have and parent.entity in picked:
                        picked[parent.entity].append(parent)
                        have.add(pid)
                        changed = True
    unfilled = 0
    out: dict[str, Any] = {}
    for e in entities:
        rows = []
        for i, r in enumerate(sorted(picked[e.id], key=lambda x: x.id)):
            if not r.filled:
                unfilled += 1
            rows.append(_row(ctx, e, r, r.id, i))
        out[e.id] = {"count": len(rows), "rows": rows}
    return {
        "name": "typical",
        "made_by": "model" if unfilled == 0 else "model_with_code_fill",
        "unfilled_records": unfilled,
        "entities": out,
    }


def _state_weight(rec: HeroRecord, states: list[str], mode: str) -> float:
    if mode == "uniform" or rec.state not in states:
        return 1.0
    return 1.0 / (1 + states.index(rec.state))


def _profile_busy(
    ctx: _Ctx, entities: list[Entity], heroes: dict[str, list[HeroRecord]]
) -> dict[str, Any]:
    t = ctx.tables["busy"]
    mode = ctx.tables["recipes"]["state_weights"]
    plan: dict[str, list[tuple[str, HeroRecord]]] = {}
    for e in entities:
        recs = heroes.get(e.id, [])
        if not recs:
            plan[e.id] = []
            continue
        want = int(t["rows_primary"] if e.klass == "primary" else t["rows_other"])
        rows: list[tuple[str, HeroRecord]] = [(r.id, r) for r in recs]
        weights = [_state_weight(r, e.lifecycle.states, mode) for r in recs]
        k = 0
        while len(rows) < max(want, len(recs)):
            k += 1
            base = ctx.rnd.choices(recs, weights=weights, k=1)[0]
            rows.append((f"{base.id}~{k:03d}", base))
        plan[e.id] = rows
    rows_of: dict[str, list[str]] = {}
    for rows in plan.values():
        for rid, base in rows:
            rows_of.setdefault(base.id, []).append(rid)
    out: dict[str, Any] = {}
    for e in entities:
        built = []
        for i, (rid, base) in enumerate(plan[e.id]):
            row = _row(ctx, e, base, rid, i, vary=rid != base.id)
            if (
                rid != base.id
            ):  # a volume row keeps the shape of its source: same parent entity, another parent row
                row["relations"] = {
                    key: ctx.rnd.choice(rows_of.get(pid, [pid]))
                    for key, pid in base.relations.items()
                }
            built.append(row)
        out[e.id] = {"count": len(built), "rows": built}
    profile = {"name": "busy", "made_by": "code", "entities": out}
    _apply_shared(ctx.graph, profile)
    return profile


def _apply_shared(graph: SpecGraph, profile: dict[str, Any]) -> None:
    """A value that appears in two entities is copied from the parent row, never drawn twice (FN-32)."""
    from .hero import shared_value_plan

    rows = {r["id"]: r for ent in profile["entities"].values() for r in ent["rows"]}
    for sv in shared_value_plan(graph):
        for r in profile["entities"].get(sv.entity, {}).get("rows", []):
            parent = rows.get(r["relations"].get(sv.via, ""))
            if parent is not None and sv.from_field in parent["fields"]:
                r["fields"][sv.field] = parent["fields"][sv.from_field]


def _pool_or_none(language: str) -> Pool | None:
    try:
        return pool_for_language(language)
    except LocaleFallbackError:
        return None


def _extreme_text(
    ctx: _Ctx, cls: str, allowed: Mapping[str, Sequence[str]], used: dict[str, set[str]]
) -> str:
    ext = ctx.tables["extreme"]
    r = ctx.rnd
    if cls == "long_text":
        parts: list[str] = []
        while sum(len(p) + 1 for p in parts) < int(ext["long_text_chars"]):
            parts.append(r.choice(ctx.pool.people) + " " + r.choice(ctx.pool.places))
        return " ".join(parts)
    if cls == "mixed_script":
        langs = sorted(set(ctx.graph.meta.languages) | {"en", "ar"})
        bits = [r.choice(p.people) for p in map(_pool_or_none, langs) if p is not None]
        return " / ".join(bits)
    cats = ext["category_by_class"].get(cls, [])
    pool: list[tuple[str, str]] = [(c, s) for c in cats for s in allowed.get(c, ())]
    if not pool:
        return ctx.person()
    cat, s = r.choice(pool)
    used.setdefault(cat, set()).add(s)
    return s


def _profile_extreme(
    ctx: _Ctx, entities: list[Entity], heroes: dict[str, list[HeroRecord]]
) -> dict[str, Any]:
    ext = ctx.tables["extreme"]
    allowed = load_allowed_strings()
    classes = [c for c in ext["edge_classes"] if c not in ("zero", "one", "many")]
    used: dict[str, set[str]] = {}
    out: dict[str, Any] = {}
    for e in entities:
        recs = heroes.get(e.id, [])
        rows = []
        if recs:
            for i in range(int(ext["rows_per_entity"])):
                base = recs[i % len(recs)]
                rid = f"{base.id}~x{i + 1:02d}"
                row = _row(ctx, e, base, rid, i, vary=True)
                cls = classes[i % len(classes)]
                row["edge_class"] = cls
                for f in e.fields:
                    if f.type == "text" and f.key in row["fields"]:
                        row["fields"][f.key] = _extreme_text(ctx, cls, allowed, used)
                    elif cls == "large_number" and f.type in ("money", "number", "quantity"):
                        amt = ctx.rnd.choice(ext["large_amounts"])
                        row["fields"][f.key] = (
                            format_money(amt, ctx.pool)
                            if f.type == "money"
                            else format_number(amt, ctx.pool)
                        )
                    elif f.type == "date" and f.key in row["fields"] and cls == "large_number":
                        row["fields"][f.key] = rel_time(-36500 if i % 2 else 36500)
                rows.append(row)
        out[e.id] = {
            "count": len(rows),
            "rows": rows,
            "list_cases": {
                "zero": 0,
                "one": 1,
                "many": {"recipe": f"RCP-{e.id}", "count": int(ext["many_count"]), "virtual": True},
            },
        }
    _apply_shared(ctx.graph, {"entities": out})
    return {
        "name": "extreme",
        "made_by": "code",
        "entities": out,
        "blns_categories_used": sorted(used),
        "blns_strings_used": sorted({s for v in used.values() for s in v}),
    }


# ---------------------------------------------------------------------- broken, offline


def _taxonomy() -> dict[str, Any]:
    return _pool_yaml("error_taxonomy.yaml")


def _applies(kind: str, op: Any) -> bool:
    is_get = op.method == "GET"
    by_id = (
        ":id" in op.path
        or "{id}" in op.path
        or (is_get and not op.paged and op.kind == "crud" and "/" in op.path.strip("/"))
    )
    return {
        "any": True,
        "read": is_get,
        "write": not is_get,
        "by_id": by_id,
        "role_gated": bool(op.roles),
        "idempotent": bool(op.idempotent),
        "paged": bool(op.paged),
    }.get(kind, False)


def _profile_broken(graph: SpecGraph) -> dict[str, Any]:
    tax = _taxonomy()
    inj: list[dict[str, Any]] = []
    for oid in sorted(graph.operations):
        op = graph.operations[oid]
        if op.status != "in":
            continue
        for k in tax["kinds"]:
            if _applies(k["applies_when"], op):
                inj.append(
                    {
                        "operation": oid,
                        "error": k["code"],
                        "status": k["status"],
                        "trigger": k["trigger"],
                        "recovery": k["recovery"],
                        "delay_ms": k["delay_ms"],
                        "source": "taxonomy",
                    }
                )
        for err in op.errors:
            inj.append(
                {
                    "operation": oid,
                    "error": err.code,
                    "status": None,
                    "trigger": tax["operation_error_trigger"],
                    "recovery": err.recovery or "stay",
                    "delay_ms": 300,
                    "source": "operation",
                }
            )
    return {
        "name": "broken",
        "made_by": "code",
        "data_from": "typical",
        "injections": inj,
        "operations_covered": sorted({i["operation"] for i in inj}),
    }


def _queued_actions(graph: SpecGraph) -> list[dict[str, Any]]:
    queued = []
    for sid in sorted(graph.screens):
        for a in sorted(graph.screens[sid].actions, key=lambda x: x.key):
            if "offline" in a.flags:
                for s in a.effect:
                    if s.verb == "call" and s.target:
                        queued.append({"screen": sid, "action": a.key, "operation": s.target})
    if not queued:
        for oid in sorted(graph.operations):
            op = graph.operations[oid]
            if op.method != "GET" and op.idempotent and op.status == "in":
                queued.append({"screen": None, "action": None, "operation": oid})
    return queued[:6]


def _profile_offline(graph: SpecGraph, typical: dict[str, Any]) -> dict[str, Any]:
    cache = {
        eid: {
            "from": "typical",
            "row_ids": [r["id"] for r in ent["rows"]],
            "cached_at": rel_time(minutes=-30),
        }
        for eid, ent in typical["entities"].items()
    }
    queued = [
        dict(q, queued_at=rel_time(minutes=-5 * (i + 1)), payload_from="typical")
        for i, q in enumerate(_queued_actions(graph))
    ]
    return {
        "name": "offline",
        "made_by": "code",
        "network": "down",
        "cache": cache,
        "queued_actions": queued,
        "sync": {"on_reconnect": "replay_in_order", "conflicts": "show_conflict_state"},
    }


# ======================================================================== public API


def profile_names(
    profile: str, extra: Sequence[str] = (), *, config: SpecConfig | None = None
) -> list[str]:
    cfg = config or default_config()
    names = list(cfg.profiles.mock_profiles[profile])
    for x in extra:
        if x not in names:
            names.append(x)
    known = vocab.mock_profiles()
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown mock profiles {unknown}")
    return names


def build_mock_profiles(
    graph: SpecGraph,
    profile: str,
    rnd: random.Random,
    *,
    extra: Sequence[str] = (),
    config: SpecConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """The mock profiles a detail profile ships (config.profiles.mock_profiles; `extra` adds one on request, for
    example `extreme` for a standard app). Each profile is an independent seeded draw: the result does not depend
    on the order of the profiles. Dates are relative to now. Same graph and seed, same bytes."""
    names = profile_names(profile, extra, config=config)
    base = rnd.getrandbits(64)
    pool = pool_for_language(graph.meta.source_language)
    tables = mock_tables()
    entities = _scope_entities(graph)
    heroes = _hero_by_entity(graph, random.Random(base))  # noqa: S311

    def ctx_for(name: str) -> _Ctx:
        return _Ctx(graph, pool, random.Random(f"{base}:{name}"), tables)  # noqa: S311

    out: dict[str, dict[str, Any]] = {}
    typical = _profile_typical(ctx_for("typical"), entities, heroes)
    for name in names:
        if name == "empty":
            out[name] = _profile_empty(entities)
        elif name == "typical":
            out[name] = typical
        elif name == "busy":
            out[name] = _profile_busy(ctx_for("busy"), entities, heroes)
        elif name == "extreme":
            out[name] = _profile_extreme(ctx_for("extreme"), entities, heroes)
        elif name == "broken":
            out[name] = _profile_broken(graph)
        elif name == "offline":
            out[name] = _profile_offline(graph, typical)
        else:  # pragma: no cover - guarded by profile_names
            raise ValueError(name)
    for p in out.values():
        p["time"] = {"anchor": "now", "format": "now[+-]<n><d|h|m>"}
        p["language"] = pool.language
        p["needs_native_review"] = pool.needs_native_review
        p["synthetic"] = True
    return out


def _slug_role(name: str, n: int) -> str:
    return _slug(name, n).replace(".", "-")


def build_demo_accounts(
    graph: SpecGraph, profiles: Mapping[str, Mapping[str, Any]], rnd: random.Random
) -> list[dict[str, Any]]:
    """One demo account per role, built from hero records. A demo code or an email at example.com; NEVER a phone
    number. The name comes from the locale pool; the first role of each app is the store reviewer's account."""
    cfg = mock_tables()["demo_accounts"]
    pool = pool_for_language(graph.meta.source_language)
    typical = profiles.get("typical", {}).get("entities", {})
    first_roles = {a.role_ids[0] for a in graph.apps.values() if a.role_ids}
    accounts = []
    for n, rid in enumerate(sorted(graph.roles), start=1):
        role = graph.roles[rid]
        if role.status != "in":
            continue
        r = random.Random(f"demo:{rid}")  # noqa: S311 - stable per role, independent of other draws
        owns: dict[str, list[str]] = {}
        for e in _scope_entities(graph):
            acts = {a for row in e.access if row.role_id == rid for a in row.actions}
            if acts - {"none"}:
                ids = [row["id"] for row in typical.get(e.id, {}).get("rows", [])]
                if ids:
                    owns[e.id] = ids[: int(cfg["records_per_entity"])]
        digits = re.sub(r"\D", "", rid) or str(n)
        accounts.append(
            {
                "role": rid,
                "app_ids": sorted(role.app_ids),
                "display_name": r.choice(pool.people),
                "login": {
                    "email": f"demo.{_slug_role(role.name, n)}@{cfg['email_domain']}",
                    "demo_code": f"{cfg['code_prefix']}-{digits}-{r.randint(1000, 9999)}",
                },
                "profile": "typical",
                "records": owns,
                "reviewer_demo": rid in first_roles,
                "synthetic": True,
            }
        )
    return accounts


def build_recipes(graph: SpecGraph, rnd: random.Random) -> list[dict[str, Any]]:
    """Seeded generators for volume: one per primary entity (and any with a typical count). The seed comes from the
    program id and the entity id, so it is the same on every run and in every app."""
    t = mock_tables()
    recipes = []
    for e in _scope_entities(graph):
        if e.klass != "primary" and not e.typical_count:
            continue
        seed = int(sha256_text(f"{graph.meta.program_id}:{e.id}")[:8], 16)
        recipes.append(
            {
                "id": f"RCP-{e.id}",
                "entity": e.id,
                "seed": seed,
                "count": int(
                    t["busy"]["rows_primary"] if e.klass == "primary" else t["busy"]["rows_other"]
                ),
                "states": {
                    "weights": t["recipes"]["state_weights"],
                    "values": list(e.lifecycle.states),
                },
                "vary": {
                    "dates": [f.key for f in e.fields if f.type == "date"],
                    "numbers": [
                        f.key for f in e.fields if f.type in ("number", "quantity", "money")
                    ],
                    "names": [f.key for f in e.fields if f.type == "text" and _text_kind(f.key)],
                },
                "relations": {
                    f.key: f.ref_entity for f in e.fields if f.type == "ref" and f.ref_entity
                },
                "edge_class_share": t["recipes"]["edge_class_share"],
                "date_window_days": t["busy"]["date_spread_days"],
            }
        )
    return recipes


def build_behaviours(graph: SpecGraph, rnd: random.Random) -> dict[str, Any]:
    """Latency per operation, paging, an auth expiry, realtime scripts (a lifecycle advancing step by step) and
    provider callbacks (a payment succeeding or failing), all from `mock_tables.yaml` and the graph."""
    b = mock_tables()["behaviours"]
    latency = {}
    for oid in sorted(graph.operations):
        op = graph.operations[oid]
        kind = "read" if op.method == "GET" else ("custom" if op.kind == "custom" else "write")
        if any(f.type == "file" for f in op.request):
            kind = "upload"
        latency[oid] = {
            "class": b["latency_by_kind"][kind],
            **b["latency_ms"][b["latency_by_kind"][kind]],
        }
    realtime = []
    live_entities = sorted(
        {
            f"{r.bind[0].split('.')[0]}"
            for s in graph.screens.values()
            for r in s.regions
            if r.live == "realtime" and r.bind
        }
    )
    for eid in live_entities:
        e = graph.entities.get(eid)
        if e is None or not e.lifecycle.states:
            continue
        steps = e.lifecycle.states[: int(b["realtime"]["max_steps"])]
        realtime.append(
            {
                "entity": eid,
                "script": [
                    {"to_state": s, "after_ms": int(b["realtime"]["step_ms"]) * (i + 1)}
                    for i, s in enumerate(steps[1:])
                ],
            }
        )
    callbacks = []
    for iid in sorted(graph.integrations):
        it = graph.integrations[iid]
        if it.provider_side_flow:
            callbacks.append(
                {
                    "integration": iid,
                    "outcomes": [
                        {"event": o, "after_ms": b["provider_callbacks"]["delay_ms"].get(o, 2500)}
                        for o in (it.events or b["provider_callbacks"]["outcomes"])
                    ],
                    "pending_after_ms": b["provider_callbacks"]["delay_ms"]["pending"],
                    "interrupted_flow": it.interruption,
                    "pending_timeout": it.pending_timeout,
                }
            )
    return {
        "latency": latency,
        "paging": dict(b["paging"]),
        "auth_expiry": dict(b["auth_expiry"]),
        "realtime_scripts": realtime,
        "provider_callbacks": callbacks,
        "synthetic": True,
    }


def build_mock_bundle(
    graph: SpecGraph,
    profile: str,
    rnd: random.Random,
    *,
    extra: Sequence[str] = (),
    allowed_names: Iterable[str] = (),
    config: SpecConfig | None = None,
) -> dict[str, Any]:
    """Profiles, demo accounts, recipes and behaviours in one result, checked by `assert_fixture_safe`."""
    profiles = build_mock_profiles(graph, profile, rnd, extra=extra, config=config)
    bundle = {
        "profiles": profiles,
        "demo_accounts": build_demo_accounts(graph, profiles, rnd),
        "recipes": build_recipes(graph, rnd),
        "behaviours": build_behaviours(graph, rnd),
    }
    assert_fixture_safe(bundle, allowed_names=allowed_names, config=config)
    return bundle
