import pytest
from queue import Queue
from logging import INFO

from piidigger.getencoding import getEncoding

@pytest.mark.filehandlers
@pytest.mark.parametrize('testFile, expected_result', [
                                ('testdata/pan/sample-pans.json', 'ascii'),
                                ('testdata/binary-json.json', None),
                            ]
                        )
def test_getEncoding(testFile, expected_result):
    logConfig={'q': Queue(), 'level': INFO}
    result=getEncoding(testFile, logConfig)

    assert result==expected_result