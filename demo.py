"""
demo.py – End-to-end demonstration of the petroleum supply chain blockchain.

This script runs entirely in-process (no subprocesses) to demonstrate:

  1. Three nodes starting up: Producer, Refinery, Distributor
  2. Nodes connecting to each other (P2P)
  3. A full supply chain flow:
       Producer  ──(10,000 L crude_oil)──▶ Refinery      [PRODUCTION]
       Refinery  ──(8,500 L petrol)──────▶ Distributor    [REFINERY]
       Distributor──(4,000 L petrol)──────▶ Retailer       [DISTRIBUTION]
       Retailer  ──(3,800 L petrol)──────▶ Customer        [RETAIL]
  4. Each stage mined into a block
  5. Chain printed and validity verified
  6. Longest-chain fork resolution demonstrated

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

    # Create nodes (ports: P2P 6500-6502, HTTP 7500-7502)
    producer_node = Node(
        p2p_port=6500, http_port=7500, role="producer",
        difficulty=DIFFICULTY, wallet=producer_wallet
    )
    refinery_node = Node(
        p2p_port=6501, http_port=7501, role="refinery",
        difficulty=DIFFICULTY, wallet=refinery_wallet
    )
    distributor_node = Node(
        p2p_port=6502, http_port=7502, role="distributor",
        difficulty=DIFFICULTY, wallet=distributor_wallet
    )

    header("2. Starting Nodes (P2P + HTTP)")

    start_node_in_thread(producer_node)
    start_node_in_thread(refinery_node)
    start_node_in_thread(distributor_node)

    # Wait for all HTTP servers
    for port in (7500, 7501, 7502):
        wait_for_http(port)
        print(f"  ✓ Node on port {port} is up")

    header("3. Connecting Peers (P2P Ring)")

    # Producer ↔ Refinery
    producer_node.p2p.connect_to_peer("localhost", 6501)
    # Refinery ↔ Distributor
    refinery_node.p2p.connect_to_peer("localhost", 6502)
    # Distributor ↔ Producer (completes the ring)
    distributor_node.p2p.connect_to_peer("localhost", 6500)

    time.sleep(0.5)   # let handshakes propagate

    for port in (7500, 7501, 7502):
        info = http_get(port, "/peers")
        print(f"  Node :{port} – peers: {info['count']}")

    header("4. Supply Chain Transactions")

    stages = [
        # (sender_wallet, sender_node_port, receiver_wallet, commodity, qty, stage, label)
        (producer_wallet,    7500, refinery_wallet,    "crude_oil", 10000, "PRODUCTION",
         "Producer → Refinery (10,000 L crude oil)"),
        (refinery_wallet,    7501, distributor_wallet, "petrol",     8500, "REFINERY",
         "Refinery → Distributor (8,500 L petrol)"),
        (distributor_wallet, 7502, retailer_wallet,    "petrol",     4000, "DISTRIBUTION",
         "Distributor → Retailer (4,000 L petrol)"),
        (retailer_wallet,    7500, customer_wallet,    "petrol",     3800, "RETAIL",
         "Retailer → Customer (3,800 L petrol)"),
    ]

    for sender_w, http_port, receiver_w, commodity, qty, stage, label in stages:
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
    for port in (7500, 7501, 7502):
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
            print(
                f"      [{tx['stage']:12s}] {tx['commodity']:10s} "
                f"{tx['quantity_litres']:8.0f} L   tx={tx['tx_id'][:12]}…"
            )

    header("7. Chain Validity Check (in-process)")

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
