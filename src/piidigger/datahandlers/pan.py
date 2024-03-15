import re

dhName='Primary Account Number'

def findMatch(line: str) -> dict:
    '''
    Matches a line of text against known credit card number formats.  Should receive the text from "filehandler" as raw text.

    Returns a dictionary of:
        'brand': set(matches)
    '''

    # Regexes provided by https://github.com/citypay/citypay-pan-search/tree/master/src/test/resources
    
    # If you know of more, or can shed light on any corrections, please submit an issue at https://github.com/kirkpatrickprice/PIIDigger/issues or submit a PR on the repo
    # Added the |[^-] to exclude strings anchored on a hyphen.  Hopefully reduce UUID false positives in log files without missing legit PAN.
    regexes={
        'visa': re.compile(r'(?:^|[^\d.-])4[0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])'),
        'mc': re.compile(r'(?:^|[^\d.-])5[1-5][0-9]{2}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])'),
        'discover': re.compile(r'(?:^|[^\d.-])6011[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}(?:$|[^\d.-])'),
        'jcb': re.compile(r'(?:^|[^\d.-])(?:2131|1800|35[0-9]{3})[0-9]{11}(?:$|[^\d.-])'),
        'amex': re.compile(r'(?:^|[^\d.-])3[47][0-9]{2}[ -]?[0-9]{6}[ -]?[0-9]{5}(?:$|[^\d.-])'),
        }
    
    results=dict()
    for brand in regexes.keys():
        matches=regexes[brand].findall(line)
        if matches:
            for match in matches:
                match=match.strip()
                if _isValid(match):
                    results[brand]=set()
                    try:
                        results[brand].add(_redact(match))
                    except KeyError:
                        results[brand]=[]
                        results[brand]+=[_redact(match)]
    return results


def _isValid(text: str) -> bool:

    def luhn(n) -> bool:
        # Luhn check taken from https://rosettacode.org/wiki/Luhn_test_of_credit_card_numbers#Python

        # Invert the number string for the Luhn formula
        r = [int(ch) for ch in str(n)][::-1]

        # Double every other digit and add the others.  Modulo 10 on the result and it should equal 0
        return (sum(r[0::2]) + sum(sum(divmod(d*2,10)) for d in r[1::2])) % 10 == 0

    # Eliminate any non-numeric characters (such as hyphen and space) from the text
    if not text.isdigit():
        text = ''.join(i for i in text if i.isdigit())
    
    return luhn(text)

def _redact(text: str, replaceWith: str = '*') -> str:
    '''
    Redacts PAN to limit to just the first six and last four digits
    '''
    needsRejoined=False
    if not text.isdigit():
        # Identify the separators and their positions
        seps=dict()
        pos=0
        for c in text:
            if not c.isdigit():
                seps.update({pos: c})
                needsRejoined=True
            pos+=1

        # Rewrite the string to only include digits and then process 
        text=''.join([c for c in text if c.isdigit()])
    
    lastFourPos=len(text)-4
    firstSix=text[:6]
    middle=replaceWith*(lastFourPos-6)
    lastFour=text[lastFourPos:]
    result=firstSix+middle+lastFour
    
    if needsRejoined:
        for pos in seps.keys():
            result=result[:pos] + seps[pos] + result[pos:]

    return result
    

if __name__ == '__main__':
    testPANs=[
        '4012001037140001514E100010003220121800000011150',
        'but not this4893 0133 3538 6137or this',
        '3782-822463-10005',
        '371449635398431\nbutnot this',
        '3787 344936 71000',
        '345606077182423',
    ]

    results=[]

    for test in testPANs:
        match=findMatch(test)
        if match:
            results+=[match]

    print(results)