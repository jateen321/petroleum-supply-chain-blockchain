"""
blockchain.py – Core blockchain data structures and Proof-of-Work consensus.

Each Block contains:
  - An ordered list of validated supply chain Transactions
  - A Merkle root of all transaction IDs (data integrity)
  - SHA-256 Proof-of-Work hash linking it to the previous block

The Blockchain class manages the chain, the mempool (unconfirmed transactions),
and provides fork-resolution via the longest-valid-chain rule.
"""

import time
import json
from dataclasses import dataclass, field
from typing import List, Optional

from utils import sha256, double_sha256, merkle_root, dict_to_canonical
from transaction import Transaction

# Default mining difficulty (number of leading zeros required in block hash)
DEFAULT_DIFFICULTY = 4


# ──────────────────────────────────────────────────────────────────────────────
# Block
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Block:
    """
    A single block in the petroleum supply chain ledger.

    Fields
    ------
    index           : block height (genesis = 0)
    timestamp       : Unix epoch float
    transactions    : list of Transaction dicts stored in this block
    previous_hash   : hash of the preceding block
    merkle_root_val : Merkle root of transaction IDs
    nonce           : value iterated during mining
    hash            : SHA-256 PoW hash of this block's header
    miner_address   : address of the node that mined this block
    """

    index: int
    timestamp: float
    transactions: List[dict]        # stored as plain dicts for JSON-compat
    previous_hash: str
    merkle_root_val: str = field(default="", init=False)
    nonce: int = 0
    hash: str = field(default="", init=False)
    miner_address: str = ""

    def __post_init__(self):
        self.merkle_root_val = self._compute_merkle_root()
        if not self.hash:
            self.hash = self.compute_hash()

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    def _header_dict(self) -> dict:
        """Canonical block header used for hashing / mining."""
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "merkle_root": self.merkle_root_val,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce,
            "miner_address": self.miner_address,
        }

    def compute_hash(self) -> str:
        return double_sha256(dict_to_canonical(self._header_dict()))

    def _compute_merkle_root(self) -> str:
        tx_ids = [tx.get("tx_id", "") for tx in self.transactions]
        return merkle_root(tx_ids)

    # ------------------------------------------------------------------
    # Mining (Proof of Work)
    # ------------------------------------------------------------------

    def mine(self, difficulty: int) -> None:
        """
        Increment nonce until the block hash has *difficulty* leading zeros.
        Updates self.nonce and self.hash in place.
        """
        target = "0" * difficulty
        self.nonce = 0
        self.hash = self.compute_hash()
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.compute_hash()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "transactions": self.transactions,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root_val,
            "nonce": self.nonce,
            "hash": self.hash,
            "miner_address": self.miner_address,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        blk = cls(
            index=d["index"],
            timestamp=d["timestamp"],
            transactions=d["transactions"],
            previous_hash=d["previous_hash"],
            nonce=d["nonce"],
            miner_address=d.get("miner_address", ""),
        )
        blk.merkle_root_val = d["merkle_root"]
        blk.hash = d["hash"]
        return blk

    def __repr__(self) -> str:
        return (
            f"Block(#{self.index}, txs={len(self.transactions)}, "
            f"hash={self.hash[:12]}…)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Blockchain
# ──────────────────────────────────────────────────────────────────────────────

class Blockchain:
    """
    The immutable petroleum supply chain ledger.

    Attributes
    ----------
    chain      : ordered list of confirmed Blocks
    mempool    : list of Transaction dicts awaiting inclusion in next block
    difficulty : PoW difficulty (leading zeros required)
    """

    def __init__(self, difficulty: int = DEFAULT_DIFFICULTY):
        self.difficulty = difficulty
        self.chain: List[Block] = []
        self.mempool: List[dict] = []          # unconfirmed transactions
        self._create_genesis_block()

    # ------------------------------------------------------------------
    # Genesis
    # ------------------------------------------------------------------

    def _create_genesis_block(self) -> None:
        """Creates and appends the genesis block (index 0, no transactions)."""
        genesis = Block(
            index=0,
            timestamp=0.0,
            transactions=[],
            previous_hash="0" * 64,
            miner_address="GENESIS",
        )
        genesis.mine(self.difficulty)
        self.chain.append(genesis)

    # ------------------------------------------------------------------
    # Chain properties
    # ------------------------------------------------------------------

    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    @property
    def height(self) -> int:
        return len(self.chain) - 1      # genesis is height 0

    # ------------------------------------------------------------------
    # Mempool management
    # ------------------------------------------------------------------

    def add_transaction(self, tx: Transaction | dict) -> bool:
        """
        Validate and add a transaction to the mempool.

        Accepts either a Transaction object or its dict representation.
        Returns True if accepted, False if invalid or already in mempool / chain.
        """
        if isinstance(tx, Transaction):
            tx_dict = tx.to_dict()
        else:
            tx_dict = tx

        # Re-hydrate and validate
        try:
            tx_obj = Transaction.from_dict(tx_dict)
        except Exception:
            return False

        if not tx_obj.is_valid():
            return False

        # Check for duplicate in mempool
        if any(t["tx_id"] == tx_dict["tx_id"] for t in self.mempool):
            return False

        # Check not already confirmed
        if self._is_confirmed(tx_dict["tx_id"]):
            return False

        self.mempool.append(tx_dict)
        return True

    def _is_confirmed(self, tx_id: str) -> bool:
        """Return True if *tx_id* appears in any confirmed block."""
        for block in self.chain:
            if any(tx.get("tx_id") == tx_id for tx in block.transactions):
                return True
        return False

    # ------------------------------------------------------------------
    # Mining
    # ------------------------------------------------------------------

    def mine_pending_transactions(self, miner_address: str) -> Block:
        """
        Gather all mempool transactions into a new Block and mine it.

        Parameters
        ----------
        miner_address : address credited with finding the block

        Returns
        -------
        The newly mined Block (already appended to self.chain).
        """
        if not self.mempool:
            raise ValueError("No pending transactions to mine.")

        new_block = Block(
            index=len(self.chain),
            timestamp=time.time(),
            transactions=list(self.mempool),
            previous_hash=self.last_block.hash,
            miner_address=miner_address,
        )
        new_block.mine(self.difficulty)
        self.chain.append(new_block)
        self.mempool.clear()
        return new_block

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_chain_valid(self) -> bool:
        """
        Full chain validation:
          1. Genesis block has expected previous_hash
          2. Each block's stored hash matches recomputed hash
          3. Each block's previous_hash matches prior block's hash
          4. Each block's Merkle root matches stored transactions
          5. Stored hash satisfies PoW difficulty
          6. All transactions in each block are individually valid
        """
        target = "0" * self.difficulty

        for i, block in enumerate(self.chain):
            # Rule 1 – genesis
            if i == 0:
                if block.previous_hash != "0" * 64:
                    return False

            # Rule 2 – hash integrity
            if block.compute_hash() != block.hash:
                return False

            # Rule 3 – chain linkage
            if i > 0 and block.previous_hash != self.chain[i - 1].hash:
                return False

            # Rule 4 – Merkle root
            tx_ids = [tx.get("tx_id", "") for tx in block.transactions]
            if merkle_root(tx_ids) != block.merkle_root_val:
                return False

            # Rule 5 – PoW
            if not block.hash.startswith(target):
                return False

            # Rule 6 – transaction validity (skip genesis coinbase)
            if i > 0:
                for tx_dict in block.transactions:
                    try:
                        tx_obj = Transaction.from_dict(tx_dict)
                        if not tx_obj.is_valid():
                            return False
                    except Exception:
                        return False

        return True

    # ------------------------------------------------------------------
    # Fork resolution (longest valid chain)
    # ------------------------------------------------------------------

    def replace_chain(self, new_chain_dicts: list) -> bool:
        """
        Replace this chain with *new_chain_dicts* if it is longer and valid.

        Parameters
        ----------
        new_chain_dicts : list of block dicts (from a peer's /chain response)

        Returns True if the chain was replaced, False otherwise.
        """
        if len(new_chain_dicts) <= len(self.chain):
            return False

        # Reconstruct candidate chain
        candidate: List[Block] = [Block.from_dict(d) for d in new_chain_dicts]
        candidate_bc = Blockchain.__new__(Blockchain)
        candidate_bc.difficulty = self.difficulty
        candidate_bc.chain = candidate
        candidate_bc.mempool = []

        if not candidate_bc.is_chain_valid():
            return False

        self.chain = candidate
        return True

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "chain": [b.to_dict() for b in self.chain],
            "length": len(self.chain),
            "difficulty": self.difficulty,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Blockchain":
        bc = cls.__new__(cls)
        bc.difficulty = d.get("difficulty", DEFAULT_DIFFICULTY)
        bc.chain = [Block.from_dict(b) for b in d["chain"]]
        bc.mempool = []
        return bc

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def __repr__(self) -> str:
        return (
            f"Blockchain(height={self.height}, "
            f"difficulty={self.difficulty}, "
            f"mempool={len(self.mempool)} txs)"
        )
