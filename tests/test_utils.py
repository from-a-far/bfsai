from app.utils import normalize_vendor, short_uid


def test_short_uid_is_prefixed_and_short() -> None:
    identifier = short_uid()
    assert identifier.startswith("inv_")
    assert len(identifier) == 20


def test_normalize_vendor() -> None:
    assert normalize_vendor("Acme, Inc.") == "acme inc"
