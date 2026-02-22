"""
transaction.py – Supply chain transaction model.

A Transaction records the transfer of petroleum (e.g., crude oil, refined fuel)
from one supply chain participant to the next.  There are four stages:

    PRODUCTION  → crude oil leaves the oil field / ONGC well
    REFINERY    → refined product leaves the refinery
    DISTRIBUTION→ fuel moves from distributor to depot
    RETAIL      → fuel delivered to petrol station / end customer

Each transaction is:
  - identified by a unique tx_id (SHA-256 of its canonical content)
  - signed by the sender's wallet (ECDSA)
  - verified before being added to the mempool
"""

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from utils import sha256, dict_to_canonical
from wallet import Wallet


class Stage(str, Enum):
    PRODUCTION = "PRODUCTION"       # Oil field → Refinery
    REFINERY = "REFINERY"           # Refinery → Distributor
    DISTRIBUTION = "DISTRIBUTION"   # Distributor → Depot / Petrol station
    RETAIL = "RETAIL"               # Depot → End consumer / station


@dataclass
class Transaction:
    """
    A single transfer of petroleum between supply chain participants.

    Fields
    ------
    sender          : wallet address (hex) of the sender
    sender_pub_key  : compressed public key (hex) for signature verification
    receiver        : wallet address (hex) of the receiver
    commodity       : e.g. "crude_oil", "petrol", "diesel", "LPG"
    quantity_litres : volume transferred (must be > 0)
    stage           : Stage enum – which leg of the supply chain
    timestamp       : Unix epoch float
    tx_id           : SHA-256 of canonical fields (computed on creation)
    signature       : DER-encoded ECDSA signature (hex), added via sign()
    notes           : Optional free-text annotation (e.g., batch ID, GPS coords)
    """

    sender: str
    sender_pub_key: str
    receiver: str
    commodity: str
    quantity_litres: float
    stage: str                          # Stage value stored as string for JSON-compat
    timestamp: float = field(default_factory=time.time)
    tx_id: str = field(default="", init=False)
    signature: str = field(default="", init=False)
    notes: str = ""

    def __post_init__(self):
        # Normalise stage to uppercase string
        if isinstance(self.stage, Stage):
            self.stage = self.stage.value
        self.stage = self.stage.upper()
        # Compute the transaction ID
        self.tx_id = self._compute_id()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _signing_payload(self) -> str:
        """Canonical string that is signed / hashed."""
        return dict_to_canonical({
            "sender": self.sender,
            "receiver": self.receiver,
            "commodity": self.commodity,
            "quantity_litres": self.quantity_litres,
            "stage": self.stage,
            "timestamp": self.timestamp,
            "notes": self.notes,
        })

    def _compute_id(self) -> str:
        return sha256(self._signing_payload())

    # ------------------------------------------------------------------
    # Sign & Verify
    # ------------------------------------------------------------------

    def sign(self, wallet: Wallet) -> None:
        """
        Sign this transaction with *wallet*.

        The sender's address must match the wallet address.
        Raises ValueError if there is an address mismatch.
        """
        if wallet.address != self.sender:
            raise ValueError(
                f"Wallet address {wallet.address[:12]}… does not match "
                f"transaction sender {self.sender[:12]}…"
            )
        self.signature = wallet.sign(self._signing_payload())

    def is_valid(self) -> bool:
        """
        Validate this transaction:
          1. quantity must be positive
          2. sender != receiver
          3. stage must be a known Stage value
          4. signature must verify against sender's public key
        """
        if self.quantity_litres <= 0:
            return False
        if self.sender == self.receiver:
            return False
        if self.stage not in {s.value for s in Stage}:
            return False
        if not self.signature:
            return False
        return Wallet.verify(self._signing_payload(), self.signature, self.sender_pub_key)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "tx_id": self.tx_id,
            "sender": self.sender,
            "sender_pub_key": self.sender_pub_key,
            "receiver": self.receiver,
            "commodity": self.commodity,
            "quantity_litres": self.quantity_litres,
            "stage": self.stage,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        tx = cls(
            sender=d["sender"],
            sender_pub_key=d["sender_pub_key"],
            receiver=d["receiver"],
            commodity=d["commodity"],
            quantity_litres=d["quantity_litres"],
            stage=d["stage"],
            timestamp=d["timestamp"],
            notes=d.get("notes", ""),
        )
        tx.tx_id = d["tx_id"]          # restore original ID
        tx.signature = d.get("signature", "")
        return tx

    def __repr__(self) -> str:
        return (
            f"Transaction(id={self.tx_id[:10]}…, "
            f"stage={self.stage}, "
            f"{self.commodity} {self.quantity_litres}L, "
            f"from={self.sender[:10]}…)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Convenience factory
# ──────────────────────────────────────────────────────────────────────────────

def create_transaction(
    sender_wallet: Wallet,
    receiver_address: str,
    commodity: str,
    quantity_litres: float,
    stage: str | Stage,
    notes: str = "",
) -> Transaction:
    """
    Create, sign, and return a valid Transaction in one call.

    Parameters
    ----------
    sender_wallet    : the sending participant's Wallet
    receiver_address : address hex of the receiver
    commodity        : e.g. "crude_oil", "petrol", "diesel"
    quantity_litres  : amount transferred
    stage            : supply chain stage (string or Stage enum)
    notes            : optional annotation

    Raises
    ------
    ValueError if the transaction fails validation after signing.
    """
    tx = Transaction(
        sender=sender_wallet.address,
        sender_pub_key=sender_wallet.public_key_hex,
        receiver=receiver_address,
        commodity=commodity,
        quantity_litres=quantity_litres,
        stage=stage,
        notes=notes,
    )
    tx.sign(sender_wallet)
    if not tx.is_valid():
        raise ValueError("Transaction failed validation after signing.")
    return tx
