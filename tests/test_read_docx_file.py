from queue import Queue

import pytest

from piidigger.filehandlers import docx
from piidigger.globalfuncs import clearQ

@pytest.mark.filehandlers
@pytest.mark.parametrize('filename, expected_result', [
                            ('testdata/docx/empty-file.docx', []),
                            ('testdata/docx/lorem-ipsum-1line-comments.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. COMMENT RESPONSE {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '4', 'created': '2024-03-28T21:21:00Z', 'modified': '2024-03-28T21:23:00Z'}", 363)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-header-footer.docx',
                                [
                                    ("Header Text Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Footer text {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '3', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-04-03T19:21:00Z'}", 370)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-heading-toc.docx', 
                                [
                                    ("Contents Heading 1 1 Heading 1 Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '3', 'created': '2024-03-28T21:19:00Z', 'modified': '2024-03-28T21:21:00Z'}", 377),
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-hyperlink.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:25:00Z', 'modified': '2024-03-28T21:25:00Z'}", 346)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-with-footnote-endnote.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.----footnote1--------endnote1---- footnote1) FOOTNOTE endnote1) ENDNOTE {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '4', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-03-28T21:18:00Z'}", 417)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-with-table.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Cell R1C1 Cell R1C2 Cell R2C1 Cell R2C2 {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:16:00Z', 'modified': '2024-03-28T21:16:00Z'}", 386)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line-wordart.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. WORDART WORDART {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '5', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-03-29T14:31:00Z'}", 362)
                                ]),
                            ('testdata/docx/lorem-ipsum-1line.docx', 
                                [
                                    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:17:00Z', 'modified': '2024-04-01T13:28:00Z'}", 346)
                                ]),
                            ('testdata/docx/lorem-ipsum-2paragraph.docx', 
                                [
                                    ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Fusce id velit ut tortor pretium viverra suspendisse potenti nullam. Eu sem integer vitae justo eget magna. Orci a scelerisque purus semper eget duis at tellus at. Tempor orci eu lobortis elementum nibh tellus molestie nunc. Aliquet lectus proin nibh nisl condimentum id. Eu tincidunt tortor aliquam nulla facilisi cras fermentum odio eu. Diam sollicitudin tempor id eu nisl nunc. Venenatis a condimentum vitae sapien pellentesque habitant morbi tristique senectus. Nunc faucibus a pellentesque sit amet porttitor eget. Dictum varius duis at consectetur lorem donec massa sapien faucibus. Amet venenatis urna cursus eget nunc scelerisque. Sit amet porttitor eget dolor morbi non arcu risus. Donec ultrices tincidunt arcu non sodales neque sodales. Tincidunt dui ut ornare lectus sit amet est placerat in. Cras pulvinar mattis nunc sed blandit libero volutpat. Sed cras ornare arcu dui vivamus arcu felis bibendum. Elementum facilisis leo vel fringilla est. Morbi enim nunc faucibus a pellentesque sit. Ipsum suspendisse ultrices gravida dictum. Urna nunc id cursus metus aliquam eleifend mi in. Amet consectetur adipiscing elit pellentesque. Dignissim cras tincidunt lobortis', 1299),
                                    ("feugiat vivamus at augue eget. Tristique et egestas quis ipsum suspendisse ultrices gravida dictum. Eu augue ut lectus arcu bibendum at varius vel. Eros donec ac odio tempor orci dapibus ultrices. Fermentum et sollicitudin ac orci phasellus. Magnis dis parturient montes nascetur ridiculus mus mauris. Integer quis auctor elit sed vulputate. Iaculis at erat pellentesque adipiscing. {'title': None, 'subject': None, 'creator': 'Randy Bartels', 'keywords': None, 'description': None, 'lastModifiedBy': 'Randy Bartels', 'revision': '2', 'created': '2024-03-28T21:14:00Z', 'modified': '2024-03-28T21:14:00Z'}", 605)
                                ]),
                            ('testdata/docx/does-not-exist.docx', []),
                          ]
                  )
def test_read_docx_file(filename, expected_result):
    # Cut back from the default of 100,000 to make testing a bit easier on super-large files.  This is based on the premise that if it can handle 2 chunks (1300 bytes), it can handle 100,000 (61MB).
    maxChunkCount=2
    logQ = Queue()
    logConfig = {
        'q': logQ,
        'level': "DEBUG",
    }

    result: list = []

    for content in docx.readFile(filename, logConfig, maxChunkCount):
        result.append((content, len(content)))

    print(f'Result: "{result}"')
    
    clearQ(logQ)

    assert result == expected_result