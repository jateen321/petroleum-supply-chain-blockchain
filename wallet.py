"""
wallet.py – ECDSA (secp256k1) digital wallet for the petroleum supply chain.

Each supply chain participant (producer, refinery, distributor, petrol station)
owns a Wallet.  Transactions are signed by the sender's private key and
verified using their public key, ensuring authenticity and non-repudiation.
"""

import hashlib
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256K1,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)
from cryptography.exceptions import InvalidSignature


class Wallet:
    """
    Represents a participant's cryptographic identity on the blockchain.

    Attributes
    ----------
    private_key : EllipticCurvePrivateKey
    public_key  : EllipticCurvePublicKey
    address     : str  – hex-encoded SHA-256 of the compressed public key bytes
    """

    def __init__(self, private_key: EllipticCurvePrivateKey | None = None):
        if private_key is None:
            self._private_key = ec.generate_private_key(SECP256K1())
        else:
            self._private_key = private_key
        self._public_key: EllipticCurvePublicKey = self._private_key.public_key()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def private_key(self) -> EllipticCurvePrivateKey:
        return self._private_key

    @property
    def public_key(self) -> EllipticCurvePublicKey:
        return self._public_key

    @property
    def address(self) -> str:
        """Wallet address = SHA-256 of compressed public key bytes (hex)."""
        pub_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.CompressedPoint,
        )
        return hashlib.sha256(pub_bytes).hexdigest()

    @property
    def public_key_hex(self) -> str:
        """Compressed public key as hex string (for serialization)."""
        pub_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.CompressedPoint,
        )
        return pub_bytes.hex()

    # ------------------------------------------------------------------
    # Signing & Verification
    # ------------------------------------------------------------------

    def sign(self, message: str) -> str:
        """
        Sign *message* with the private key.

        Returns
        -------
        str – DER-encoded signature as a lowercase hex string.
        """
        msg_bytes = message.encode("utf-8")
        sig_bytes = self._private_key.sign(msg_bytes, ec.ECDSA(hashes.SHA256()))
        return sig_bytes.hex()

    @staticmethod
    def verify(message: str, signature_hex: str, public_key_hex: str) -> bool:
        """
        Verify a hex-encoded DER signature against *message* using the
        compressed public key (hex-encoded).

        Returns True if signature is valid, False otherwise.
        """
        try:
            pub_bytes = bytes.fromhex(public_key_hex)
            public_key = ec.EllipticCurvePublicKey.from_encoded_point(
                SECP256K1(), pub_bytes
            )
            sig_bytes = bytes.fromhex(signature_hex)
            msg_bytes = message.encode("utf-8")
            public_key.verify(sig_bytes, msg_bytes, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError, Exception):
            return False

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_pem(self) -> bytes:
        """Serialize private key to PEM (for saving to disk)."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def from_pem(cls, pem_data: bytes) -> "Wallet":
        """Load a Wallet from a PEM-encoded private key."""
        private_key = serialization.load_pem_private_key(pem_data, password=None)
        return cls(private_key=private_key)

    def save(self, path: str) -> None:
        """Save wallet private key to *path*."""
        with open(path, "wb") as f:
            f.write(self.to_pem())

    @classmethod
    def load(cls, path: str) -> "Wallet":
        """Load wallet from PEM file at *path*."""
        with open(path, "rb") as f:
            return cls.from_pem(f.read())

    def __repr__(self) -> str:
        return f"Wallet(address={self.address[:16]}...)"
