from queue import Queue

import pytest

from piidigger.filehandlers import plaintext
from piidigger.globalfuncs import clearQ

@pytest.mark.parametrize('filename, expected_result', [
                            ('testdata/plaintext/empty-file-utf16le-crlf.txt', ['']),
                            ('testdata/plaintext/lorem-ipsum-1line-utf8-crlf.txt', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.']),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf16le-crlf.txt', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.']),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-crlf.txt', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.']),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-lf.txt', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.']),
                            ('testdata/plaintext/lorem-ipsum-2paragraph-utf8-crlf.txt', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Fusce id velit ut tortor pretium viverra suspendisse potenti nullam. Eu sem integer vitae justo eget magna. Orci a scelerisque purus semper eget duis at tellus at. Tempor orci eu lobortis elementum nibh tellus molestie nunc. Aliquet lectus proin nibh nisl condimentum id. Eu tincidunt tortor aliquam nulla facilisi cras fermentum odio eu. Diam sollicitudin tempor id eu nisl nunc. Venenatis a condimentum vitae sapien pellentesque habitant morbi tristique senectus. Nunc faucibus a pellentesque sit amet porttitor eget. Dictum varius duis at consectetur lorem donec massa sapien faucibus. Amet venenatis urna cursus eget nunc scelerisque. Sit amet porttitor eget dolor morbi non arcu risus. Donec ultrices tincidunt arcu non sodales neque sodales. Tincidunt dui ut ornare lectus sit amet est placerat in. Cras pulvinar mattis nunc sed blandit libero volutpat. Sed cras ornare arcu dui vivamus arcu felis bibendum. Elementum facilisis leo vel fringilla est. Morbi enim nunc faucibus a pellentesque sit. Ipsum suspendisse ultrices gravida dictum. Urna nunc id cursus metus aliquam eleifend mi in. Amet consectetur adipiscing elit pellentesque. Dignissim cras tincidunt lobortis feugiat vivamus at augue eget. Tristique et egestas quis ipsum suspendisse ultrices gravida dictum. Eu augue ut lectus arcu bibendum at varius vel. Eros donec ac odio tempor orci dapibus ultrices. Fermentum et sollicitudin ac orci phasellus. Magnis dis parturient montes nascetur ridiculus mus mauris. Integer quis auctor elit sed vulputate. Iaculis at erat pellentesque adipiscing.']),
                            ('testdata/plaintext/unknown-encoding.txt', ['']),
                            ('testdata/plaintext/zero-byte-file.txt', ['']),
                          ]
                  )
def test_read_plaintext_file(filename, expected_result):
    logQ = Queue()
    logConfig = {
        'q': logQ,
        'level': "DEBUG",
    }

    result=plaintext.readFile(filename, logConfig)

    print(f'Result[{type(result)}[{type(result[0])}]]: "{result}"')
    
    clearQ(logQ)

    assert result == expected_result