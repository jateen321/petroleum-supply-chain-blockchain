"""utils.py – Hashing helpers and Merkle tree computation."""

import hashlib
import json
from typing import List


def sha256(data: str | bytes) -> str:
    """Return the SHA-256 hex digest of *data*."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def double_sha256(data: str | bytes) -> str:
    """Return SHA-256(SHA-256(data)) – used for block hashes, Bitcoin-style."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(hashlib.sha256(data).digest()).hexdigest()


def merkle_root(tx_hashes: List[str]) -> str:
    """
    Compute the Merkle root of a list of transaction ID hex-strings.

    Returns the hash of all transactions combined if the list length is odd
    (the last hash is duplicated, as per Bitcoin convention).
    Returns '0' * 64 for an empty list.
    """
    if not tx_hashes:
        return "0" * 64

    layer = list(tx_hashes)

    while len(layer) > 1:
        # If odd number, duplicate the last element
        if len(layer) % 2 != 0:
            layer.append(layer[-1])

        next_layer = []
        for i in range(0, len(layer), 2):
            combined = layer[i] + layer[i + 1]
            next_layer.append(sha256(combined))
        layer = next_layer

    return layer[0]


def dict_to_canonical(d: dict) -> str:
    """
    Serialize a dictionary to a canonical JSON string (sorted keys) for
    consistent hashing regardless of insertion order.
    """
    return json.dumps(d, sort_keys=True, separators=(",", ":"))
