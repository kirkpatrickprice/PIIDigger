import pytest

from piidigger.datahandlers import pan

@pytest.mark.parametrize('data, expected_result', [
                            ('4893 0133 3538 6137', {'visa': {'4893 01** **** 6137'}}),
                            ('4684399293674835', {'visa': {'468439******4835'}}),
                            ('4556-7375-8689-9855', {'visa': {'4556-73**-****-9855'}}),
                            ('48930133-35386137', {'visa': {'489301**-****6137'}}),
                            ('4098724854267035', {}),
                            ('John Doe', {}),
                            ('jdoe@example.com', {}),
                            ('4012001037140001514E100010003220121800000011150', {}),
                            ('3782-822463-10005', {'amex': {'3782-82****-*0005'}}),
                            ('371449635398431', {'amex': {'371449*****8431'}}),
                            ('3787 344936 71000', {'amex': {'3787 34**** *1000'}}),
                            ('345606077182423', {}),
                            ('3579964259818823', {'jcb': {'357996******8823'}}),
                            ('3559390822709303', {'jcb': {'355939******9303'}}),
                            ('3578488152861707', {'jcb': {'357848******1707'}}),
                          ]
                  )
def testIsValidPan(data, expected_result):
    result = pan.findMatch(data)
    assert result == expected_result