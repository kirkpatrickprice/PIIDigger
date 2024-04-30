from queue import Queue

import pytest

from piidigger.filehandlers import plaintext
from piidigger.globalfuncs import clearQ

@pytest.mark.filehandlers
@pytest.mark.parametrize('filename, expected_result', [
                            ('testdata/plaintext/does-not-exist.txt', []),
                            ('testdata/plaintext/empty-file-utf16le-crlf.txt', [('', 0)]),
                            ('testdata/plaintext/lorem-ipsum-1line-utf8-crlf.txt', 
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.', 123),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf16le-crlf.txt', 
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.', 123),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-lf.txt', 
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.', 123),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-1line-with-blank-ending-line-utf8-crlf.txt',
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.', 123),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-2line-utf8-crlf-649-bytes.txt',
                                [
                                    ('magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuada', 649),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-2line-utf8-crlf-650-bytes.txt',
                                [
                                    ('magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquaam malesuadaa', 651),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-2line-utf8-crlf-651-bytes.txt',
                                [
                                    ('magnas fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuadaa', 651),
                                ]),
                            ('testdata/plaintext/lorem-ipsum-2line-utf8-crlf-1000-bytes.txt', 
                                [
                                    ('magna fringilla urna porttitor rhoncus dolor purus non enim praesent elementum facilisis leo vel fringilla est ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis scelerisque fermentum dui faucibus in ornare quam viverra orci sagittis eu volutpat odio facilisis mauris sit amet massa vitae tortor condimentum lacinia quis vel eros donec ac odio tempor orci dapibus ultrices in iaculis nunc sed augue lacus viverra vitae congue eu consequat ac felis donec et odio pellentesque diam volutpat commodo sed egestas egestas fringilla phasellus faucibus scelerisque eleifend donec pretium vulputate sapien nec sagittis aliquam malesuada bibendum arcu vitae elementum curabitur vitae nunc sed velit dignissim sodales ut eu sem integer vitae justo eget magna fermentum iaculis eu non diam phasellus vestibulum lorem sed risus ultricies tristique nulla aliquet enim tortor at auctor urna nunc id cursus metus aliquam eleifend mi in nulla posuere sollicitudin aliquam ultrices sagittis orci', 999)
                                ]),
                            ('testdata/plaintext/lorem-ipsum-2paragraph-utf8-crlf.txt',
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Fusce id velit ut tortor pretium viverra suspendisse potenti nullam. Eu sem integer vitae justo eget magna. Orci a scelerisque purus semper eget duis at tellus at. Tempor orci eu lobortis elementum nibh tellus molestie nunc. Aliquet lectus proin nibh nisl condimentum id. Eu tincidunt tortor aliquam nulla facilisi cras fermentum odio eu. Diam sollicitudin tempor id eu nisl nunc. Venenatis a condimentum vitae sapien pellentesque habitant morbi tristique senectus. Nunc faucibus a pellentesque sit amet porttitor eget. Dictum varius duis at consectetur lorem donec massa sapien faucibus. Amet venenatis urna cursus eget nunc scelerisque. Sit amet porttitor eget dolor morbi non arcu risus. Donec ultrices tincidunt arcu non sodales neque sodales. Tincidunt dui ut ornare lectus sit amet est placerat in. Cras pulvinar mattis nunc sed blandit libero volutpat. Sed cras ornare arcu dui vivamus arcu felis bibendum. Elementum facilisis leo vel fringilla est. Morbi enim nunc faucibus a pellentesque sit. Ipsum suspendisse ultrices gravida dictum. Urna nunc id cursus metus aliquam eleifend mi in. Amet consectetur adipiscing elit pellentesque. Dignissim cras tincidunt lobortis', 1299),
                                    ('feugiat vivamus at augue eget. Tristique et egestas quis ipsum suspendisse ultrices gravida dictum. Eu augue ut lectus arcu bibendum at varius vel. Eros donec ac odio tempor orci dapibus ultrices. Fermentum et sollicitudin ac orci phasellus. Magnis dis parturient montes nascetur ridiculus mus mauris. Integer quis auctor elit sed vulputate. Iaculis at erat pellentesque adipiscing. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Fusce id velit ut tortor pretium viverra suspendisse potenti nullam. Eu sem integer vitae justo eget magna. Orci a scelerisque purus semper eget duis at tellus at. Tempor orci eu lobortis elementum nibh tellus molestie nunc. Aliquet lectus proin nibh nisl condimentum id. Eu tincidunt tortor aliquam nulla facilisi cras fermentum odio eu. Diam sollicitudin tempor id eu nisl nunc. Venenatis a condimentum vitae sapien pellentesque habitant morbi tristique senectus. Nunc faucibus a pellentesque sit amet porttitor eget. Dictum variusduis at consectetur lorem donec massa sapien faucibus. Amet venenatis urna cursus eget nunc scelerisque. Sit amet porttitor eget dolor morbi non arcu risus. Donec ultrices tincidunt arcu non sodales neque sodales. Tincidunt dui ut ornare lectus sit amet est placerat', 1306),
                                    ('in. Cras pulvinar mattis nunc sed blandit libero volutpat. Sed cras ornare arcu dui vivamus arcu felis bibendum. Elementum facilisis leo vel fringilla est. Morbi enim nunc faucibus a pellentesque sit. Ipsum suspendisse ultrices gravida dictum. Urna nunc id cursus metus aliquam eleifend mi in. Amet consectetur adipiscing elit pellentesque. Dignissim cras tincidunt lobortis feugiat vivamusat augue eget. Tristique et egestas quis ipsum suspendisse ultrices gravida dictum. Eu augue ut lectus arcu bibendum at varius vel. Eros donec ac odio tempor orci dapibus ultrices. Fermentum et sollicitudin ac orci phasellus. Magnis dis parturient montes nascetur ridiculus mus mauris. Integer quis auctor elit sed vulputate. Iaculis at erat pellentesque adipiscing.', 756)
                                ]),
                            ('testdata/plaintext/mislabeld-text-file.txt', []),
                            ('testdata/plaintext/random-data-700-bytes.txt', 
                                [
                                    ('YygE2ENjzFKuEnSjYDQDv6wFPRMbZp8pAd1t3UcGTZxgSq7k7XftmmbbTjcuP0yQLSYkND7VdDJhwqxJES7zRBcLMcDmxBbk1PXuPh3im5hXTB42pPeepAxY3UHTHM56Kjyrz2yYAESWStTHzSr65krBeGTXZNvipfP7PJAMPqpvchjebSta71Rp8ybMKk8idgiHQNWgmMfCRfR61uGx3arFKWeC0xRctv8WdieqPfe7uzE3afprVfTL5E3di8wCkngdPuwnnfPeEBiAbp5RDteqT1Sy5pVWxj0iT9F1qyifEWXbwnvkmcC1D64LBzACXQ5NdhypbdUkr7utz0EupA9FvRNWdSLyeMeychwBN2FWnm0E3XtU2F76RXapcTfz5Y010vfEz8v5EUSbQhxPV4JhpTpeKzYV6a5BARB3AKZ6ChivTmkh8RcMPHpgZhTqex46C8XGZTgZ8zm8QK4mFEbPHY0Qij7BBT4kK1PhxFEKHAdGRqkxwV3Dn186SrpmxrqvBm9wXJh47EKP7BVLjHMZKVMj2n8WZC1x8HcNc1tai2fBC5bMutAR3Cp31WYAr68jui15DUqr949ZLz1amd317ZgBHeaQkZZKceUnV83tpyYtgzjEDN6SxNkx3qGkNnua82YAKHun3N8JDWGPV4mEjzhuHS5Z5KPnQD3K41Yt2zUJwy7vjRZfm0h0', 700)
                                ]),
                            ('testdata/plaintext/unknown-encoding.txt', []),
                            ('testdata/plaintext/zero-byte-file.txt', []),
                          ]
                  )
def test_read_plaintext_file(filename, expected_result):
    # Cut back from the default of 100,000 to make testing a bit easier on super-large files.  This is based on the premise that if it can handle 2 chunks (1300 bytes), it can handle 100,000 (61MB).
    maxChunkCount=2
    logQ = Queue()
    logConfig = {
        'q': logQ,
        'level': "DEBUG",
    }

    result: list = []

    for content in plaintext.readFile(filename, logConfig, maxChunkCount):
        result.append((content, len(content)))

    print(f'Result: "{result}"')
    
    clearQ(logQ)

    assert result == expected_result

    