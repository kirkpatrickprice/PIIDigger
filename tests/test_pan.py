import pytest

from piidigger.datahandlers import pan
from piidigger.filehandlers._sharedfuncs import ContentBuffer


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "data, expected_result",
    [
        ("4893 0133 3538 6137", {"visa": {"4893 01** **** 6137"}}),
        ("4684399293674835", {"visa": {"468439******4835"}}),
        ("4556-7375-8689-9855", {"visa": {"4556-73**-****-9855"}}),
        ("4098724854267035", {}),
        ("John Doe", {}),
        ("jdoe@example.com", {}),
        ("4012001037140001514E100010003220121800000011150", {}),
        ("3782-822463-10005", {"amex": {"3782-82****-*0005"}}),
        ("371449635398431", {"amex": {"371449*****8431"}}),
        ("3787 344936 71000", {"amex": {"3787 34**** *1000"}}),
        ("345606077182423", {}),
        ("3579964259818823", {"jcb": {"357996******8823"}}),
        ("3559390822709303", {"jcb": {"355939******9303"}}),
        ("3578488152861707", {"jcb": {"357848******1707"}}),
    ],
)
def testIsValidPan(data, expected_result):
    result = pan.handler.find_matches(data)
    assert result == expected_result


_VISAS = ["4111111111111111", "4012888888881881", "4684399293674835", "4893013335386137", "4556737586899855"]
_VISAS_REDACTED = {"411111******1111", "401288******1881", "468439******4835", "489301******6137", "455673******9855"}


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text",
    [
        "\n".join(_VISAS),
        ",".join(f'"{n}"' for n in _VISAS),
    ],
    ids=["one-per-line", "csv-row"],
)
def test_back_to_back_pans_are_all_found(text):
    # The boundary around each match must not be consumed, or the next number
    # has none left and every other one is missed.  The quotes and commas must
    # not end up in the reported values either.
    assert pan.handler.find_matches(text) == {"visa": _VISAS_REDACTED}


@pytest.mark.datahandlers
@pytest.mark.parametrize("text", ["1.4111111111111111", "4111111111111111.5", "4111111111111111-", "-4111111111111111"])
def test_pan_next_to_a_dot_or_hyphen_is_rejected(text):
    assert pan.handler.find_matches(text) == {}


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text, expected",
    [
        ("5555555555554444", {"mc": {"555555******4444"}}),
        ("5555 5555 5555 4444", {"mc": {"5555 55** **** 4444"}}),
        ("5555-5555-5555-4444", {"mc": {"5555-55**-****-4444"}}),
        ("6011111111111117", {"discover": {"601111******1117"}}),
        ("6011 1111 1111 1117", {"discover": {"6011 11** **** 1117"}}),
        ("3528000000000007", {"jcb": {"352800******0007"}}),
        ("3589-0000-0000-0003", {"jcb": {"3589-00**-****-0003"}}),
        ("213100000000001", {"jcb": {"213100*****0001"}}),
        ("1800 000000 00002", {"jcb": {"1800 00**** *0002"}}),
        ("2221000000000009", {"mc": {"222100******0009"}}),
        ("2720 9999 9999 9996", {"mc": {"2720 99** **** 9996"}}),
        ("6445644564456445", {"discover": {"644564******6445"}}),
        ("6499-0000-0000-0005", {"discover": {"6499-00**-****-0005"}}),
        ("6500000000000002", {"discover": {"650000******0002"}}),
        ("card 4111111111111111.", {"visa": {"411111******1111"}}),
        ("4111 1111 1111 1111. Next", {"visa": {"4111 11** **** 1111"}}),
    ],
    ids=[
        "mc-plain",
        "mc-spaces",
        "mc-hyphens",
        "discover-plain",
        "discover-spaces",
        "jcb16-3528",
        "jcb16-3589-hyphens",
        "jcb15-plain",
        "jcb15-4-6-5",
        "mc-2221",
        "mc-2720-spaces",
        "discover-644",
        "discover-6499-hyphens",
        "discover-65",
        "full-stop",
        "full-stop-grouped",
    ],
)
def test_brand_formats_are_found(text, expected):
    assert pan.handler.find_matches(text) == expected


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text",
    [
        "40406324 52986916",  # 8+8, as two numbers on adjacent lines read
        "40406324-52986916",
        "4040 632452986916",  # one separator, not one at every boundary
        "4040-6324 5298-6916",  # mixed separators
        "4040 6324-5298 6916",
        "3782 822463-10005",  # Amex 4-6-5 with mixed separators
        "3527000000000008",  # below the JCB 3528-3589 range
        "3590000000000000",  # above it
        "2220999999999991",  # below the Mastercard 2221-2720 range
        "2721000000000004",  # above it
        "6439000000000008",  # below Discover's 644-649
    ],
)
def test_numbers_not_in_a_brand_format_are_rejected(text):
    # Every one of these passes the Luhn check, so only the format rejects it.
    assert pan._is_valid(text)
    assert pan.handler.find_matches(text) == {}


@pytest.mark.datahandlers
@pytest.mark.parametrize("line_break", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_numbers_on_adjacent_lines_are_not_joined_into_a_card(line_break):
    # The text handler turns line breaks into spaces, so two 8-digit numbers on
    # adjacent lines reach the matcher as "40406324 52986916".
    buffer = ContentBuffer(1024)
    for line in f"40406324{line_break}52986916{line_break}".splitlines(keepends=True):
        buffer.append_content(line)
    assert pan.handler.find_matches(buffer.finalize_content()) == {}


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text",
    [
        "4\uff11\uff11\uff11111111111111",
        "411111111111\u0661\u0661\u0661\u0661",
        "\uff114111111111111111",  # a longer number: another digit before it
        "4111111111111111\u0661",  # and after it
    ],
    ids=["fullwidth", "arabic-indic", "fullwidth-digit-before", "arabic-indic-digit-after"],
)
def test_digits_from_other_scripts_are_not_card_digits(text):
    assert pan.handler.find_matches(text) == {}


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text, expected",
    [
        # "4521 4111 1111 1111" matches first and fails Luhn; the real card overlaps it.
        ("Order 4521 4111 1111 1111 1111", {"visa": {"4111 11** **** 1111"}}),
        ("Card No.4111111111111111", {"visa": {"411111******1111"}}),
        ("Card No.4111 1111 1111 1111", {"visa": {"4111 11** **** 1111"}}),
    ],
    ids=["overlap-spaces", "after-no-dot", "after-no-dot-grouped"],
)
def test_cards_next_to_other_text_are_found(text, expected):
    assert pan.handler.find_matches(text) == expected


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "text, expected",
    [
        ("metric.4532015112830366", {"visa": {"453201******0366"}}),
        ("4532015112830366.metric", {"visa": {"453201******0366"}}),
        ("1.4532015112830366", {}),
        ("4532015112830366.1", {}),
    ],
    ids=["word-dot-before", "dot-word-after", "digit-dot-before", "dot-digit-after"],
)
def test_a_dot_blocks_a_card_only_when_it_joins_another_digit(text, expected):
    # The same rule on both sides: a dot next to a digit makes the number part
    # of a decimal; a dot next to anything else is ordinary punctuation.
    assert pan.handler.find_matches(text) == expected
