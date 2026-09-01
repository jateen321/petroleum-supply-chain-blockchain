"""
blockchain.py – Core blockchain data structures and Proof-of-Work consensus.

Each Block contains:
  - An ordered list of validated supply chain Transactions
  - A Merkle root of all transaction IDs (data integrity)
  - SHA-256 Proof-of-Work hash linking it to the previous block

The Blockchain class manages the chain, the mempool (unconfirmed transactions),
custody enforcement (see ledger.py), and fork resolution via the
longest-valid-chain rule.

Three independent layers decide whether a transaction is acceptable:

  1. Transaction.is_valid()  – is it well-formed and correctly signed?
  2. CustodyLedger           – does the sender actually hold the petroleum?
  3. Proof-of-Work           – has history been rewritten?

Layer 2 is what stops a signed-but-fraudulent shipment: without it a producer
could ship the same barrels twice, and any wallet could invent supply.
"""

import os
import time
import json
from dataclasses import dataclass, field
from typing import List, Optional, Set

from utils import sha256, double_sha256, merkle_root, dict_to_canonical
from transaction import Transaction
from ledger import CustodyLedger, CustodyError

# Default mining difficulty (number of leading zeros required in block hash)
DEFAULT_DIFFICULTY = 4

# Marker for the synthetic record that carries the producer registry in genesis.
REGISTRY_RECORD_TYPE = "PRODUCER_REGISTRY"


def registry_record(producers) -> dict:
    """
    Build the genesis record that pins the set of authorised producers.

    The registry lives *inside the genesis block* rather than in node config, so
    that it is consensus-critical: it is covered by the genesis Merkle root and
    therefore by the genesis hash, it travels with the chain during sync, and
    two nodes configured with different producer sets have different genesis
    hashes and simply refuse to federate.
    """
    sorted_producers = sorted(set(producers))
    content = {"type": REGISTRY_RECORD_TYPE, "producers": sorted_producers}
    return {
        "tx_id": sha256(dict_to_canonical(content)),
        "type": REGISTRY_RECORD_TYPE,
        "producers": sorted_producers,
    }


def _registry_record_is_intact(rec: dict) -> bool:
    """
    Verify a registry record's tx_id still matches its contents.

    Without this check an attacker could append themselves to the producer list
    while leaving tx_id untouched — the Merkle root is computed over tx_ids, so
    the block hash would still verify and the tamper would go unnoticed.
    """
    content = {
        "type": REGISTRY_RECORD_TYPE,
        "producers": sorted(set(rec.get("producers", []))),
    }
    return rec.get("tx_id") == sha256(dict_to_canonical(content))


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

    The set of authorised producers is *not* stored as an attribute — it is read
    back out of the genesis block, so it can never drift from what the chain
    itself commits to.
    """

    def __init__(
        self,
        difficulty: int = DEFAULT_DIFFICULTY,
        authorized_producers: Optional[List[str]] = None,
    ):
        self.difficulty = difficulty
        self.chain: List[Block] = []
        self.mempool: List[dict] = []          # unconfirmed transactions
        self._create_genesis_block(authorized_producers or [])

    # ------------------------------------------------------------------
    # Genesis
    # ------------------------------------------------------------------

    def _create_genesis_block(self, authorized_producers: List[str]) -> None:
        """
        Create and append the genesis block.

        Genesis is fully deterministic (fixed timestamp, no miner reward, fixed
        previous_hash) so that every node given the same producer registry
        independently derives an identical genesis hash and can sync without
        being handed one.
        """
        genesis = Block(
            index=0,
            timestamp=0.0,
            transactions=[registry_record(authorized_producers)],
            previous_hash="0" * 64,
            miner_address="GENESIS",
        )
        genesis.mine(self.difficulty)
        self.chain.append(genesis)

    @property
    def authorized_producers(self) -> Set[str]:
        """Producer addresses permitted to mint new supply, read from genesis."""
        for rec in self.chain[0].transactions:
            if rec.get("type") == REGISTRY_RECORD_TYPE:
                return set(rec.get("producers", []))
        return set()

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
    # Custody state
    # ------------------------------------------------------------------

    def custody_state(self, include_mempool: bool = False) -> CustodyLedger:
        """
        Replay the chain to derive current custody (who holds what).

        Recomputed on demand rather than cached: blocks can arrive from peers
        and replace the chain wholesale, and a stale cache there would be a
        consensus bug.  A production node would maintain this incrementally with
        an undo log; at demo scale the replay is cheap and impossible to
        desynchronise.

        Raises CustodyError if the confirmed chain itself is inconsistent, which
        callers should treat as an invalid chain.
        """
        ledger = CustodyLedger(self.authorized_producers)
        for block in self.chain[1:]:          # genesis holds no transfers
            for tx in block.transactions:
                ledger.apply(tx)
        if include_mempool:
            for tx in self.mempool:
                ledger.apply(tx)
        return ledger

    # ------------------------------------------------------------------
    # Mempool management
    # ------------------------------------------------------------------

    def add_transaction(self, tx: Transaction | dict) -> bool:
        """
        Validate and add a transaction to the mempool.

        Accepts either a Transaction object or its dict representation.
        Returns True if accepted, False if invalid, unaffordable, or already
        present in the mempool / chain.
        """
        ok, _ = self.add_transaction_verbose(tx)
        return ok

    def add_transaction_verbose(self, tx: Transaction | dict) -> tuple[bool, str]:
        """
        Same as `add_transaction` but returns (accepted, reason).

        The reason string is what makes a rejection legible over the HTTP API —
        "insufficient custody: … holds 1,500 L of crude_oil, tried to move
        8,500 L" is far more useful to an operator than a bare False.
        """
        tx_dict = tx.to_dict() if isinstance(tx, Transaction) else tx

        # Re-hydrate and validate shape + signature
        try:
            tx_obj = Transaction.from_dict(tx_dict)
        except Exception as e:
            return False, f"malformed transaction: {e}"

        if not tx_obj.is_valid():
            return False, "invalid transaction (bad signature or fields)"

        # Check for duplicate in mempool
        if any(t["tx_id"] == tx_dict["tx_id"] for t in self.mempool):
            return False, "already in mempool"

        # Check not already confirmed
        if self._is_confirmed(tx_dict["tx_id"]):
            return False, "already confirmed on chain"

        # Custody: does the sender actually hold this petroleum?  Checked
        # against confirmed state *plus* everything already queued, so two
        # conflicting shipments cannot both sit in the mempool.
        try:
            projected = self.custody_state(include_mempool=True)
        except CustodyError as e:
            return False, f"local chain state is inconsistent: {e}"

        ok, reason = projected.check(tx_dict)
        if not ok:
            return False, reason

        self.mempool.append(tx_dict)
        return True, ""

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
        Gather affordable mempool transactions into a new Block and mine it.

        Transactions that no longer apply against current custody state — for
        example because a peer's block already spent that inventory — are
        evicted rather than mined into an invalid block.

        Returns the newly mined Block (already appended to self.chain).
        """
        if not self.mempool:
            raise ValueError("No pending transactions to mine.")

        ledger = self.custody_state()
        included: List[dict] = []
        evicted: List[dict] = []
        for tx in self.mempool:
            try:
                ledger.apply(tx)
            except CustodyError:
                evicted.append(tx)
                continue
            included.append(tx)

        if not included:
            self.mempool = []
            raise ValueError(
                "No pending transactions are still valid against current "
                "custody state – mempool evicted."
            )

        new_block = Block(
            index=len(self.chain),
            timestamp=time.time(),
            transactions=included,
            previous_hash=self.last_block.hash,
            miner_address=miner_address,
        )
        new_block.mine(self.difficulty)
        self.chain.append(new_block)
        self.mempool = []
        return new_block

    # ------------------------------------------------------------------
    # Accepting a block from a peer
    # ------------------------------------------------------------------

    def add_block(self, block_dict: dict) -> tuple[bool, str]:
        """
        Fully validate a peer-supplied block and append it if it extends our tip.

        A node must never append a peer's block on Proof-of-Work alone: mining
        proves effort was spent, not that the contents are legitimate.  Every
        transaction is re-verified for signature *and* custody here, exactly as
        if we had built the block ourselves.

        Returns (accepted, reason).
        """
        try:
            candidate = Block.from_dict(block_dict)
        except Exception as e:
            return False, f"malformed block: {e}"

        if candidate.index != len(self.chain):
            return False, "does not extend our tip"
        if candidate.previous_hash != self.last_block.hash:
            return False, "previous_hash does not match our tip"
        if not candidate.hash.startswith("0" * self.difficulty):
            return False, "insufficient proof-of-work"
        if candidate.compute_hash() != candidate.hash:
            return False, "stored hash does not match header"

        tx_ids = [tx.get("tx_id", "") for tx in candidate.transactions]
        if merkle_root(tx_ids) != candidate.merkle_root_val:
            return False, "merkle root does not match transactions"

        try:
            ledger = self.custody_state()
        except CustodyError as e:
            return False, f"local chain state is inconsistent: {e}"

        for tx_dict in candidate.transactions:
            try:
                tx_obj = Transaction.from_dict(tx_dict)
            except Exception as e:
                return False, f"malformed transaction in block: {e}"
            if not tx_obj.is_valid():
                return False, "block contains an improperly signed transaction"
            try:
                ledger.apply(tx_dict)
            except CustodyError as e:
                return False, f"block contains an unbacked shipment: {e}"

        # Drop now-confirmed transactions from our mempool.
        confirmed = {tx.get("tx_id") for tx in candidate.transactions}
        self.mempool = [t for t in self.mempool if t["tx_id"] not in confirmed]

        self.chain.append(candidate)
        return True, ""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_chain_valid(self) -> bool:
        """
        Full chain validation:
          1. Genesis has the expected previous_hash and an intact registry
          2. Each block's stored hash matches recomputed hash
          3. Each block's previous_hash matches prior block's hash
          4. Each block's Merkle root matches stored transactions
          5. Stored hash satisfies PoW difficulty
          6. All transactions in each block are individually valid (signature)
          7. Every transfer is backed by custody — no double-spends, no supply
             invented from nothing
        """
        target = "0" * self.difficulty

        for i, block in enumerate(self.chain):
            # Rule 1 – genesis
            if i == 0:
                if block.previous_hash != "0" * 64:
                    return False
                for rec in block.transactions:
                    if rec.get("type") != REGISTRY_RECORD_TYPE:
                        return False
                    if not _registry_record_is_intact(rec):
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

            # Rule 6 – transaction validity (genesis carries no transfers)
            if i > 0:
                for tx_dict in block.transactions:
                    try:
                        tx_obj = Transaction.from_dict(tx_dict)
                        if not tx_obj.is_valid():
                            return False
                    except Exception:
                        return False

        # Rule 7 – custody replay over the whole chain
        try:
            self.custody_state()
        except CustodyError:
            return False

        return True

    # ------------------------------------------------------------------
    # Fork resolution (longest valid chain)
    # ------------------------------------------------------------------

    def replace_chain(self, new_chain_dicts: list) -> bool:
        """
        Replace this chain with *new_chain_dicts* if it is longer and valid.

        Beyond length and validity the candidate must share our genesis hash.
        Without that check a node would happily adopt a longer chain from a
        different network — or one whose genesis lists a different, attacker-
        controlled set of authorised producers.

        Returns True if the chain was replaced, False otherwise.
        """
        if len(new_chain_dicts) <= len(self.chain):
            return False

        try:
            candidate: List[Block] = [Block.from_dict(d) for d in new_chain_dicts]
        except Exception:
            return False

        if not candidate:
            return False
        if candidate[0].hash != self.chain[0].hash:
            return False

        candidate_bc = Blockchain.__new__(Blockchain)
        candidate_bc.difficulty = self.difficulty
        candidate_bc.chain = candidate
        candidate_bc.mempool = []

        if not candidate_bc.is_chain_valid():
            return False

        self.chain = candidate
        # Re-validate our mempool against the new history; anything the adopted
        # chain already confirmed or invalidated is dropped.
        surviving = [
            t for t in self.mempool if not self._is_confirmed(t.get("tx_id", ""))
        ]
        self.mempool = []
        for tx in surviving:
            self.add_transaction(tx)
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """
        Write the chain and mempool to *path* as JSON.

        Written to a temporary file and moved into place with os.replace, which
        is atomic on POSIX: a node killed mid-write leaves the previous good
        ledger intact rather than a truncated one.
        """
        payload = {
            "chain": [b.to_dict() for b in self.chain],
            "mempool": self.mempool,
            "difficulty": self.difficulty,
        }
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str) -> "Blockchain":
        """
        Load a chain previously written by `save`.

        The chain is fully re-validated on load — including custody replay — so
        a hand-edited ledger file is rejected rather than silently trusted.
        Raises ValueError if validation fails.
        """
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        bc = cls.__new__(cls)
        bc.difficulty = payload.get("difficulty", DEFAULT_DIFFICULTY)
        bc.chain = [Block.from_dict(b) for b in payload["chain"]]
        bc.mempool = []

        if not bc.chain:
            raise ValueError(f"{path}: contains no blocks")
        if not bc.is_chain_valid():
            raise ValueError(f"{path}: stored chain failed validation")

        # Restore the mempool through the normal validation path.
        for tx in payload.get("mempool", []):
            bc.add_transaction(tx)
        return bc

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
