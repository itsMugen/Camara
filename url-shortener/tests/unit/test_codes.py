import pytest

from url_shortener.codes import (
    ALPHABET,
    MAX_CODE_LENGTH,
    MIN_CODE_LENGTH,
    generate_code,
    is_valid_code,
)


def test_alphabet_is_base62_without_duplicates():
    assert len(ALPHABET) == 62
    assert len(set(ALPHABET)) == 62


@pytest.mark.parametrize("length", [MIN_CODE_LENGTH, 7, MAX_CODE_LENGTH])
def test_generated_code_has_requested_length_and_valid_characters(length):
    code = generate_code(length)
    assert len(code) == length
    assert set(code) <= set(ALPHABET)
    assert is_valid_code(code)


def test_codes_are_not_repeated_in_practice():
    codes = {generate_code(7) for _ in range(10_000)}
    assert len(codes) == 10_000


def test_every_character_can_be_produced():
    seen = set("".join(generate_code(MAX_CODE_LENGTH) for _ in range(200)))
    assert seen == set(ALPHABET)


@pytest.mark.parametrize(
    "code",
    ["", "abc", "a" * (MAX_CODE_LENGTH + 1), "abc-def", "abc def", "abc/de", "ñandú12", "abcd%20"],
)
def test_is_valid_code_rejects(code):
    assert not is_valid_code(code)
