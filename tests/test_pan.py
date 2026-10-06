import pytest

from piidigger.datahandlers import pan


@pytest.mark.datahandlers
@pytest.mark.parametrize(
    "data, expected_result",
    [
        ("4893 0133 3538 6137", {"visa": {"4893 01** **** 6137"}}),
        ("4684399293674835", {"visa": {"468439******4835"}}),
        ("4556-7375-8689-9855", {"visa": {"4556-73**-****-9855"}}),
        ("48930133-35386137", {"visa": {"489301**-****6137"}}),
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
@pytest.mark.parametrize("text", ["1.4111111111111111", "4111111111111111-", "-4111111111111111"])
def test_pan_next_to_a_dot_or_hyphen_is_rejected(text):
    assert pan.handler.find_matches(text) == {}
