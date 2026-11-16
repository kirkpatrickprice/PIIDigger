import re

DH_NAME = "pan"
DH_DISPLAY_NAME = "Primary Account Number"

# Card-number regexes, built from named parts.  The brand prefixes started from
# https://github.com/citypay/citypay-pan-search/tree/master/src/test/resources
#
# If you know of more, or can shed light on any corrections, please submit an issue at
# https://github.com/kirkpatrickprice/PIIDigger/issues or submit a PR on the repo.

# A card's own digits: ASCII only.  \d would also match other scripts' digits,
# such as the fullwidth digits that turn up in decoded PDF and binary text.
_D = r"[0-9]"

# Not next to a digit or a hyphen, and not joined to a digit by a dot.  So a
# number inside a longer one, a decimal or a UUID-like run is not read as a card,
# but one after "No." or before a full stop is.  These use \d, which matches a
# digit in any script, so a fullwidth or Arabic-Indic digit beside the number
# still marks it as part of a longer one.
_START = r"(?<![\d-])(?<!\d\.)"
_END = r"(?![\d-]|\.\d)"

# The separator a number may be written with.  Before matching, ContentBuffer
# turns line breaks and tabs into spaces, and the spreadsheet handlers join cells
# with spaces, so a space may also stand for a line, tab or cell boundary.  That
# is why _grouped() accepts only a brand's own grouping: two 8-digit numbers on
# adjacent lines arrive as "40406324 52986916", which is not one.  Short values
# that do form a brand's grouping (4+4+4+4, or 4+6+5 for Amex and JCB15) still
# can; see TECHNICAL_DEBT.md.
_SEP = r"[ -]"

# The first four digits of each brand.
_VISA = rf"4{_D}{{3}}"
_MASTERCARD = rf"(?:5[1-5]{_D}{{2}}|222[1-9]|22[3-9]{_D}|2[3-6]{_D}{{2}}|27[01]{_D}|2720)"  # 5100-5599, 2221-2720
_DISCOVER = rf"(?:6011|64[4-9]{_D}|65{_D}{{2}})"  # 6011, 6440-6599
_AMEX = rf"3[47]{_D}{{2}}"
_JCB16 = rf"35(?:2[89]|[3-8]{_D})"  # 3528-3589
_JCB15 = r"(?:2131|1800)"  # discontinued 15-digit JCB


def _grouped(prefix: str, groups: tuple[int, ...]) -> re.Pattern[str]:
    """A card number: prefix, then the brand's digit groups, with no separators or one throughout.

    groups are the sizes of the groups after the four-digit prefix.  The
    separator is captured once and repeated by backreference, so a number
    written 4-4-4-4 must use the same character at every boundary.  Mixed or
    partial grouping, such as 8+8 digits, is not a card number in any brand's
    own format, and is rejected.

    The number is matched inside a lookahead and captured as "pan", so a match
    consumes nothing and the search tries every start.  A plain match would
    resume after a candidate that fails Luhn and skip a real card that overlaps
    it: "4521 4111 1111 1111 1111" first matches "4521 4111 1111 1111".
    """
    plain = rf"{_D}{{{sum(groups)}}}"
    separated = rf"(?P<sep>{_SEP})" + "(?P=sep)".join(rf"{_D}{{{size}}}" for size in groups)
    # The leading (?=[0-9]) is a cheap first test, so a position that can't start
    # a card skips the lookbehinds: it halves the time on text with few digits.
    return re.compile(rf"(?={_D}){_START}(?=(?P<pan>{prefix}(?:{plain}|{separated}){_END}))")


_GROUPS_4444 = (4, 4, 4)  # 16 digits: 4-4-4-4
_GROUPS_465 = (6, 5)  # 15 digits: 4-6-5

# Each brand's own formats.  JCB has two: current 16-digit cards grouped like
# Visa, and the discontinued 15-digit range, which has no published grouping
# and is taken to follow Amex's 4-6-5.  Both report as "jcb".
_REGEXES: dict[str, tuple[re.Pattern[str], ...]] = {
    "visa": (_grouped(_VISA, _GROUPS_4444),),
    "mc": (_grouped(_MASTERCARD, _GROUPS_4444),),
    "discover": (_grouped(_DISCOVER, _GROUPS_4444),),
    "jcb": (_grouped(_JCB16, _GROUPS_4444), _grouped(_JCB15, _GROUPS_465)),
    "amex": (_grouped(_AMEX, _GROUPS_465),),
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
        for brand, patterns in _REGEXES.items():
            for pattern in patterns:
                # The number is the "pan" group; the match itself is empty (see
                # _grouped).  finditer, because findall would return every group.
                for found in pattern.finditer(text):
                    match = found.group("pan")
                    if _is_valid(match):
                        results.setdefault(brand, set()).add(_redact(match))
        return results


handler = PanHandler()
