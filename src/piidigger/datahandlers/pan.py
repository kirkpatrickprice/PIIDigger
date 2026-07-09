import re

DH_NAME = "pan"
DH_DISPLAY_NAME = "Primary Account Number"

# Regexes provided by https://github.com/citypay/citypay-pan-search/tree/master/src/test/resources

# If you know of more, or can shed light on any corrections, please submit an issue at https://github.com/kirkpatrickprice/PIIDigger/issues or submit a PR on the repo
# # Added the |[^-] to exclude strings anchored on a hyphen.  Hopefully reduce UUID false positives
# in log files without missing legit PAN.
_REGEXES = {
    "visa": re.compile(r"(?:^|[^\d.-])4[0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])"),
    "mc": re.compile(r"(?:^|[^\d.-])5[1-5][0-9]{2}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])"),
    "discover": re.compile(r"(?:^|[^\d.-])6011[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])"),
    "jcb": re.compile(r"(?:^|[^\d.-])(?:2131|1800|35[0-9]{3})[0-9]{11}(?:$|[^\d.-])"),
    "amex": re.compile(r"(?:^|[^\d.-])3[47][0-9]{2}[ -]?[0-9]{6}[ -]?[0-9]{5}(?:$|[^\d.-])"),
}


def _is_valid(text: str) -> bool:

    def luhn(n) -> bool:
        # Luhn check taken from https://rosettacode.org/wiki/Luhn_test_of_credit_card_numbers#Python
        r = [int(ch) for ch in str(n)][::-1]
        return (sum(r[0::2]) + sum(sum(divmod(d * 2, 10)) for d in r[1::2])) % 10 == 0

    if not text.isdigit():
        text = "".join(i for i in text if i.isdigit())

    return luhn(text)


def _redact(text: str, replace_with: str = "*") -> str:
    """Redacts PAN to limit to just the first six and last four digits."""
    needs_rejoined = False
    seps: dict[int, str] = {}
    if not text.isdigit():
        pos = 0
        for c in text:
            if not c.isdigit():
                seps[pos] = c
                needs_rejoined = True
            pos += 1
        text = "".join([c for c in text if c.isdigit()])

    last_four_pos = len(text) - 4
    first_six = text[:6]
    middle = replace_with * (last_four_pos - 6)
    last_four = text[last_four_pos:]
    result = first_six + middle + last_four

    if needs_rejoined:
        for pos, sep in seps.items():
            result = result[:pos] + sep + result[pos:]

    return result


class PanHandler:
    """DataHandler that detects and redacts Primary Account Numbers (credit/debit cards)."""

    name = DH_NAME

    def find_matches(self, text: str) -> dict[str, set[str]]:
        """Match text against known credit card number formats.

        Returns {'brand': {redacted_value, ...}} for each brand found.
        """
        results: dict[str, set[str]] = {}
        for brand, pattern in _REGEXES.items():
            raw_matches = pattern.findall(text)
            if raw_matches:
                for match in raw_matches:
                    match = match.strip()
                    if _is_valid(match):
                        if brand not in results:
                            results[brand] = set()
                        results[brand].add(_redact(match))
        return results


handler = PanHandler()
