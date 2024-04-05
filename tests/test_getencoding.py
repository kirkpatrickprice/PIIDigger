import pytest

from piidigger.getencoding import getEncoding

@pytest.mark.filehandlers
@pytest.mark.parametrize('testFile, expected_result', [
                                ('testdata/pan/sample-pans.json', 'ascii'),
                                ('testdata/binary-json.json', None),
                            ]
                        )
def test_getEncoding(testFile, expected_result):
    result=getEncoding(testFile)

    assert result==expected_result