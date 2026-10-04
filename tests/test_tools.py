import pytest

from makan.tools import int_arg, number_arg, text_arg


def test_number_arg_accepts_ints_and_floats() -> None:
    assert number_arg({"x": 3}, "x") == 3.0
    assert number_arg({"x": 3.5}, "x") == 3.5


@pytest.mark.parametrize("value", ["3", None, True, [1]])
def test_number_arg_rejects_everything_else(value: object) -> None:
    with pytest.raises(ValueError, match="x must be a number"):
        number_arg({"x": value}, "x")


def test_int_arg_defaults_and_accepts_whole_numbers() -> None:
    assert int_arg({}, "n", 7) == 7
    assert int_arg({"n": None}, "n", 7) == 7
    assert int_arg({"n": 3}, "n", 7) == 3
    assert int_arg({"n": 3.0}, "n", 7) == 3


@pytest.mark.parametrize("value", [3.5, "3", True])
def test_int_arg_rejects_everything_else(value: object) -> None:
    with pytest.raises(ValueError, match="n must be a whole number"):
        int_arg({"n": value}, "n", 7)


def test_text_arg_trims_and_treats_blank_as_missing() -> None:
    assert text_arg({"t": "  thai "}, "t") == "thai"
    assert text_arg({"t": "   "}, "t") is None
    assert text_arg({}, "t") is None
    with pytest.raises(ValueError, match="t must be a string"):
        text_arg({"t": 3}, "t")
