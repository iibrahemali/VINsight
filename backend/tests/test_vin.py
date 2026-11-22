from vinsight import vin


def test_valid_vin_has_no_problems():
    assert vin.validate("1HGCM82633A004352") == []


def test_normalizes_spaces_dashes_and_case():
    assert vin.validate(" 1hgcm8-2633a004352 ") == []


def test_wrong_length():
    assert "17 characters" in vin.validate("1HGCM82633A00435")[0]


def test_illegal_letters():
    assert "invalid characters: O" in vin.validate("1HGCM82633A00435O")[0]


def test_check_digit_mismatch():
    problems = vin.validate("1HGCM82633A004353")
    assert problems and problems[0].startswith("check digit mismatch")


def test_check_digit_x():
    # Classic example VIN whose check digit is X.
    assert vin.check_digit("1M8GDM9AXKP042788") == "X"


def test_find_vins_in_listing_text():
    text = "2003 Accord EX, clean title. VIN: 1HGCM82633A004352. Call 555-0100."
    assert vin.find_vins(text) == ["1HGCM82633A004352"]
