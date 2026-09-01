"""
demo.py – End-to-end demonstration of the petroleum supply chain blockchain.

This script runs entirely in-process (no subprocesses) to demonstrate:

  1. Four nodes starting up: Producer, Refinery, Distributor, Retailer
  2. Nodes connecting to each other (P2P)
  3. A full supply chain flow:
       Producer  ──(10,000 L crude_oil)──▶ Refinery      [PRODUCTION]
       Refinery  ──(8,500 L petrol)──────▶ Distributor    [REFINERY]
       Distributor──(4,000 L petrol)──────▶ Retailer       [DISTRIBUTION]
       Retailer  ──(3,800 L petrol)──────▶ Customer        [RETAIL]
  4. Each stage mined into a block
  5. Chain printed and validity verified
  6. Custody state derived by replaying the chain
  7. Double-spend defence: three signed-but-fraudulent attacks are refused,
     including a forged block with valid Proof-of-Work gossiped over P2P
  8. Persistence: the ledger is saved, reloaded, re-validated and its custody
     state re-derived from blocks alone

Run with:
    python demo.py
"""

import sys
import os
import time
import json
import threading
import urllib.request
import urllib.error
import logging
logging.basicConfig(level=logging.WARNING)   # silence INFO noise during demo

# Ensure local modules are importable
sys.path.insert(0, os.path.dirname(__file__))

from wallet import Wallet
from transaction import create_transaction, Stage
from blockchain import Blockchain
from p2p_network import P2PNode
from node import Node


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

SEPARATOR = "─" * 60

def header(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def http_post(port: int, path: str, body: dict) -> dict:
    url = f"http://localhost:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def http_get(port: int, path: str) -> dict:
    url = f"http://localhost:{port}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_http(port: int, retries: int = 20) -> None:
    """Poll until the HTTP server at *port* is responsive."""
    for _ in range(retries):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/wallet", timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"Node at port {port} did not start in time.")


def start_node_in_thread(node: Node) -> None:
    """Start a node's HTTP server in a daemon thread."""
    t = threading.Thread(target=node.start, daemon=True)
    t.start()


# ──────────────────────────────────────────────────────────────────────────────
# Main demo
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    DIFFICULTY = 2   # low difficulty for fast demo

    header("1. Creating Wallets & Nodes")

    # Create wallets
    producer_wallet     = Wallet()
    refinery_wallet     = Wallet()
    distributor_wallet  = Wallet()
    retailer_wallet     = Wallet()
    customer_wallet     = Wallet()

    print(f"  Producer    address : {producer_wallet.address[:20]}…")
    print(f"  Refinery    address : {refinery_wallet.address[:20]}…")
    print(f"  Distributor address : {distributor_wallet.address[:20]}…")
    print(f"  Retailer    address : {retailer_wallet.address[:20]}…")
    print(f"  Customer    address : {customer_wallet.address[:20]}…")

    # Only the oil field may bring new petroleum into existence.  This registry
    # is baked into the genesis block, so every node must be given the same
    # list — otherwise their genesis hashes differ and they refuse to sync.
    AUTHORIZED_PRODUCERS = [producer_wallet.address]

    # Create nodes (ports: P2P 6500-6503, HTTP 7500-7503)
    producer_node = Node(
        p2p_port=6500, http_port=7500, role="producer",
        difficulty=DIFFICULTY, wallet=producer_wallet,
        authorized_producers=AUTHORIZED_PRODUCERS,
    )
    refinery_node = Node(
        p2p_port=6501, http_port=7501, role="refinery",
        difficulty=DIFFICULTY, wallet=refinery_wallet,
        authorized_producers=AUTHORIZED_PRODUCERS,
    )
    distributor_node = Node(
        p2p_port=6502, http_port=7502, role="distributor",
        difficulty=DIFFICULTY, wallet=distributor_wallet,
        authorized_producers=AUTHORIZED_PRODUCERS,
    )
    # The retailer needs its own node: a node can only sign with its own wallet,
    # so the RETAIL leg has to be submitted by the retailer itself.
    retailer_node = Node(
        p2p_port=6503, http_port=7503, role="retailer",
        difficulty=DIFFICULTY, wallet=retailer_wallet,
        authorized_producers=AUTHORIZED_PRODUCERS,
    )

    genesis_hashes = {
        n.blockchain.chain[0].hash
        for n in (producer_node, refinery_node, distributor_node, retailer_node)
    }
    print(f"\n  Authorised producer  : {producer_wallet.address[:20]}…")
    print(f"  Shared genesis hash  : {genesis_hashes.pop()[:24]}…")
    assert len(genesis_hashes) == 0, "nodes disagreed on genesis!"
    print("  (every node derived the same genesis independently)")

    header("2. Starting Nodes (P2P + HTTP)")

    start_node_in_thread(producer_node)
    start_node_in_thread(refinery_node)
    start_node_in_thread(distributor_node)
    start_node_in_thread(retailer_node)

    # Wait for all HTTP servers
    for port in (7500, 7501, 7502, 7503):
        wait_for_http(port)
        print(f"  ✓ Node on port {port} is up")

    header("3. Connecting Peers (P2P Ring)")

    # Producer ↔ Refinery
    producer_node.p2p.connect_to_peer("localhost", 6501)
    # Refinery ↔ Distributor
    refinery_node.p2p.connect_to_peer("localhost", 6502)
    # Distributor ↔ Retailer
    distributor_node.p2p.connect_to_peer("localhost", 6503)
    # Retailer ↔ Producer (completes the ring)
    retailer_node.p2p.connect_to_peer("localhost", 6500)

    time.sleep(0.5)   # let handshakes propagate

    for port in (7500, 7501, 7502, 7503):
        info = http_get(port, "/peers")
        print(f"  Node :{port} – peers: {info['count']}")

    header("4. Supply Chain Transactions")

    # Each leg is submitted to the sending participant's OWN node: a node signs
    # only with its own wallet, so the port here determines the actual sender.
    stages = [
        # (sender_node_port, receiver_wallet, commodity, qty, stage, label)
        (7500, refinery_wallet,    "crude_oil", 10000, "PRODUCTION",
         "Producer → Refinery (10,000 L crude oil)"),
        (7501, distributor_wallet, "petrol",     8500, "REFINERY",
         "Refinery → Distributor (8,500 L petrol)"),
        (7502, retailer_wallet,    "petrol",     4000, "DISTRIBUTION",
         "Distributor → Retailer (4,000 L petrol)"),
        (7503, customer_wallet,    "petrol",     3800, "RETAIL",
         "Retailer → Customer (3,800 L petrol)"),
    ]

    for http_port, receiver_w, commodity, qty, stage, label in stages:
        print(f"\n  ▶ {label}")
        # Submit the transaction to the sender's node
        result = http_post(http_port, "/transaction", {
            "receiver": receiver_w.address,
            "commodity": commodity,
            "quantity_litres": qty,
            "stage": stage,
        })
        if result.get("success"):
            print(f"    tx_id : {result['tx_id'][:24]}…  ✓")
        else:
            print(f"    ERROR : {result.get('error')}")

        # Allow gossip to propagate
        time.sleep(0.4)

        # Mine on the sender's node
        print(f"    Mining block on node :{http_port} …", end=" ", flush=True)
        mine_result = http_post(http_port, "/mine", {})
        if mine_result.get("success"):
            blk = mine_result["block"]
            print(f"Block #{blk['index']} nonce={blk['nonce']}  hash={blk['hash'][:16]}… ✓")
        else:
            print(f"FAILED: {mine_result.get('error')}")

        time.sleep(0.5)   # let block gossip propagate

    header("5. Verifying Chain Sync Across All Nodes")

    time.sleep(1.0)
    for port in (7500, 7501, 7502, 7503):
        info = http_get(port, "/chain")
        print(f"  Node :{port} – chain length = {info['length']}  height = {info['height']}")

    header("6. Printing Blockchain (from Producer node)")

    chain_data = http_get(7500, "/chain")
    for block in chain_data["chain"]:
        print(f"\n  Block #{block['index']}")
        print(f"    hash     : {block['hash'][:24]}…")
        print(f"    prev     : {block['previous_hash'][:24]}…")
        print(f"    merkle   : {block['merkle_root'][:24]}…")
        print(f"    nonce    : {block['nonce']}")
        print(f"    tx count : {len(block['transactions'])}")
        for tx in block["transactions"]:
            if tx.get("type") == "PRODUCER_REGISTRY":
                print(
                    f"      [REGISTRY    ] {len(tx['producers'])} authorised "
                    f"producer(s)  tx={tx['tx_id'][:12]}…"
                )
                continue
            print(
                f"      [{tx['stage']:12s}] {tx['commodity']:10s} "
                f"{tx['quantity_litres']:8.0f} L   tx={tx['tx_id'][:12]}…"
            )

    header("7. Custody State (who is holding what)")

    inv = http_get(7500, "/inventory")
    labels = {
        producer_wallet.address:    "Producer   ",
        refinery_wallet.address:    "Refinery   ",
        distributor_wallet.address: "Distributor",
        retailer_wallet.address:    "Retailer   ",
        customer_wallet.address:    "Customer   ",
    }
    print("\n  Balances are never stored — they are replayed from the blocks:\n")
    for address, holdings in inv["balances"].items():
        label = labels.get(address, "Unknown    ")
        for commodity, qty in sorted(holdings.items()):
            print(f"    {label}  {commodity:<10s} {qty:>12,.0f} L")

    header("8. Double-Spend Defence (the attacks the ledger must refuse)")

    print("\n  Each of these is a correctly SIGNED transaction. A signature")
    print("  proves authorisation — it does not conjure the petroleum.\n")

    # Attack 1 – the retailer tries to sell fuel it has already sold.
    retailer_left = inv["balances"].get(retailer_wallet.address, {}).get("petrol", 0)
    attack_1 = http_post(7503, "/transaction", {
        "receiver": customer_wallet.address,
        "commodity": "petrol",
        "quantity_litres": retailer_left + 5_000,
        "stage": "RETAIL",
    })
    print(f"  ▶ Retailer sells {retailer_left + 5_000:,.0f} L holding only "
          f"{retailer_left:,.0f} L")
    print(f"    → {'REJECTED' if not attack_1.get('success') else 'ACCEPTED'}"
          f" – {attack_1.get('error', 'no error!')}")

    # Attack 2 – a wallet nobody authorised tries to mint supply from nothing.
    rogue_wallet = Wallet()
    rogue_node = Node(
        p2p_port=6504, http_port=7504, role="node",
        difficulty=DIFFICULTY, wallet=rogue_wallet,
        authorized_producers=AUTHORIZED_PRODUCERS,
    )
    start_node_in_thread(rogue_node)
    wait_for_http(7504)
    rogue_node.p2p.connect_to_peer("localhost", 6500)
    time.sleep(0.5)

    attack_2 = http_post(7504, "/transaction", {
        "receiver": refinery_wallet.address,
        "commodity": "crude_oil",
        "quantity_litres": 999_999_999,
        "stage": "PRODUCTION",
    })
    print(f"\n  ▶ Unregistered wallet mints 999,999,999 L of crude oil")
    print(f"    → {'REJECTED' if not attack_2.get('success') else 'ACCEPTED'}"
          f" – {attack_2.get('error', 'no error!')}")

    # Attack 3 – forge a block directly and gossip it to the network, skipping
    # the HTTP API entirely.  Proof-of-Work alone must not buy acceptance.
    from blockchain import Block
    forged_tx = create_transaction(
        rogue_wallet, customer_wallet.address, "petrol", 50_000, "RETAIL"
    )
    tip = http_get(7500, "/chain")["chain"][-1]
    forged = Block(
        index=tip["index"] + 1,
        timestamp=time.time(),
        transactions=[forged_tx.to_dict()],
        previous_hash=tip["hash"],
        miner_address=rogue_wallet.address,
    )
    forged.mine(DIFFICULTY)     # real Proof-of-Work on a fraudulent block
    height_before = http_get(7500, "/chain")["height"]
    rogue_node.p2p.broadcast_block(forged.to_dict())
    time.sleep(1.0)
    height_after = http_get(7500, "/chain")["height"]
    print(f"\n  ▶ Rogue node mines a VALID-PoW block containing 50,000 L")
    print(f"    it never received, and gossips it over P2P")
    print(f"    → producer height {height_before} → {height_after} "
          f"({'REJECTED' if height_after == height_before else 'ACCEPTED'})")
    assert height_after == height_before, "forged block was accepted!"

    header("9. Persistence (ledger survives a restart)")

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        chain_file = os.path.join(tmpdir, "chain.json")
        live_chain = http_get(7500, "/chain")

        saved = Blockchain.__new__(Blockchain)
        saved.difficulty = DIFFICULTY
        saved.mempool = []
        saved.chain = [Block.from_dict(b) for b in live_chain["chain"]]
        saved.save(chain_file)
        size_kb = os.path.getsize(chain_file) / 1024
        print(f"\n  Saved ledger to disk           : {size_kb:.1f} KB")

        reloaded = Blockchain.load(chain_file)   # re-validates on load
        print(f"  Reloaded and re-validated      : height {reloaded.height}")
        print(f"  Genesis hash matches           : "
              f"{reloaded.chain[0].hash == live_chain['chain'][0]['hash']}")
        rebuilt = reloaded.custody_state()
        print(f"  Custody re-derived from blocks : "
              f"{rebuilt.balance_of(customer_wallet.address, 'petrol'):,.0f} L "
              f"held by Customer")

    header("10. Chain Validity Check (in-process)")

    bc = Blockchain.__new__(Blockchain)
    bc.difficulty = DIFFICULTY
    bc.mempool = []
    from blockchain import Block
    bc.chain = [Block.from_dict(b) for b in chain_data["chain"]]
    is_valid = bc.is_chain_valid()
    print(f"\n  Chain valid? → {is_valid}")
    assert is_valid, "Chain validation FAILED!"

    print(f"\n{'='*60}")
    print("  Demo complete. All assertions passed. ✓")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
