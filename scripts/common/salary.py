"""Salary helpers.

posted_salary_max()  - top of the salary range stated in the job posting.
pick_salary_option() - the dropdown/radio option whose range contains an amount.
"""

import json
import re

MIN_ANNUAL = 30_000
MAX_ANNUAL = 1_000_000

_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_RANGE = re.compile(r"\$?\s*" + _NUMBER + r"\s*(?:-|–|—|to)\s*\$?\s*" + _NUMBER)
_SALARY_WORDS = ("salary", "compensation", "pay", "range", "base")


def _to_amount(number, k_suffix):
    value = float(number.replace(",", ""))

    if k_suffix:
        value *= 1000

    return int(value)


def _ranges_in_text(text):
    """Return [(low, high, position)] for plausible annual salary ranges."""
    found = []

    for match in _RANGE.finditer(text):
        low = _to_amount(match.group(1), match.group(2))
        high = _to_amount(match.group(3), match.group(4))

        # "$80k - 95k": the second number inherits the k.
        if match.group(2) and not match.group(4) and high < 1000:
            high *= 1000

        if low > high:
            low, high = high, low

        if MIN_ANNUAL <= low and high <= MAX_ANNUAL and high <= low * 3:
            found.append((low, high, match.start()))

    return found


def _max_from_json_fields(data):
    """Look for numeric fields like salary_max / max_compensation in the JSON."""
    best = 0

    def walk(node):
        nonlocal best

        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key).lower()

                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and "max" in name
                    and any(word in name for word in ("salary", "compensation", "pay"))
                    and MIN_ANNUAL <= value <= MAX_ANNUAL
                ):
                    best = max(best, int(value))
                else:
                    walk(value)

        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return best or None


def posted_salary_max(job_posting):
    """Return the top of the posted annual salary range, or None if absent."""
    text = job_posting or ""

    try:
        data = json.loads(text)
    except ValueError:
        data = None

    if data is not None:
        from_fields = _max_from_json_fields(data)

        if from_fields:
            return from_fields

    ranges = _ranges_in_text(text)

    if not ranges:
        return None

    # Prefer a range that is introduced by salary-like wording.
    for low, high, position in ranges:
        before = text[max(0, position - 200) : position].lower()

        if any(word in before for word in _SALARY_WORDS):
            return high

    return ranges[0][1]


def _option_range(text):
    """Parse "$80,000 - $89,000", "+$200,000", "Under $70,000" into (low, high)."""
    lowered = text.lower()
    amounts = [
        _to_amount(number, k_suffix)
        for number, k_suffix in re.findall(_NUMBER, text)
    ]
    amounts = [amount for amount in amounts if amount >= 1000]

    if not amounts:
        return None

    if len(amounts) == 1:
        amount = amounts[0]

        if any(word in lowered for word in ("under", "less than", "below")):
            return (0, amount)

        if "+" in text or any(word in lowered for word in ("over", "more than", "above")):
            return (amount, float("inf"))

        return (amount, amount)

    return (min(amounts[:2]), max(amounts[:2]))


def pick_salary_option(options, amount):
    """Return the option whose range contains `amount` (nearest, if none does)."""
    best = None
    best_key = None

    for option in options:
        option_range = _option_range(option)

        if option_range is None:
            continue

        low, high = option_range

        if low <= amount <= high:
            distance = 0
        else:
            distance = min(abs(amount - low), abs(amount - high))

        # On a tie, prefer the higher bucket.
        key = (distance, -low)

        if best_key is None or key < best_key:
            best = option
            best_key = key

    return best
