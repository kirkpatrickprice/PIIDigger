from queue import Queue

import pytest

from piidigger.filehandlers import pdf
from piidigger.queuefuncs import clearQ
from piidigger.logmanager import LogManager

@pytest.mark.filehandlers
@pytest.mark.parametrize('filename, expected_result', [
                            ('testdata/pdf/does-not-exist.pdf', []),
                            ('testdata/pdf/empty-file.pdf', 
                                [
                                    ("Randy Bartels Microsoft® Word for Microsoft 365 D:20250327175503-07'00' D:20250327175503-07'00' Microsoft® Word for Microsoft 365", 129),
                                ]),
                            ('testdata/pdf/mislabeled-pdf-file.pdf', []),
                            ('testdata/pdf/lorem-ipsum.pdf', 
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit. Etiam tristique a odio ultrices aliquet. Quisque sit amet lectus porta, viverra nunc dignissim, semper nulla. Maecenas neque metus, tincidunt in neque posuere, feugiat cursus ex. Sed auctor eleifend vestibulum. Quisque a nulla id lectus dictum ornare. Duis ac iaculis mauris. Pellentesque dignissim in ex nec cursus. Nullam consequat ex justo, ac suscipit velit convallis ut. Donec venenatis dapibus magna in sodales. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Pellentesque metus tellus, dapibus feugiat malesuada gravida, convallis id ante. Donec in magna egestas, efficitur massa in, eleifend turpis. Nunc vel ligula risus. Proin id velit et erat suscipit elementum sit amet in sapien. Etiam vulputate, nibh at accumsan aliquam, magna orci venenatis nisl, a pellentesque lectus sem quis nisi. Proin posuere nibh sed rutrum consequat. Fusce id arcu diam. Praesent rhoncus, urna eu sagittis egestas, nisi eros imperdiet tellus, a dignissim neque dui id purus. Aenean a metus at arcu laoreet elementum ut ut diam. Nulla rutrum erat vel volutpat cursus. Phasellus blandit enim aliquam leo eleifend, ac finibus arcu consequat. Sed dictum blandit lorem, efficitur tristique lacus elementum ornare. Ut ultricies la cus sit amet urna malesuada', 1308), 
                                    ('dictum. Integer eleifend eleifend purus sed vestibulum. Donec eu elit maximus, finibus justo venenatis, malesuada orci. Aenean mattis aliquet sapien ac mollis. Vivamus elementum volutpat imperdiet. Fusce ut eros in lectus tempor ullamcorper. Nam sed bibendum nibh, ut pretium erat. Sed commodo turpis et ullamcorper ornare. Sed vehicula in massa non dapibus. Ut placerat lacus vel sem tincidunt, eu malesuada sem euismod. Class aptent taciti sociosqu ad litora torquent per conubia no stra, per inceptos himenaeos. Aenean dapibus sem nulla, at porta elit accumsan ut. Donec in consequat est. Curabitur purus lacus, tincidunt faucibus porttitor in, fringilla nec arcu. Donec vel purus urna. Phasellus pretium consectetur ipsum, et accumsan ma uris fermentum non. Quisque at vestibulum orci, gravida malesuada lectus. Nam sodales turpis at erat facilisis, laoreet scelerisque dui egestas. Etiam eleifend quam sit amet pharetra luctus. Mauris interdum urna tempor leo placerat, a viverra lorem volutpa t. Donec nec tellus condimentum, lacinia leo dictum, bibendum ipsum. Maecenas ac nisl vel nunc pretium elementum. In imperdiet magna mi, mattis pretium mi consequat in. Fusce commodo risus a elit ullamcorper pulvinar. Ut diam ex, dictum nec porta id, variu s sit amet urna. Ut lobortis vestibulum purus,', 1302), 
                                    ("porta condimentum lacus pretium vel. Suspendisse et efficitur erat, vel eleifend nulla. In accumsan velit turpis. Vestibulum et lectus in dui blandit tincidunt quis vitae lorem. Praesent porta magna vel erat pharetra volutpat. Morbi massa nisl, iaculis pellentesque massa non, volutpat finibus arcu. Duis mattis tellus at venenatis fringilla. Nullam sit amet nunc nec augue sodales sollicitudin eget quis nibh. Nulla facilisi. Maecenas nec cursus nibh. Praesent rho ncus quam ex, at dignissim sapien egestas in. Phasellus rhoncus felis a tristique maximus. Pellentesque ullamcorper est sit amet massa dictum viverra. Sed molestie eu velit a maximus. Aliquam ac odio tempus elit ullamcorper cursus. Class aptent taciti soci osqu ad litora torquent per conubia nostra, per inceptos himenaeos. Suspendisse potenti. Ut pellentesque sapien non ipsum luctus mattis. Vestibulum eu ex imperdiet, condimentum erat eget, commodo mi. Randy Bartels Microsoft® Word 2016 D:20180116095531-05'00' D:20180116095531-05'00' Microsoft® Word 2016", 1026),
                                ]),
                            ('testdata/pdf/sample-pans.pdf', 
                                [
                                    ("This file has some randomly generated PAN 4893013335386137 841 Visa 5455448149609745 422 Mastercard 344491743133122 3487 Amex 6496379518604192136 116 Discover 353070086880515405 777 JCB Randy Bartels Microsoft® Word for Microsoft 365 D:20230402142701-04'00' D:20230402142701-04'00' Microsoft® Word for Microsoft 365", 315),
                                ]),
                          ]
                  )
def test_read_pdf_file(filename, expected_result):
    # Cut back from the default of 100,000 to make testing a bit easier on super-large files.  This is based on the premise that if it can handle 2 chunks (1300 bytes), it can handle 100,000 (61MB).
    maxChunkCount=2
    logQ = Queue()
    logManager=LogManager(logFile='test.log', logLevel='INFO', logQueue=logQ)

    result: list[str] = []
    for content in pdf.readFile(filename, logManager, maxChunkCount):
        result.append((content, len(content)))

    print(f'Result: "{result}"')
    
    clearQ(logQ)

    assert result == expected_result