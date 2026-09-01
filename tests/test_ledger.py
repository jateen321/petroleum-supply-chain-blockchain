"""
tests/test_ledger.py – Custody / double-spend prevention.

These tests encode the security property that separates this ledger from a
signed append-only log: a valid signature authorises a shipment, it does not
conjure the petroleum being shipped.

The first three tests are the concrete attacks the ledger must refuse.
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from blockchain import Blockchain, registry_record
from ledger import CustodyLedger, CustodyError
from transaction import create_transaction
from wallet import Wallet


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def producer():
    return Wallet()


@pytest.fixture
def chain(producer):
    """Chain of difficulty 2 with a single authorised producer."""
    return Blockchain(difficulty=2, authorized_producers=[producer.address])


@pytest.fixture
def refinery():
    return Wallet()


@pytest.fixture
def distributor():
    return Wallet()


def _produce(chain, producer, receiver, litres=10_000):
    """Mint *litres* of crude oil to *receiver* and confirm it in a block."""
    tx = create_transaction(
        producer, receiver.address, "crude_oil", litres, "PRODUCTION"
    )
    assert chain.add_transaction(tx)
    chain.mine_pending_transactions(producer.address)
    return tx


# ── The attacks ──────────────────────────────────────────────────────────────

class TestDoubleSpendPrevention:
    def test_cannot_ship_same_oil_twice(self, chain, producer, refinery, distributor):
        """The same 10,000 L must not be shippable to two different receivers."""
        _produce(chain, producer, refinery, 10_000)

        first = create_transaction(
            refinery, distributor.address, "petrol", 10_000, "REFINERY"
        )
        assert chain.add_transaction(first), "first shipment should be accepted"

        time.sleep(0.01)  # distinct timestamp => distinct tx_id, so not a replay
        second_receiver = Wallet()
        second = create_transaction(
            refinery, second_receiver.address, "petrol", 10_000, "REFINERY"
        )
        ok, reason = chain.add_transaction_verbose(second)
        assert not ok, "double-spend of the same crude was accepted"
        assert "insufficient custody" in reason

    def test_unauthorised_wallet_cannot_invent_supply(self, chain, refinery):
        """A wallet not in the producer registry cannot mint petroleum."""
        ghost = Wallet()
        tx = create_transaction(
            ghost, refinery.address, "crude_oil", 999_999_999, "PRODUCTION"
        )
        ok, reason = chain.add_transaction_verbose(tx)
        assert not ok, "an unregistered wallet minted supply from nothing"
        assert "unauthorised producer" in reason

    def test_cannot_ship_more_than_received(self, chain, producer, refinery, distributor):
        """A refinery that received 10,000 L cannot ship out 500,000 L."""
        _produce(chain, producer, refinery, 10_000)
        tx = create_transaction(
            refinery, distributor.address, "petrol", 500_000, "REFINERY"
        )
        ok, reason = chain.add_transaction_verbose(tx)
        assert not ok, "refinery shipped more product than it held crude"
        assert "insufficient custody" in reason


# ── Ledger unit behaviour ────────────────────────────────────────────────────

class TestCustodyLedger:
    def test_production_credits_receiver(self):
        p = Wallet()
        led = CustodyLedger([p.address])
        led.apply({
            "stage": "PRODUCTION", "sender": p.address, "receiver": "ref",
            "commodity": "crude_oil", "quantity_litres": 5_000,
        })
        assert led.balance_of("ref", "crude_oil") == 5_000

    def test_refinery_burns_crude_and_credits_product(self):
        p = Wallet()
        led = CustodyLedger([p.address])
        led.apply({
            "stage": "PRODUCTION", "sender": p.address, "receiver": "ref",
            "commodity": "crude_oil", "quantity_litres": 10_000,
        })
        led.apply({
            "stage": "REFINERY", "sender": "ref", "receiver": "dist",
            "commodity": "petrol", "quantity_litres": 8_500,
        })
        assert led.balance_of("ref", "crude_oil") == 1_500
        assert led.balance_of("dist", "petrol") == 8_500

    def test_transfer_moves_exact_commodity(self):
        led = CustodyLedger()
        led._credit("dist", "petrol", 4_000)
        led.apply({
            "stage": "DISTRIBUTION", "sender": "dist", "receiver": "retail",
            "commodity": "petrol", "quantity_litres": 4_000,
        })
        assert led.balance_of("dist", "petrol") == 0
        assert led.balance_of("retail", "petrol") == 4_000

    def test_cannot_transfer_a_commodity_you_do_not_hold(self):
        led = CustodyLedger()
        led._credit("dist", "petrol", 4_000)
        with pytest.raises(CustodyError):
            led.apply({
                "stage": "DISTRIBUTION", "sender": "dist", "receiver": "retail",
                "commodity": "diesel", "quantity_litres": 100,
            })

    def test_shipping_exactly_the_full_balance_succeeds(self):
        """Float accumulation must not make an exact-balance shipment fail."""
        led = CustodyLedger()
        for _ in range(3):
            led._credit("d", "petrol", 0.1)
        led.apply({
            "stage": "RETAIL", "sender": "d", "receiver": "c",
            "commodity": "petrol", "quantity_litres": 0.3,
        })
        assert led.balance_of("c", "petrol") == pytest.approx(0.3)

    def test_copy_is_isolated(self):
        led = CustodyLedger()
        led._credit("a", "petrol", 100)
        clone = led.copy()
        clone._credit("a", "petrol", 900)
        assert led.balance_of("a", "petrol") == 100
        assert clone.balance_of("a", "petrol") == 1_000

    def test_total_supply_tracks_minting(self, ):
        p = Wallet()
        led = CustodyLedger([p.address])
        for _ in range(3):
            led.apply({
                "stage": "PRODUCTION", "sender": p.address, "receiver": "ref",
                "commodity": "crude_oil", "quantity_litres": 1_000,
            })
        assert led.total_supply("crude_oil") == 3_000


# ── Registry is consensus-critical ───────────────────────────────────────────

class TestProducerRegistry:
    def test_registry_is_readable_from_genesis(self, producer):
        bc = Blockchain(difficulty=2, authorized_producers=[producer.address])
        assert bc.authorized_producers == {producer.address}

    def test_different_registry_gives_different_genesis(self):
        a, b = Wallet(), Wallet()
        assert (
            Blockchain(difficulty=2, authorized_producers=[a.address]).chain[0].hash
            != Blockchain(difficulty=2, authorized_producers=[b.address]).chain[0].hash
        )

    def test_same_registry_is_deterministic_across_nodes(self, producer):
        """Two independently constructed nodes must agree on genesis."""
        one = Blockchain(difficulty=2, authorized_producers=[producer.address])
        two = Blockchain(difficulty=2, authorized_producers=[producer.address])
        assert one.chain[0].hash == two.chain[0].hash

    def test_tampering_with_producer_list_is_detected(self, chain):
        """
        Appending yourself to the registry while leaving tx_id intact must be
        caught — the Merkle root only covers tx_ids, so this is exactly the
        tamper that would otherwise slip through.
        """
        attacker = Wallet()
        chain.chain[0].transactions[0]["producers"].append(attacker.address)
        assert not chain.is_chain_valid()

    def test_chain_with_foreign_genesis_is_rejected(self, chain, producer):
        """A longer chain from a different registry must not be adopted."""
        rogue_producer = Wallet()
        rogue = Blockchain(difficulty=2, authorized_producers=[rogue_producer.address])
        victim = Wallet()
        for _ in range(3):
            tx = create_transaction(
                rogue_producer, victim.address, "crude_oil", 1_000, "PRODUCTION"
            )
            rogue.add_transaction(tx)
            rogue.mine_pending_transactions(rogue_producer.address)

        assert len(rogue.chain) > len(chain.chain)
        assert not chain.replace_chain([b.to_dict() for b in rogue.chain])


# ── Chain-level enforcement ──────────────────────────────────────────────────

class TestChainCustodyEnforcement:
    def test_mined_chain_with_valid_flow_is_valid(self, chain, producer, refinery, distributor):
        _produce(chain, producer, refinery, 10_000)
        tx = create_transaction(
            refinery, distributor.address, "petrol", 8_500, "REFINERY"
        )
        chain.add_transaction(tx)
        chain.mine_pending_transactions(refinery.address)
        assert chain.is_chain_valid()

    def test_injected_unbacked_block_invalidates_chain(self, chain, producer, refinery):
        """
        A block whose transactions are all correctly signed but unbacked must
        still fail validation — signatures alone are not enough.
        """
        from blockchain import Block

        thief = Wallet()
        victim = Wallet()
        tx = create_transaction(thief, victim.address, "petrol", 50_000, "RETAIL")
        blk = Block(
            index=len(chain.chain),
            timestamp=time.time(),
            transactions=[tx.to_dict()],
            previous_hash=chain.last_block.hash,
            miner_address=thief.address,
        )
        blk.mine(chain.difficulty)
        chain.chain.append(blk)          # bypass validation, as an attacker would
        assert not chain.is_chain_valid()

    def test_add_block_rejects_unbacked_peer_block(self, chain, refinery):
        """The same block offered over P2P must be refused at the door."""
        from blockchain import Block

        thief = Wallet()
        tx = create_transaction(thief, refinery.address, "petrol", 50_000, "RETAIL")
        blk = Block(
            index=len(chain.chain),
            timestamp=time.time(),
            transactions=[tx.to_dict()],
            previous_hash=chain.last_block.hash,
            miner_address=thief.address,
        )
        blk.mine(chain.difficulty)
        ok, reason = chain.add_block(blk.to_dict())
        assert not ok
        assert "unbacked shipment" in reason
        assert len(chain.chain) == 1

    def test_add_block_accepts_valid_peer_block(self, chain, producer, refinery):
        from blockchain import Block

        tx = create_transaction(
            producer, refinery.address, "crude_oil", 1_000, "PRODUCTION"
        )
        blk = Block(
            index=len(chain.chain),
            timestamp=time.time(),
            transactions=[tx.to_dict()],
            previous_hash=chain.last_block.hash,
            miner_address=producer.address,
        )
        blk.mine(chain.difficulty)
        ok, reason = chain.add_block(blk.to_dict())
        assert ok, reason
        assert chain.height == 1

    def test_conflicting_mempool_entries_cannot_coexist(self, chain, producer, refinery):
        """
        Two shipments that are individually affordable but jointly unaffordable
        must not both sit in the mempool waiting to be mined.
        """
        _produce(chain, producer, refinery, 1_000)

        a = create_transaction(refinery, Wallet().address, "petrol", 800, "REFINERY")
        assert chain.add_transaction(a)
        time.sleep(0.01)
        b = create_transaction(refinery, Wallet().address, "petrol", 800, "REFINERY")
        assert not chain.add_transaction(b)
        assert len(chain.mempool) == 1
