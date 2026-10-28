"""Login encryption accepts the key bodies a camera might send."""
import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from vigicam_pure.api import VIGICamera, VIGIError

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=1024)


def _body(fmt) -> str:
    pem = _KEY.public_key().public_bytes(serialization.Encoding.PEM, fmt).decode()
    return "".join(pem.splitlines()[1:-1])  # bare base64, as the camera sends it


@pytest.mark.parametrize(
    "fmt",
    [serialization.PublicFormat.SubjectPublicKeyInfo, serialization.PublicFormat.PKCS1],
)
def test_encrypts_with_either_key_body(fmt):
    cam = VIGICamera("192.0.2.1", "admin", "pw")
    token = cam._encrypt_password("nonce", _body(fmt))
    plain = _KEY.decrypt(base64.b64decode(token), padding.PKCS1v15()).decode()
    assert plain.endswith(":nonce") and len(plain) == 32 + len(":nonce")


def test_unreadable_key_is_a_vigi_error():
    with pytest.raises(VIGIError):
        VIGICamera("192.0.2.1", "admin", "pw")._encrypt_password("n", "not-a-key")
