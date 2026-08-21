import sys

import pytest

from app.client.credential_store import protect_text, unprotect_text


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip() -> None:
    encrypted = protect_text("Admin12345!")

    assert encrypted != "Admin12345!"
    assert unprotect_text(encrypted) == "Admin12345!"
