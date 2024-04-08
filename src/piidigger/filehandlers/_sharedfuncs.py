######################################################
############ Shared File Handler Functions ###########
######################################################


def appendContent(content: str, line: str, maxContentSize: int):
    '''Appends a line of text to a content string, returning the updated content string'''

    if (len(content.strip()) + len(line.strip())) < maxContentSize:
        if content == '':
            content = replaceChars(line)
        else:
            content += ' '+replaceChars(line)
    else:
        i: int = 0
        words: list = line.split()
        for word in line.split():
            i+=1
            if content == '':
                content = word
            else:
                content += ' ' + word
            if len(content.strip()) > maxContentSize:
                return (content.strip(), ' '.join(words[i:])) 
    
    return content.strip(), ''


def replaceChars(content: str) -> str:
    '''Replaces characters in a string to make the it simpler for data handler regexes.  
    Replaces newlines, carriage returns, and tabs with spaces.  Strips the string of leading and trailing whitespace.'''
    
    return content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ').strip()