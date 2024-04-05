######################################################
############ Shared File Handler Functions ###########
######################################################


def appendContent(content: str, line: str, maxContentSize: int):
    '''Appends a line of text to a content string, returning the updated content string'''

    if (len(content.strip()) + len(line.strip())) < maxContentSize:
        if content == '':
            content = line.replace('\t', ' ').strip()
        else:
            content += ' ' + line.replace('\t', ' ').strip()
    else:
        i: int = 0
        words: list = line.split()
        for word in words:
            i+=1
            if content == '':
                content = word
            else:
                content += ' ' + word
            if len(content.strip()) > maxContentSize:
                return (content.strip(), ' '.join(words[i:])) 
    
    return content.strip(), ''