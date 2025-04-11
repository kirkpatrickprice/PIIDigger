import re

dhName='Primary Account Number'

import re

dhName='Email Address'

def findMatch(line: str) -> dict:
    '''
    Matches a line of text against email address formats consistent with RFC5322.
    Should receive the text from "filehandler" as raw text.

    Returns a dictionary of:
        'brand': set(matches)
    '''
    
    # RFC5322 compliant email regex
    regex=re.compile(r'(?:[a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?|\[(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?|[a-zA-Z0-9-]*[a-zA-Z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])')
    
    results=dict()
    matches=regex.findall(line)
    if matches:
        for match in matches:
            match=match.strip()
            if _isValid(match):
                try:
                    results['email'].add(_redact(match))
                except KeyError:
                    results['email'] = set()
                    results['email'].add(_redact(match))
    return results

def _isValid(text: str) -> bool:
    """
    Validate if the text is a valid email address
    Basic validation - a more thorough validation was done in the regex
    """
    # Simple check if there is an @ symbol with text before and after it
    return '@' in text and text.count('@') == 1 and len(text.split('@')[0]) > 0 and len(text.split('@')[1]) > 0

def _redact(text: str, replaceWith: str = '*') -> str:
    '''
    Redacts email address according to this rule:
    Rule 1: If 10+ chars before @, retain first 3 and last 1
    Rule 2: If <10 chars, retain first and last char
    Rule 3: If <=5 chars, keep only the first character in the local part
    Rule 4: If exactly one character, redact it
    Rule 5: Leave domain portion unchanged
    '''
    if '@' not in text:
        return text  # Not an email address
    
    local_part, domain = text.split('@')
    
    # Apply redaction rules to local part
    local_len = len(local_part)
    
    if local_len == 1:
        # Rule 4: If exactly one character, redact it
        return f'{replaceWith}@{domain}'
    if local_len <= 5:
        # Rule 3: Keep only the first character in the local part
        first = local_part[0]
        redacted_local = f'{first}{replaceWith * (local_len - 1)}'
    elif local_len < 10:
        # Rule 2: Retain only first and last char
        first = local_part[0]
        last = local_part[-1]
        redacted_local = f'{first}{replaceWith * (local_len - 2)}{last}'
    else:
        # Rule 1: Retain first 3 and last 1
        first_three = local_part[:3]
        last_one = local_part[-1]
        redacted_local = f'{first_three}{replaceWith * (local_len - 4)}{last_one}'
    
    # Rule 5: Leave domain unchanged
    return f'{redacted_local}@{domain}'
    

if __name__ == '__main__':
    test_texts = [
        'user@example.com',
        'very.long.email.address@company-name.com',
        'short@test.org',
        'a@b.co',
        'first.last@subdomain.example.co.uk',
        'email_with+symbol@domain.com'
        'Please contact support@example.com for assistance.',
        'My email is john.doe1234@company-name.co.uk and I need help.',
        'Send your resume to hr@bigcorp.org or careers@bigcorp.org by Friday.',
        'This text contains no email addresses at all.',
        'Short email: a@b.io needs special handling.',
        'Contact info: very.long.email.address123@subdomain.example.com',
        'Invalid emails: a@, @example.com, plaintext',
        'Mixed text with user@example.com and other content.',
        'Multiple emails in one line: first@example.com and second@test.org',
        'Email with special chars: first.last+filter@mail-server.com',
        '4012001037140001514E100010003220121800000011150',  # Not an email
        'sam@small.co has fewer than 5 chars in local part',
        'quoted"email"@example.com is valid per RFC5322'
    ]

    results = []

    for test in test_texts:
        match = findMatch(test)
        if match:
            results.append((test, match))

    # Display results in a readable format
    for original, matches in results:
        print(f"Original: {original}")
        print(f"Matches: {matches}")
        print("-" * 50)