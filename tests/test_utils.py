"""
Tests for utility modules.

This module tests the singleton metaclass, validation utilities,
HTTP utilities, and other helper functions.
"""

import threading
from unittest.mock import MagicMock

from app.utils.http_utils import (
    build_auth_headers,
    build_headers,
    get_client_headers,
)
from app.utils.singleton import SingletonMeta
from app.utils.validation import (
    sanitize_string,
    validate_float,
    validate_integer,
    validate_name,
    validate_path,
    validate_url,
    validate_uuid,
)

# =============================================================================
# SingletonMeta Tests
# =============================================================================


class TestSingletonMeta:
    """Tests for the SingletonMeta metaclass."""

    def test_singleton_returns_same_instance(self):
        """Test that singleton always returns the same instance."""

        class TestClass(metaclass=SingletonMeta):
            def __init__(self, value: int = 0):
                self.value = value

        instance1 = TestClass(1)
        instance2 = TestClass(2)

        assert instance1 is instance2
        assert instance1.value == 1  # First initialization value persists

    def test_singleton_initialization_only_once(self):
        """Test that __init__ is only called once."""

        class Counter(metaclass=SingletonMeta):
            call_count = 0

            def __init__(self):
                Counter.call_count += 1

        Counter()
        Counter()
        Counter()

        assert Counter.call_count == 1

    def test_singleton_reset_instance(self):
        """Test that reset_instance allows new initialization."""

        class Resettable(metaclass=SingletonMeta):
            def __init__(self, value: int = 0):
                self.value = value

        instance1 = Resettable(10)
        assert instance1.value == 10

        Resettable.reset_instance()

        instance2 = Resettable(20)
        assert instance2.value == 20
        assert instance1 is not instance2

    def test_singleton_is_initialized(self):
        """Test is_initialized method."""

        class Initializable(metaclass=SingletonMeta):
            pass

        assert Initializable.is_initialized() is False

        Initializable()

        assert Initializable.is_initialized() is True

    def test_singleton_instance_property(self):
        """Test instance property returns correct value."""

        class Accessible(metaclass=SingletonMeta):
            def __init__(self):
                self.data = "test"

        assert Accessible.instance is None

        obj = Accessible()

        assert Accessible.instance is obj
        assert Accessible.instance.data == "test"

    def test_singleton_thread_safety(self):
        """Test that singleton is thread-safe."""

        class ThreadSafe(metaclass=SingletonMeta):
            def __init__(self):
                self.value = 0

        instances = []
        errors = []

        def create_instance():
            try:
                instance = ThreadSafe()
                instances.append(instance)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_instance) for _ in range(100)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(instances) == 100
        # All instances should be the same object
        assert all(inst is instances[0] for inst in instances)

    def test_different_singleton_classes_are_independent(self):
        """Test that different singleton classes don't share instances."""

        class ClassA(metaclass=SingletonMeta):
            def __init__(self):
                self.name = "A"

        class ClassB(metaclass=SingletonMeta):
            def __init__(self):
                self.name = "B"

        a = ClassA()
        b = ClassB()

        assert a is not b
        assert a.name == "A"
        assert b.name == "B"


# =============================================================================
# Validation Tests
# =============================================================================


class TestValidateName:
    """Tests for validate_name function."""

    def test_valid_name(self):
        """Test validation of valid names."""
        valid_names = [
            "test",
            "test_name",
            "test-name",
            "test.name",
            "TestName123",
            "a",
            "sensor1",
        ]

        for name in valid_names:
            is_valid, error = validate_name(name)
            assert is_valid is True, f"'{name}' should be valid"
            assert error is None

    def test_empty_name(self):
        """Test validation of empty name."""
        is_valid, error = validate_name("")
        assert is_valid is False
        assert "cannot be empty" in error

    def test_name_too_long(self):
        """Test validation of name exceeding max length."""
        long_name = "a" * 256
        is_valid, error = validate_name(long_name)
        assert is_valid is False
        assert "exceeds maximum length" in error

    def test_custom_max_length(self):
        """Test validation with custom max length."""
        name = "a" * 20
        is_valid, error = validate_name(name, max_length=10)
        assert is_valid is False
        assert "exceeds maximum length" in error

    def test_invalid_characters(self):
        """Test validation of names with invalid characters."""
        invalid_names = [
            "name with space",
            "name@special",
            "name#hash",
            "name$dollar",
            "name;semicolon",
            "<script>",
        ]

        for name in invalid_names:
            is_valid, error = validate_name(name)
            assert is_valid is False, f"'{name}' should be invalid"
            assert "invalid characters" in error


class TestValidatePath:
    """Tests for validate_path function."""

    def test_valid_path(self):
        """Test validation of valid paths."""
        valid_paths = [
            "path/to/file",
            "path/to/file.txt",
            "path-name/file_name.ext",
            "/absolute/path",
            "relative/path",
        ]

        for path in valid_paths:
            is_valid, error = validate_path(path)
            assert is_valid is True, f"'{path}' should be valid"
            assert error is None

    def test_empty_path(self):
        """Test validation of empty path."""
        is_valid, error = validate_path("")
        assert is_valid is False
        assert "cannot be empty" in error

    def test_path_traversal(self):
        """Test validation detects path traversal."""
        is_valid, error = validate_path("../parent/file")
        assert is_valid is False
        assert "traversal" in error.lower()

    def test_path_too_long(self):
        """Test validation of path exceeding max length."""
        long_path = "a/" * 600
        is_valid, error = validate_path(long_path)
        assert is_valid is False
        assert "exceeds maximum length" in error

    def test_invalid_path_characters(self):
        """Test validation of paths with invalid characters."""
        invalid_paths = [
            "path with space/file",
            "path;semicolon",
            "path@at",
        ]

        for path in invalid_paths:
            is_valid, error = validate_path(path)
            assert is_valid is False, f"'{path}' should be invalid"


class TestValidateUuid:
    """Tests for validate_uuid function."""

    def test_valid_uuid(self):
        """Test validation of valid UUIDs."""
        valid_uuids = [
            "550e8400-e29b-41d4-a716-446655440000",
            "123e4567-e89b-12d3-a456-426614174000",
            "ABCDEF12-3456-7890-ABCD-EF1234567890",
        ]

        for uuid_str in valid_uuids:
            is_valid, error = validate_uuid(uuid_str)
            assert is_valid is True, f"'{uuid_str}' should be valid"
            assert error is None

    def test_empty_uuid(self):
        """Test validation of empty UUID."""
        is_valid, error = validate_uuid("")
        assert is_valid is False
        assert "cannot be empty" in error

    def test_invalid_uuid(self):
        """Test validation of invalid UUIDs."""
        invalid_uuids = [
            "not-a-uuid",
            "12345",
            "550e8400-e29b-41d4-a716",  # Too short
            "550e8400-e29b-41d4-a716-446655440000-extra",  # Too long
        ]

        for uuid_str in invalid_uuids:
            is_valid, error = validate_uuid(uuid_str)
            assert is_valid is False, f"'{uuid_str}' should be invalid"
            assert "Invalid UUID" in error


class TestValidateInteger:
    """Tests for validate_integer function."""

    def test_valid_integer(self):
        """Test validation of valid integers."""
        is_valid, error, value = validate_integer(42)
        assert is_valid is True
        assert error is None
        assert value == 42

    def test_string_integer(self):
        """Test validation of string integer."""
        is_valid, error, value = validate_integer("123")
        assert is_valid is True
        assert error is None
        assert value == 123

    def test_invalid_integer(self):
        """Test validation of non-integer values."""
        invalid_values = ["abc", "12.5", None, [], {}]

        for val in invalid_values:
            is_valid, error, value = validate_integer(val)
            assert is_valid is False
            assert "must be an integer" in error
            assert value is None

    def test_min_value(self):
        """Test validation with minimum value."""
        is_valid, error, value = validate_integer(5, min_value=10)
        assert is_valid is False
        assert "at least 10" in error

    def test_max_value(self):
        """Test validation with maximum value."""
        is_valid, error, value = validate_integer(100, max_value=50)
        assert is_valid is False
        assert "at most 50" in error

    def test_range_valid(self):
        """Test validation within range."""
        is_valid, error, value = validate_integer(25, min_value=10, max_value=50)
        assert is_valid is True
        assert value == 25


class TestValidateFloat:
    """Tests for validate_float function."""

    def test_valid_float(self):
        """Test validation of valid floats."""
        is_valid, error, value = validate_float(3.14)
        assert is_valid is True
        assert error is None
        assert value == 3.14

    def test_integer_as_float(self):
        """Test validation of integer as float."""
        is_valid, error, value = validate_float(42)
        assert is_valid is True
        assert value == 42.0

    def test_string_float(self):
        """Test validation of string float."""
        is_valid, error, value = validate_float("2.718")
        assert is_valid is True
        assert value == 2.718

    def test_invalid_float(self):
        """Test validation of non-float values."""
        invalid_values = ["abc", None, [], {}]

        for val in invalid_values:
            is_valid, error, value = validate_float(val)
            assert is_valid is False
            assert "must be a number" in error
            assert value is None

    def test_min_max_value(self):
        """Test validation with min/max values."""
        is_valid, error, _ = validate_float(0.5, min_value=1.0)
        assert is_valid is False
        assert "at least 1.0" in error

        is_valid, error, _ = validate_float(10.5, max_value=5.0)
        assert is_valid is False
        assert "at most 5.0" in error


class TestSanitizeString:
    """Tests for sanitize_string function."""

    def test_basic_sanitization(self):
        """Test basic string sanitization."""
        result = sanitize_string("  hello world  ")
        assert result == "hello world"

    def test_empty_string(self):
        """Test sanitization of empty string."""
        result = sanitize_string("")
        assert result == ""

    def test_none_string(self):
        """Test sanitization of None-like values."""
        result = sanitize_string(None)
        assert result == ""

    def test_max_length_truncation(self):
        """Test max length truncation."""
        long_string = "a" * 2000
        result = sanitize_string(long_string, max_length=100)
        assert len(result) == 100

    def test_control_character_removal(self):
        """Test removal of control characters."""
        result = sanitize_string("hello\x00world\x01test")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_newlines_preserved(self):
        """Test that newlines and tabs are preserved."""
        result = sanitize_string("hello\nworld\ttab")
        assert "\n" in result
        assert "\t" in result


class TestValidateUrl:
    """Tests for validate_url function."""

    def test_valid_http_url(self):
        """Test validation of valid HTTP URLs."""
        valid_urls = [
            "http://example.com",
            "https://example.com",
            "http://localhost:8080",
            "https://192.168.1.1:8080/path",
            "http://sub.domain.example.com/path?query=1",
        ]

        for url in valid_urls:
            is_valid, error = validate_url(url)
            assert is_valid is True, f"'{url}' should be valid"
            assert error is None

    def test_empty_url(self):
        """Test validation of empty URL."""
        is_valid, error = validate_url("")
        assert is_valid is False
        assert "cannot be empty" in error

    def test_invalid_url(self):
        """Test validation of invalid URLs."""
        invalid_urls = [
            "not-a-url",
            "ftp://example.com",  # Only http/https allowed
            "example.com",  # Missing protocol
            "//example.com",  # Missing protocol
        ]

        for url in invalid_urls:
            is_valid, error = validate_url(url)
            assert is_valid is False, f"'{url}' should be invalid"
            assert "Invalid URL" in error


# =============================================================================
# HTTP Utils Tests
# =============================================================================


class TestBuildHeaders:
    """Tests for build_headers function."""

    def test_default_headers(self):
        """Test default headers."""
        headers = build_headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    def test_custom_content_type(self):
        """Test custom content type."""
        headers = build_headers(content_type="text/plain")
        assert headers["Content-Type"] == "text/plain"

    def test_custom_accept(self):
        """Test custom accept header."""
        headers = build_headers(accept="text/html")
        assert headers["Accept"] == "text/html"

    def test_extra_headers(self):
        """Test extra headers are added."""
        extra = {"X-Custom": "value", "X-Another": "test"}
        headers = build_headers(extra_headers=extra)
        assert headers["X-Custom"] == "value"
        assert headers["X-Another"] == "test"


class TestBuildAuthHeaders:
    """Tests for build_auth_headers function."""

    def test_with_client_id(self):
        """Test headers with client ID."""
        headers = build_auth_headers(client_id="test-client-123")
        assert headers["X-Client-ID"] == "test-client-123"

    def test_with_token(self):
        """Test headers with bearer token."""
        headers = build_auth_headers(token="secret-token")
        assert headers["Authorization"] == "Bearer secret-token"

    def test_with_both(self):
        """Test headers with both client ID and token."""
        headers = build_auth_headers(client_id="client-1", token="token-1")
        assert headers["X-Client-ID"] == "client-1"
        assert headers["Authorization"] == "Bearer token-1"

    def test_without_auth(self):
        """Test headers without authentication."""
        headers = build_auth_headers()
        assert "X-Client-ID" not in headers
        assert "Authorization" not in headers


class TestGetClientHeaders:
    """Tests for get_client_headers function."""

    def test_with_authenticated_manager(self):
        """Test headers with authenticated auth manager."""
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_client_id.return_value = "client-123"

        headers = get_client_headers(mock_auth_manager)
        assert headers["X-Client-ID"] == "client-123"

    def test_with_unauthenticated_manager(self):
        """Test headers with unauthenticated auth manager."""
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False

        headers = get_client_headers(mock_auth_manager)
        assert "X-Client-ID" not in headers

    def test_include_token(self):
        """Test headers with token included."""
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_client_id.return_value = "client-123"
        mock_auth_manager._credentials = {"token": "bearer-token"}

        headers = get_client_headers(mock_auth_manager, include_token=True)
        assert headers["Authorization"] == "Bearer bearer-token"

    def test_exclude_client_id(self):
        """Test headers with client ID excluded."""
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_client_id.return_value = "client-123"

        headers = get_client_headers(mock_auth_manager, include_client_id=False)
        assert "X-Client-ID" not in headers
