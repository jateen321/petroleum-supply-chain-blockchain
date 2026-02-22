"""tests/test_transaction.py – Unit tests for Transaction and Wallet."""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from wallet import Wallet
from transaction import Transaction, create_transaction, Stage


# ── Wallet tests ──────────────────────────────────────────────────────────────

class TestWallet:
    def test_generate_unique_addresses(self):
        w1, w2 = Wallet(), Wallet()
        assert w1.address != w2.address

    def test_address_is_64_hex_chars(self):
        w = Wallet()
        assert len(w.address) == 64
        assert all(c in "0123456789abcdef" for c in w.address)

    def test_public_key_hex_is_66_bytes(self):
        """Compressed secp256k1 key = 33 bytes = 66 hex chars."""
        w = Wallet()
        assert len(w.public_key_hex) == 66

    def test_sign_verify_round_trip(self):
        w = Wallet()
        msg = "hello supply chain"
        sig = w.sign(msg)
        assert Wallet.verify(msg, sig, w.public_key_hex)

    def test_verify_wrong_message_fails(self):
        w = Wallet()
        sig = w.sign("message A")
        assert not Wallet.verify("message B", sig, w.public_key_hex)

    def test_verify_wrong_key_fails(self):
        w1, w2 = Wallet(), Wallet()
        sig = w1.sign("test")
        assert not Wallet.verify("test", sig, w2.public_key_hex)

    def test_pem_serialization_round_trip(self, tmp_path):
        w = Wallet()
        pem = w.to_pem()
        w2 = Wallet.from_pem(pem)
        assert w.address == w2.address

    def test_save_load_roundtrip(self, tmp_path):
        w = Wallet()
        path = str(tmp_path / "wallet.pem")
        w.save(path)
        w2 = Wallet.load(path)
        assert w.address == w2.address


# ── Transaction tests ─────────────────────────────────────────────────────────

class TestTransaction:
    @pytest.fixture
    def wallets(self):
        return Wallet(), Wallet()

    def test_create_and_sign(self, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="crude_oil",
            quantity_litres=5000,
            stage="PRODUCTION",
        )
        tx.sign(sender)
        assert tx.is_valid()

    def test_factory_creates_valid_tx(self, wallets):
        sender, receiver = wallets
        tx = create_transaction(sender, receiver.address, "petrol", 3000, "REFINERY")
        assert tx.is_valid()
        assert tx.stage == "REFINERY"

    def test_stage_enum_accepted(self, wallets):
        sender, receiver = wallets
        tx = create_transaction(sender, receiver.address, "diesel", 100, Stage.DISTRIBUTION)
        assert tx.stage == "DISTRIBUTION"

    def test_invalid_quantity_rejected(self, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="petrol",
            quantity_litres=-1,
            stage="RETAIL",
        )
        tx.sign(sender)
        assert not tx.is_valid()

    def test_self_transfer_rejected(self, wallets):
        sender, _ = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=sender.address,   # same as sender
            commodity="LPG",
            quantity_litres=100,
            stage="RETAIL",
        )
        tx.sign(sender)
        assert not tx.is_valid()

    def test_unsigned_tx_is_invalid(self, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="petrol",
            quantity_litres=500,
            stage="REFINERY",
        )
        # No call to tx.sign()
        assert not tx.is_valid()

    def test_tampered_quantity_fails_signature(self, wallets):
        sender, receiver = wallets
        tx = create_transaction(sender, receiver.address, "crude_oil", 9999, "PRODUCTION")
        # Tamper after signing
        tx.quantity_litres = 1
        assert not tx.is_valid()

    def test_wrong_signer_raises(self, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="LPG",
            quantity_litres=200,
            stage="DISTRIBUTION",
        )
        with pytest.raises(ValueError):
            tx.sign(receiver)   # receiver wallet != sender address

    def test_serialization_round_trip(self, wallets):
        sender, receiver = wallets
        tx = create_transaction(sender, receiver.address, "diesel", 750, "DISTRIBUTION")
        tx2 = Transaction.from_dict(tx.to_dict())
        assert tx2.tx_id == tx.tx_id
        assert tx2.is_valid()

    def test_unknown_stage_is_invalid(self, wallets):
        sender, receiver = wallets
        tx = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="water",
            quantity_litres=100,
            stage="SMUGGLING",    # not a valid Stage
        )
        tx.sign(sender)
        assert not tx.is_valid()

    def test_tx_id_deterministic(self, wallets):
        """Two transactions created with same fields at same timestamp have same ID."""
        sender, receiver = wallets
        ts = time.time()
        tx1 = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="petrol",
            quantity_litres=100,
            stage="RETAIL",
            timestamp=ts,
        )
        tx2 = Transaction(
            sender=sender.address,
            sender_pub_key=sender.public_key_hex,
            receiver=receiver.address,
            commodity="petrol",
            quantity_litres=100,
            stage="RETAIL",
            timestamp=ts,
        )
        assert tx1.tx_id == tx2.tx_id


# ── Full supply chain flow test ───────────────────────────────────────────────

class TestSupplyChainFlow:
    def test_full_four_stage_flow(self):
        """
        Simulate a complete petroleum supply chain:
        Producer → Refinery → Distributor → Petrol Station → Customer
        Each leg produces a signed, valid transaction.
        """
        producer    = Wallet()
        refinery    = Wallet()
        distributor = Wallet()
        station     = Wallet()
        customer    = Wallet()

        # Stage 1: Production
        tx1 = create_transaction(producer, refinery.address,    "crude_oil", 10000, Stage.PRODUCTION)
        assert tx1.is_valid()

        # Stage 2: Refinery
        tx2 = create_transaction(refinery, distributor.address, "petrol",     8500, Stage.REFINERY)
        assert tx2.is_valid()

        # Stage 3: Distribution
        tx3 = create_transaction(distributor, station.address,  "petrol",     4000, Stage.DISTRIBUTION)
        assert tx3.is_valid()

        # Stage 4: Retail
        tx4 = create_transaction(station, customer.address,     "petrol",     3800, Stage.RETAIL)
        assert tx4.is_valid()

        # All unique tx_ids
        ids = {tx1.tx_id, tx2.tx_id, tx3.tx_id, tx4.tx_id}
        assert len(ids) == 4
