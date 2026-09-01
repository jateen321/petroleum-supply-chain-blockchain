"""tests/test_blockchain.py – Unit tests for Block and Blockchain."""
import sys
import os
import json
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
def bc(wallets):
    """
    Fresh blockchain with difficulty=2 (fast mining in tests).

    wallets[0] is registered as an authorised producer, so it may mint new
    supply; every other address must be backed by custody it actually received.
    """
    return Blockchain(difficulty=2, authorized_producers=[wallets[0].address])


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
    def test_genesis_block(self, bc, wallets):
        assert len(bc.chain) == 1
        genesis = bc.chain[0]
        assert genesis.index == 0
        assert genesis.previous_hash == "0" * 64
        # Genesis carries no transfers, only the producer registry it pins.
        assert len(genesis.transactions) == 1
        assert genesis.transactions[0]["type"] == "PRODUCER_REGISTRY"
        assert bc.authorized_producers == {wallets[0].address}

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
        producer, refinery = wallets
        # Mint crude to the refinery, then refine it out over further blocks.
        bc.add_transaction(
            create_transaction(producer, refinery.address, "crude_oil", 900, "PRODUCTION")
        )
        bc.mine_pending_transactions(producer.address)
        for i in range(2):
            buyer = Wallet()
            tx = create_transaction(refinery, buyer.address, "petrol", 100 + i, "REFINERY")
            bc.add_transaction(tx)
            bc.mine_pending_transactions(producer.address)
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

    def test_replace_chain_accepts_longer(self, bc, wallets):
        """A longer valid chain sharing our genesis should replace ours."""
        producer, receiver = wallets
        # Same producer registry => same genesis hash => same network.
        longer = Blockchain(difficulty=2, authorized_producers=[producer.address])
        for i in range(2):
            tx = create_transaction(
                producer, receiver.address, "crude_oil", 50 + i, "PRODUCTION"
            )
            longer.add_transaction(tx)
            longer.mine_pending_transactions(producer.address)

        # bc (height=0) should accept longer (height=2)
        replaced = bc.replace_chain([b.to_dict() for b in longer.chain])
        assert replaced
        assert bc.height == 2

    def test_replace_chain_rejects_shorter(self, bc, signed_tx, wallets):
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        # Try to replace with just genesis
        short = Blockchain(difficulty=2, authorized_producers=[wallets[0].address])
        replaced = bc.replace_chain([b.to_dict() for b in short.chain])
        assert not replaced

    def test_mine_empty_mempool_raises(self, bc, wallets):
        with pytest.raises(ValueError):
            bc.mine_pending_transactions(wallets[0].address)


# ── Persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_load_round_trip(self, bc, signed_tx, wallets, tmp_path):
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        path = str(tmp_path / "chain.json")
        bc.save(path)

        restored = Blockchain.load(path)
        assert restored.height == bc.height
        assert restored.chain[0].hash == bc.chain[0].hash
        assert restored.last_block.hash == bc.last_block.hash
        assert restored.is_chain_valid()

    def test_custody_survives_restart(self, bc, signed_tx, wallets, tmp_path):
        """Balances are re-derived from the reloaded chain, not stored."""
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        path = str(tmp_path / "chain.json")
        bc.save(path)

        restored = Blockchain.load(path)
        assert restored.custody_state().balance_of(
            wallets[1].address, "crude_oil"
        ) == 1000

    def test_registry_survives_restart(self, bc, wallets, tmp_path):
        path = str(tmp_path / "chain.json")
        bc.save(path)
        assert Blockchain.load(path).authorized_producers == {wallets[0].address}

    def test_mempool_is_persisted(self, bc, signed_tx, tmp_path):
        bc.add_transaction(signed_tx)
        path = str(tmp_path / "chain.json")
        bc.save(path)
        assert len(Blockchain.load(path).mempool) == 1

    def test_tampered_file_is_rejected_on_load(self, bc, signed_tx, wallets, tmp_path):
        """A hand-edited ledger file must not be silently trusted."""
        bc.add_transaction(signed_tx)
        bc.mine_pending_transactions(wallets[0].address)
        path = str(tmp_path / "chain.json")
        bc.save(path)

        with open(path) as f:
            payload = json.load(f)
        payload["chain"][1]["transactions"][0]["quantity_litres"] = 999_999
        with open(path, "w") as f:
            json.dump(payload, f)

        with pytest.raises(ValueError):
            Blockchain.load(path)

    def test_save_is_atomic_leaves_no_temp_file(self, bc, tmp_path):
        path = str(tmp_path / "chain.json")
        bc.save(path)
        assert not os.path.exists(f"{path}.tmp")
