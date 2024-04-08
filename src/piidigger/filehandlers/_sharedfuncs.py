######################################################
############ Shared File Handler Functions ###########
######################################################


def appendContent(content: str, line: str, maxContentSize: int):
    '''Appends a line of text to a content string, returning the updated content string'''

    line = replaceChars(line)
    contentWords=content.split()
    lineWords = line.split()

    while len(' '.join(contentWords)) < maxContentSize:
        try:
            word=lineWords.pop(0)
            contentWords.append(word)
        except IndexError:
            break
        
    return ' '.join(contentWords), ' '.join(lineWords)    


def replaceChars(content: str) -> str:
    '''Replaces characters in a string to make the it simpler for data handler regexes.  
    Replaces newlines, carriage returns, and tabs with spaces.  Strips the string of leading and trailing whitespace.'''
    
    return content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').strip()