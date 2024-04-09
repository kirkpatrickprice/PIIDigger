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

    assert savedConfig==expectedConfig