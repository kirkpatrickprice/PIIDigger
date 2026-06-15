######################################################
############ Shared File Handler Functions ###########
######################################################

from collections import deque


class ContentHandler:
    def __init__(self,
                 max_content_size: int
                ):
        self.max_content_size = max_content_size
        self.content_buffer = deque()
        self.buffer_length = 0
        self.total_bytes = 0

    def append_content(self, line: str) -> None:
        '''Appends a line of text to the content buffer'''

        line = self.replace_chars(line)
        words = line.split()
        self.total_bytes += len(line)

        for word in words:
            self.content_buffer.append(word)
            self.buffer_length += len(word) + 1

    def content_buffer_full(self) -> bool:
        '''Returns True if the content buffer is full, False otherwise'''

        return self.buffer_length >= self.max_content_size

    def replace_chars(self, content: str) -> str:
        '''Replaces characters in a string to make it simpler for data handler regexes.
        Replaces newlines, carriage returns, and tabs with spaces.  Strips the string of leading and trailing whitespace.'''

        return content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').strip()

    def get_content(self) -> str:
        '''Returns up to max_content_size amount of data from the buffer'''

        content: list = []
        content_length: int = 0

        while self.content_buffer and content_length < self.max_content_size:
            word = self.content_buffer.popleft()
            content.append(word)
            self.buffer_length -= len(word) + 1
            content_length += len(word) + 1

        return ' '.join(content)

    def finalize_content(self) -> str:
        '''Returns the remaining content in the buffer'''

        content = ' '.join(self.content_buffer)
        self.buffer_length = 0
        self.content_buffer = deque()

        return content
