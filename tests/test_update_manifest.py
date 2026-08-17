from app.server.main import version_tuple


def test_version_tuple_compares_multidigit_versions() -> None:
    assert version_tuple("0.1.10") > version_tuple("0.1.2")
