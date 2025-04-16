import os

import pytest

from piidigger.globalfuncs import writeDefaultConfig, getDefaultConfig
import tomli

@pytest.mark.utils
def test_write_default_config():
    expectedConfig=getDefaultConfig()
    testFile = 'testDefault.toml'

    result=writeDefaultConfig(testFile)

    if result=="Success":
        with open(testFile, 'rb') as f:
            savedConfig=tomli.load(f)
    else:
        savedConfig={}

    os.remove(testFile)

    assert savedConfig['dataHandlers'] == expectedConfig['dataHandlers']
    assert savedConfig['localFilesOnly'] == expectedConfig['localFilesOnly']
    assert savedConfig['results']['path'] == expectedConfig['results']['path']
    assert savedConfig['results']['csv'] == expectedConfig['results']['csv']
    assert savedConfig['results']['json'] == expectedConfig['results']['json']
    assert savedConfig['results']['text'] == expectedConfig['results']['text']
    assert savedConfig['includeFiles']['ext'] == expectedConfig['includeFiles']['ext']
    assert savedConfig['includeFiles']['mime'] == expectedConfig['includeFiles']['mime']
    assert savedConfig['includeFiles']['startDirs']['windows'] == expectedConfig['includeFiles']['startDirs']['windows']
    assert savedConfig['includeFiles']['startDirs']['darwin'] == expectedConfig['includeFiles']['startDirs']['darwin']
    assert savedConfig['includeFiles']['startDirs']['linux'] == expectedConfig['includeFiles']['startDirs']['linux']
    assert savedConfig['excludeDirs']['windows'] == expectedConfig['excludeDirs']['windows']
    assert savedConfig['excludeDirs']['darwin'] == expectedConfig['excludeDirs']['darwin']
    assert savedConfig['excludeDirs']['linux'] == expectedConfig['excludeDirs']['linux']
    assert savedConfig['logging']['logLevel'] == expectedConfig['logging']['logLevel']
    assert savedConfig['logging']['logFile'] == expectedConfig['logging']['logFile']