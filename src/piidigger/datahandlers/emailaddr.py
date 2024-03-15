'''This data handler is not very well developed and in the interest of getting PIIDigger released to find PAN, it was more or less abandoned.  

The whole thing can probably be replaced with something built around this: https://stackabuse.com/validate-email-addresses-in-python-with-email-validator/

I don't recommend using it for now.'''

import re

dhName='Email Address'

def findMatch(line: str) -> dict:
    '''
    Matches a line of text against email addresses.  Should receive the text from "filehandler" as raw text.

    Returns a dictionary of:
        'type': set(matches)
    '''

    # Regexes provided by:
    #   email: https://stackabuse.com/python-validate-email-address-with-regular-expressions-regex/

    # But this one might be better? https://stackoverflow.com/questions/201323/how-can-i-validate-an-email-address-using-a-regular-expression 

    # If you know of more, or can shed light on any corrections, please submit an issue at https://github.com/kirkpatrickprice/PIIDigger/issues or submit a PR on the repo

    regexes={
        'email': re.compile(r"(([-!#-'*+/-9=?A-Z^-~]+(\.[-!#-'*+/-9=?A-Z^-~]+)*|\"([]!#-[^-~ \t]|(\\[\t -~]))+\")@([-!#-'*+/-9=?A-Z^-~]+(\.[-!#-'*+/-9=?A-Z^-~]+)*|\[[\t -Z^-~]*]))"),
        }
    
    results=dict()
    for datatype in regexes.keys():
        matches=regexes[datatype].findall(line)
        if matches:
            for match in matches:
                matchedText=match[0]
                matchedText=matchedText.strip()
                results[datatype]=set()
                redacted=_redact(matchedText, datatype)
                try:
                    results[datatype].add(redacted)
                except KeyError:
                    results[datatype]=[]
                    results[datatype]+=[_redact(redacted, datatype)]
    return results

def _redact(text: str, datatype: str, replaceWith: str = '*') -> str:
    
    def email() -> str:
        '''
        Redacts the email address
        '''

        parts=text.split('@')
        pii=parts[0]
        domain=parts[1]
        keepLength=1
        parts=list()
        parts.append(pii[0:keepLength])
        parts.append(replaceWith*(len(pii)-(keepLength*2)))
        parts.append(pii[-keepLength:])
        parts.append('@')
        parts.append(domain)
        
        return ''.join(parts)
    
    def phone_us():
        '''
        Redacts US-based phone numbers
        '''

        lastFour=replaceWith*4
        return text[:-4]+lastFour
    
    
    return locals()[datatype]()
    

if __name__ == '__main__':
    testdata=[
        'someone@example.com',
        'another.person@nowhere.co.uk',
        '+1 512 555 1234',
        '(512) 555-1234 is also a phone number',
    
    ]

    results=[]

    for test in testdata:
        match=findMatch(test)
        if match:
            results+=[match]

    print(results)