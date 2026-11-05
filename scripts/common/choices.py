"""Helpers for mapping a free-text answer onto one real option."""

import re


def normalize_choice(value):
    """Lowercase and strip everything except letters and digits."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


# Common wording differences, e.g. EEO race/ethnicity lists.
_ALIASES = {
    "asian": ("asian",),
    "black": ("black", "africanamerican"),
    "blackafricanamerican": ("black", "africanamerican"),
    "white": ("white",),
    "hispanic": ("hispanic", "latino", "latina", "latinx"),
    "hispanicorlatino": ("hispanic", "latino", "latina", "latinx"),
    "nativeamerican": ("nativeamerican", "americanindian", "alaskanative"),
    "americanindianoralaskanative": (
        "nativeamerican",
        "americanindian",
        "alaskanative",
    ),
    "pacificislander": ("pacificislander", "nativehawaiian"),
    "nativehawaiianorotherpacificislander": ("pacificislander", "nativehawaiian"),
}


def match_option(option_texts, wanted_value):
    """Return the one available option that matches `wanted_value`, else None.

    Tolerates harmless wording differences, but only returns a match when it is
    unique, so an ambiguous answer is never guessed.
    """
    wanted = normalize_choice(wanted_value)

    if not wanted:
        return None

    normalized_options = [
        (option, normalize_choice(option)) for option in option_texts if option
    ]

    exact = [option for option, norm in normalized_options if norm == wanted]

    if len(exact) == 1:
        return exact[0]

    # "Asian" -> "Asian (Not Hispanic or Latino)": complete prefix or substring
    # of exactly one option.
    partial = [
        option
        for option, norm in normalized_options
        if norm and (norm.startswith(wanted) or wanted in norm)
    ]

    if len(partial) == 1:
        return partial[0]

    # Same words, different order: "+1 (Canada)" vs "Canada (+1)".
    wanted_words = frozenset(re.findall(r"[a-z0-9]+", (wanted_value or "").casefold()))

    if wanted_words:
        same_words = [
            option
            for option, _ in normalized_options
            if frozenset(re.findall(r"[a-z0-9]+", option.casefold())) == wanted_words
        ]

        if len(same_words) == 1:
            return same_words[0]

    tokens = _ALIASES.get(wanted)

    if tokens:
        aliased = [
            option
            for option, norm in normalized_options
            if any(token in norm for token in tokens)
        ]

        if len(aliased) == 1:
            return aliased[0]

    return None
