"""tests/test_blockchain.py – Unit tests for Block and Blockchain."""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from blockchain import Block, Blockchain, DEFAULT_DIFFICULTY
from transaction import create_transaction, Transaction
from wallet import Wallet


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def wallets():
    return Wallet(), Wallet()


@pytest.fixture
def bc():
    """Fresh blockchain with difficulty=2 (fast mining in tests)."""
    return Blockchain(difficulty=2)


@pytest.fixture
def signed_tx(wallets):
    sender, receiver = wallets
    return create_transaction(sender, receiver.address, "crude_oil", 1000, "PRODUCTION")


# ── Block tests ───────────────────────────────────────────────────────────────

class TestBlock:
    def test_hash_changes_with_nonce(self):
        blk = Block(0, time.time(), [], "0" * 64)
        h1 = blk.compute_hash()
        blk.nonce = 99
        h2 = blk.compute_hash()
        assert h1 != h2

    def test_mine_satisfies_difficulty(self):
        blk = Block(1, time.time(), [], "a" * 64)
        blk.mine(2)
        assert blk.hash.startswith("00"), f"Hash {blk.hash} does not start with 00"

    def test_serialization_round_trip(self, signed_tx):
        blk = Block(1, time.time(), [signed_tx.to_dict()], "b" * 64)
        blk.mine(1)
        restored = Block.from_dict(blk.to_dict())
        assert restored.hash == blk.hash
        assert restored.index == blk.index
        assert restored.nonce == blk.nonce
        assert len(restored.transactions) == 1

    def test_merkle_root_stored_correctly(self, signed_tx):
        blk = Block(2, time.time(), [signed_tx.to_dict()], "c" * 64)
        from utils import merkle_root
        expected = merkle_root([signed_tx.tx_id])
        assert blk.merkle_root_val == expected


# ── Blockchain tests ──────────────────────────────────────────────────────────

class TestBlockchain:
    def test_genesis_block(self, bc):
        assert len(bc.chain) == 1
        genesis = bc.chain[0]
        assert genesis.index == 0
        assert genesis.previous_hash == "0" * 64
        assert genesis.transactions == []

    def test_genesis_valid(self, bc):
        assert bc.is_chain_valid()

    def test_add_transaction_to_mempool(self, bc, signed_tx):
        ok = bc.add_transaction(signed_tx)
        assert ok
        assert len(bc.mempool) == 1

    def test_reject_invalid_transaction(self, bc, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="petrol",
            quantity_litres=500,
            stage="REFINERY",
        )
        # NOT signed – should be rejected
        ok = bc.add_transaction(tx)
        assert not ok

    def test_reject_duplicate_transaction(self, bc, signed_tx):
        bc.add_transaction(signed_tx)
        ok2 = bc.add_transaction(signed_tx)   # same tx again
        assert not ok2
        assert len(bc.mempool) == 1

    def test_mine_creates_block(self, bc, signed_tx, wallets):
        bc.add_transaction(signed_tx)
        miner = wallets[0]
        block = bc.mine_pending_transactions(miner.address)
        assert block.index == 1
        assert len(bc.chain) == 2
        assert bc.mempool == []
        assert bc.is_chain_valid()

    def test_chain_valid_after_multiple_blocks(self, bc, wallets):
        sender, receiver = wallets
        for i in range(3):
            tx = create_transaction(sender, receiver.address, "diesel", 100 + i, "DISTRIBUTION")
            bc.add_transaction(tx)
            bc.mine_pending_transactions(sender.address)
        assert bc.is_chain_valid()
        assert bc.height == 3

    def test_tampered_transaction_detected(self, bc, signed_tx, wallets):
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        # Tamper with a transaction inside block 1
        bc.chain[1].transactions[0]["quantity_litres"] = 999999
        assert not bc.is_chain_valid()

    def test_tampered_hash_detected(self, bc, signed_tx, wallets):
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        bc.chain[1].hash = "0" * 64   # corrupt hash
        assert not bc.is_chain_valid()

    def test_replace_chain_accepts_longer(self, bc, signed_tx, wallets):
        """A longer valid chain should replace the shorter local chain."""
        # Build a longer blockchain
        longer = Blockchain(difficulty=2)
        for i in range(2):
            tx = create_transaction(wallets[0], wallets[1].address, "LPG", 50 + i, "RETAIL")
            longer.add_transaction(tx)
            longer.mine_pending_transactions(wallets[0].address)

        # bc (height=0) should accept longer (height=2)
        replaced = bc.replace_chain([b.to_dict() for b in longer.chain])
        assert replaced
        assert bc.height == 2

    def test_replace_chain_rejects_shorter(self, bc, signed_tx, wallets):
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        # Try to replace with just genesis
        short = Blockchain(difficulty=2)
        replaced = bc.replace_chain([b.to_dict() for b in short.chain])
        assert not replaced

    def test_mine_empty_mempool_raises(self, bc, wallets):
        with pytest.raises(ValueError):
            bc.mine_pending_transactions(wallets[0].address)
