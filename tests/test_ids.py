from eqrisk.ids import cik10, name_key, normalize_symbol, strip_old_suffix, to_vendor, token_similarity


def test_symbol_styles():
    assert normalize_symbol("brk-b.US") == "BRK.B" and normalize_symbol("BRK/B") == "BRK.B"
    assert to_vendor("BRK.B") == "BRK-B"
    assert strip_old_suffix("DOW_old") == "DOW" and strip_old_suffix("ANET_old2") == "ANET"
    assert cik10(320193) == "0000320193"


def test_name_keys_ignore_legal_form_state_tags_and_class():
    assert name_key("Apple Inc.") == name_key("APPLE INC /CA/") == "APPLE"
    assert name_key("Alphabet Inc Class A") == "ALPHABET"
    assert name_key("Johnson & Johnson") == "JOHNSON AND JOHNSON"
    assert name_key("17 ICE BOX/SWEETER TWITTER, LLC") != "TWITTER"
    assert token_similarity("Twitter Inc", "TWITTER, INC.") == 1.0
