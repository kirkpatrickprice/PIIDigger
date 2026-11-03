import re

DH_NAME = "email"
DH_DISPLAY_NAME = "Email Address"

# RFC5322 compliant email regex, built from the grammar's own parts.
# Compiled once at module scope to avoid repeated compilation overhead on every call.

# The characters of an unquoted local part, less "-", which each class adds
# last so it cannot be read as a range.
_ATEXT_CHARS = r"a-zA-Z0-9!#$%&\'*+/=?^_`{|}~"
_DOT_ATOM = rf"[{_ATEXT_CHARS}-]+(?:\.[{_ATEXT_CHARS}-]+)*"  # e.g. john.smith
_QTEXT = r"[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]"  # inside "..." except " and \
_QUOTED_PAIR = r"\\[\x01-\x09\x0b\x0c\x0e-\x7f]"  # \" or \\ etc. inside "..."
# Inside a [domain literal]: RFC 5322's dtext, which excludes [ \ ].  This once
# read \x53-\x7f, admitting "\", so a run of backslashes matched as dtext or as
# quoted pairs in every combination: 36 of them took 2.3s, doubling with each 2.
_DTEXT = r"[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x5e-\x7f]"
_LABEL = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"  # one hostname label
_OCTET = r"25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?"  # 0-255

# _is_valid() rejects a local part over 64 characters, so the regex need not
# look further.  Unbounded, each start in a long run with no '@' scanned to the
# run's end, so the time grew with the square of its length: one 277 KB token
# in a source map took 105s.
#
# The bound also changes one result, by choice: a local part over 64
# characters, once rejected whole, now yields its last dotted part within 64
# characters (x...x.yyy@d.com finds yyy@d.com).  A scanner should rather
# report a piece of an address than nothing.
_MAX_LOCAL = 64

_UNQUOTED_LOCAL = (
    rf"(?<![{_ATEXT_CHARS}-])"  # starts where a local part can start
    rf"(?=[{_ATEXT_CHARS}.-]{{1,{_MAX_LOCAL}}}@)"  # and reaches "@" within _MAX_LOCAL
    rf"{_DOT_ATOM}"
)
_QUOTED_LOCAL = rf'"(?:{_QTEXT}|{_QUOTED_PAIR}){{0,{_MAX_LOCAL - 2}}}"'  # 62 items, not characters: a quoted pair is two, so _is_valid() has the last word
_HOSTNAME = rf"(?:{_LABEL}\.)+{_LABEL}"
# Matches user@[192.168.1.1], but _is_valid() requires a letter TLD, so a
# domain literal is never reported today.
_DOMAIN_LITERAL = rf"\[(?:(?:{_OCTET})\.){{3}}(?:{_OCTET}|[a-zA-Z0-9-]*[a-zA-Z0-9]:(?:{_DTEXT}|{_QUOTED_PAIR})+)\]"

_EMAIL_REGEX = re.compile(rf"(?:{_UNQUOTED_LOCAL}|{_QUOTED_LOCAL})@(?:{_HOSTNAME}|{_DOMAIN_LITERAL})")


def _is_valid(text: str) -> bool:
    """Validate if the text is a valid email address."""
    is_valid = {}
    try:
        local_part, domain_part = text.split("@", 1)
    except ValueError:
        return False

    is_valid["contains_at"] = "@" in text and len(text) > 0
    is_valid["single_at"] = text.count("@") == 1
    is_valid["text_before_after_at"] = len(local_part) > 0 and len(domain_part) > 0
    is_valid["local_part_length"] = len(local_part) <= 64
    is_valid["domain_part_length"] = len(domain_part) <= 253
    is_valid["domain_labels_length"] = all(len(label) <= 63 for label in domain_part.split("."))
    is_valid["valid_tld"] = bool(re.search(r"\.[a-zA-Z]{2,63}$", domain_part))

    return all(is_valid.values())


def _redact(text: str, replace_with: str = "*") -> str:
    """Redacts email address local part, leaving domain unchanged.

    Rule 1: 10+ chars before @ — retain first 3 and last 1
    Rule 2: 6–9 chars — retain first and last
    Rule 3: 2–5 chars — retain first only
    Rule 4: 1 char — redact entirely
    """
    if "@" not in text:
        return text

    local_part, domain = text.split("@")
    local_len = len(local_part)

    if local_len == 1:
        return f"{replace_with}@{domain}"
    if local_len <= 5:
        first = local_part[0]
        redacted_local = f"{first}{replace_with * (local_len - 1)}"
    elif local_len < 10:
        first = local_part[0]
        last = local_part[-1]
        redacted_local = f"{first}{replace_with * (local_len - 2)}{last}"
    else:
        first_three = local_part[:3]
        last_one = local_part[-1]
        redacted_local = f"{first_three}{replace_with * (local_len - 4)}{last_one}"

    return f"{redacted_local}@{domain}"


class EmailHandler:
    """DataHandler that detects and redacts email addresses (RFC5322)."""

    name = DH_NAME

    def find_matches(self, text: str) -> dict[str, set[str]]:
        """Match text against RFC5322 email address patterns.

        Returns {'email': {redacted_value, ...}} or {} if no valid addresses found.
        """
        # Cheap prefilter: every valid email address must contain '@'.
        if "@" not in text:
            return {}

        results: dict[str, set[str]] = {}
        raw_matches = _EMAIL_REGEX.findall(text)
        if raw_matches:
            for match in raw_matches:
                match = match.strip()
                if _is_valid(match):
                    if "email" not in results:
                        results["email"] = set()
                    results["email"].add(_redact(match))
        return results


handler = EmailHandler()
