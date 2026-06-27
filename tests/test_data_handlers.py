import pytest

from piidigger.datahandlers import HANDLER_REGISTRY, email, pan
from piidigger.protocols import DataHandler


@pytest.mark.datahandlers
class TestPanHandler:
    def test_satisfies_protocol(self):
        assert isinstance(pan.handler, DataHandler)

    def test_name(self):
        assert pan.handler.name == 'pan'

    def test_find_matches_returns_correct_type(self):
        result = pan.handler.find_matches('4893 0133 3538 6137')
        assert isinstance(result, dict)
        for key, val in result.items():
            assert isinstance(key, str)
            assert isinstance(val, set)
            for item in val:
                assert isinstance(item, str)

    def test_find_matches_known_visa(self):
        result = pan.handler.find_matches('4893 0133 3538 6137')
        assert 'visa' in result
        assert '4893 01** **** 6137' in result['visa']

    def test_find_matches_no_match(self):
        result = pan.handler.find_matches('no card here')
        assert result == {}


@pytest.mark.datahandlers
class TestEmailHandler:
    def test_satisfies_protocol(self):
        assert isinstance(email.handler, DataHandler)

    def test_name(self):
        assert email.handler.name == 'email'

    def test_find_matches_returns_correct_type(self):
        result = email.handler.find_matches('user@example.com')
        assert isinstance(result, dict)
        for key, val in result.items():
            assert isinstance(key, str)
            assert isinstance(val, set)
            for item in val:
                assert isinstance(item, str)

    def test_find_matches_known_email(self):
        result = email.handler.find_matches('user@example.com')
        assert 'email' in result
        assert 'u***@example.com' in result['email']

    def test_find_matches_no_match(self):
        result = email.handler.find_matches('no email here')
        assert result == {}


@pytest.mark.datahandlers
class TestHandlerRegistry:
    def test_pan_in_registry(self):
        assert 'pan' in HANDLER_REGISTRY

    def test_email_in_registry(self):
        assert 'email' in HANDLER_REGISTRY

    def test_all_registry_values_satisfy_protocol(self):
        for name, h in HANDLER_REGISTRY.items():
            assert isinstance(h, DataHandler), f"{name} does not satisfy DataHandler protocol"
            assert h.name == name
