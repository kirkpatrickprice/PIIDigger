import pytest

from piidigger.datahandlers import email

@pytest.mark.datahandlers
@pytest.mark.parametrize('data, expected_result', [
    # Standard email addresses
    ('user@example.com', {'email': {'u***@example.com'}}),
    ('very.long.email.address@company-name.com', {'email': {'ver*******************s@company-name.com'}}),
    ('short@test.org', {'email': {'s****@test.org'}}),
    ('a@b.co', {'email': {'*@b.co'}}),
    ('first.last@subdomain.example.co.uk', {'email': {'fir******t@subdomain.example.co.uk'}}),
    ('email_with+symbol@domain.com', {'email': {'ema*************l@domain.com'}}),
    
    # Emails embedded in text
    ('Please contact support@example.com for assistance.', {'email': {'s*****t@example.com'}}),
    ('My email is john.doe1234@company-name.co.uk and I need help.', {'email': {'joh********4@company-name.co.uk'}}),
    ('Send your resume to hr@bigcorp.org or careers@bigcorp.org by Friday.', 
     {'email': {'h*@bigcorp.org', 'c*****s@bigcorp.org'}}),
    
    # Edge cases
    ('Short email: a@b.io needs special handling.', {'email': {'*@b.io'}}),
    ('sam@small.co has fewer than 5 chars in local part', {'email': {'s**@small.co'}}),
    ('quoted"email"@example.com is valid per RFC5322', {'email': {'"*****"@example.com'}}),
    
    # Non-matching cases
    ('This text contains no email addresses at all.', {}),
    ('Invalid emails: a@, @example.com, plaintext', {}),
    ('invalid local and domain: &ap123456789012345678f@0.33', {}),  # Invalid email format
    ('invalid domain: ron@0.67', {}),                                # Invalid domain format
    ('4012001037140001514E100010003220121800000011150', {}),        # Not an email
])
def test_email_match_and_redaction(data, expected_result):
    """Test email detection and redaction rules"""
    result = email.findMatch(data)
    assert result == expected_result

class TestEmailFunctions:
    def test_is_valid(self):
        """Test email validation function"""
        # Valid emails
        assert email._isValid('user@example.com') is True
        assert email._isValid('a@b.co') is True
        assert email._isValid('complex+email.address123@sub-domain.example.co.uk') is True
        
        # Invalid emails
        assert email._isValid('not_an_email') is False
        assert email._isValid('@missing_local_part.com') is False
        assert email._isValid('missing_domain@') is False
        assert email._isValid('multiple@@at.symbols') is False
        assert email._isValid('user@example@example.com') is False  # Invalid due to multiple @ symbols
        assert email._isValid('') is False
        assert email._isValid('invalid local and domain: &ap123456789012345678f@0.33') is False
        assert email._isValid('invalid domain: ron@0.67') is False
        assert email._isValid('user@thisisasuperlongdomainlabelofexactlysixtysevencharactersinlengthxxx.com') is False  # Invalid domain label length
        assert email._isValid('user@invalid.0tld') is False  # Invalid TLD
        assert email._isValid('user@example.thisisaverylongdomainnamethatexceedsthemaximumallowedlengthforadomainpartof253charactersandshouldbeusedtotestemailvalidationroutines.com') is False  # Domain part too long
    

    def test_redact_rule1(self):
        """Test redaction rule 1: 10+ characters before @ - retain first 3 and last 1"""
        test_emails = {
            'johndoe1234@example.com': 'joh*******4@example.com',
            'very.long.email@domain.com': 'ver***********l@domain.com',
            'longemailaddress@test.org': 'lon************s@test.org'
        }
        
        for original, expected in test_emails.items():
            assert email._redact(original) == expected

    def test_redact_rule2(self):
        """Test redaction rule 2: <10 characters before @ - retain first and last"""
        test_emails = {
            'midlen@no.such.domain.co.uk': 'm****n@no.such.domain.co.uk',
            'johndoe@example.com': 'j*****e@example.com',
        }
        
        for original, expected in test_emails.items():
            assert email._redact(original) == expected

    def test_redact_rule3(self):
        """Test redaction Rule 3: If <=5 chars, keep only the first character in the local part"""
        test_emails = {
            'short@test.org': 's****@test.org',
            'hello@domain.com': 'h****@domain.com',
            'user@example.com': 'u***@example.com',
            'sam@small.co': 's**@small.co',
            'john@doe.com': 'j***@doe.com',
        }
        
        for original, expected in test_emails.items():
            assert email._redact(original) == expected

    def test_redact_rule4(self):
        """Test redaction Rule 4: If exactly one character, redact it"""
        test_emails = {
            'a@b.co': '*@b.co'
        }
        
        for original, expected in test_emails.items():
            assert email._redact(original) == expected

    def test_find_match_with_multiple_emails(self):
        """Test finding multiple email addresses in a single line"""
        text = "Contact us at info@company.com, support@company.com or ourlongsupportemail@company.com for help"
        result = email.findMatch(text)
        assert 'email' in result
        assert len(result['email']) == 3
        assert 'i***@company.com' in result['email']
        assert 's*****t@company.com' in result['email']
        assert 'our***************l@company.com' in result['email']

    def test_empty_and_invalid_inputs(self):
        """Test handling of empty or invalid inputs"""
        assert email.findMatch('') == {}
        assert email.findMatch('No email addresses here') == {}
        assert email.findMatch('Invalid: user@') == {}
        assert email.findMatch('Invalid: @example.com') == {}

if __name__ == "__main__":
    # This allows running the tests directly with python
    pytest.main(["-v", __file__])