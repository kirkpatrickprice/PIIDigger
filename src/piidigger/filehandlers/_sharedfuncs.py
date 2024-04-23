######################################################
############ Shared File Handler Functions ###########
######################################################

from collections import deque

class ContentHandler:
    def __init__(self, 
                 maxContentSize: int
                ):
        self.maxContentSize = maxContentSize
        self.contentBuffer = deque()
        self.bufferLength = 0
        self.totalBytes = 0
    
    def appendContent(self, line: str) -> None:
        '''Appends a line of text to the content buffer'''

        line = self.replaceChars(line)
        words = line.split()
        self.totalBytes += len(line)

        for word in words:
            self.contentBuffer.append(word)
            self.bufferLength += len(word) + 1
    
    def contentBufferFull(self) -> bool:
        '''Returns True if the content buffer is full, False otherwise'''

        return self.bufferLength >= self.maxContentSize

    def replaceChars(self, content: str) -> str:
        '''Replaces characters in a string to make the it simpler for data handler regexes.  
        Replaces newlines, carriage returns, and tabs with spaces.  Strips the string of leading and trailing whitespace.'''
        
        return content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').strip()
    
    def getContent(self) -> str:
        '''Returns up to maxContentSize amount of data from the buffer'''

        content: list = []
        contentLength: int = 0

        while self.contentBuffer and contentLength < self.maxContentSize:
            word = self.contentBuffer.popleft()
            content.append(word)
            self.bufferLength -= len(word) + 1
            contentLength += len(word) + 1

        return ' '.join(content)
    
    def finalizeContent(self) -> str:
        '''Returns the remaining content in the buffer'''

        content = ' '.join(self.contentBuffer)
        self.bufferLength = 0
        self.contentBuffer = []
        
        return content
