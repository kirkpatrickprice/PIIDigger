import pytest
from queue import Queue

from piidigger.getencoding import getEncoding
from piidigger.logmanager import LogManager

@pytest.mark.filehandlers
@pytest.mark.parametrize('testFile, expected_result', [
                                ('testdata/pan/sample-pans.json', 'ascii'),
                                ('testdata/binary-json.json', None),
                            ]
                        )
def test_getEncoding(testFile, expected_result):
    logManager=LogManager(logFile='test.log', logLevel='INFO', logQueue=Queue())
    result=getEncoding(testFile, logManager)

    assert result==expected_result