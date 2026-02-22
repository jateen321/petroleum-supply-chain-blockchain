"""
cli.py – Command-line interface for the petroleum supply chain blockchain node.

Usage
-----
  # Start a node
  python cli.py start --p2p-port 5000 --http-port 8000 --role producer

  # Connect to a peer (while another terminal runs the node)
  curl -X POST http://localhost:8000/connect -H "Content-Type: application/json" \\
       -d '{"host":"localhost","port":5001}'

  # Submit a transaction
  python cli.py tx --http 8000 \\
       --to <receiver_address> --commodity crude_oil --qty 5000 --stage PRODUCTION

  # Mine a block
  python cli.py mine --http 8000

  # Print the chain
  python cli.py chain --http 8000

  # Show wallet address
  python cli.py wallet --http 8000
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────────────

def http_get(port: int, path: str) -> dict:
    url = f"http://localhost:{port}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def http_post(port: int, path: str, body: dict) -> dict:
    url = f"http://localhost:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def pretty(d: dict) -> None:
    print(json.dumps(d, indent=2))


# ──────────────────────────────────────────────────────────────────────────────
# Sub-command handlers
# ──────────────────────────────────────────────────────────────────────────────

def cmd_start(args: argparse.Namespace) -> None:
    """Start a blockchain node (blocking)."""
    from node import Node
    from wallet import Wallet

    wallet = None
    if args.wallet_file:
        try:
            wallet = Wallet.load(args.wallet_file)
            print(f"[*] Loaded wallet from {args.wallet_file}")
        except FileNotFoundError:
            wallet = Wallet()
            wallet.save(args.wallet_file)
            print(f"[*] Generated new wallet → saved to {args.wallet_file}")

    node = Node(
        p2p_host="0.0.0.0",
        p2p_port=args.p2p_port,
        http_port=args.http_port,
        role=args.role,
        difficulty=args.difficulty,
        wallet=wallet,
    )

    # Connect to seed peers before starting HTTP (non-blocking P2P is already up)
    if args.peer:
        for peer_str in args.peer:
            h, p = peer_str.split(":")
            print(f"[*] Connecting to peer {h}:{p} …")
            node.p2p.connect_to_peer(h, int(p))

    print(f"\n{'='*55}")
    print(f"  Petroleum Supply Chain Node")
    print(f"  Role     : {args.role}")
    print(f"  Address  : {node.wallet.address}")
    print(f"  P2P port : {args.p2p_port}")
    print(f"  HTTP API : http://localhost:{args.http_port}")
    print(f"{'='*55}\n")

    node.start()   # blocks


def cmd_tx(args: argparse.Namespace) -> None:
    """Submit a supply chain transaction via the HTTP API."""
    body = {
        "receiver": args.to,
        "commodity": args.commodity,
        "quantity_litres": args.qty,
        "stage": args.stage.upper(),
        "notes": args.notes or "",
    }
    try:
        result = http_post(args.http, "/transaction", body)
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_mine(args: argparse.Namespace) -> None:
    """Trigger mining on the node."""
    try:
        result = http_post(args.http, "/mine", {})
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_chain(args: argparse.Namespace) -> None:
    """Print the full blockchain."""
    try:
        result = http_get(args.http, "/chain")
        print(f"\n{'='*55}")
        print(f"  Chain length : {result['length']} blocks (height={result['height']})")
        print(f"{'='*55}")
        for block in result["chain"]:
            print(
                f"\n  Block #{block['index']}  "
                f"hash={block['hash'][:16]}…  "
                f"nonce={block['nonce']}"
            )
            print(f"  miner    : {block.get('miner_address', 'N/A')[:20]}…")
            print(f"  prev     : {block['previous_hash'][:16]}…")
            print(f"  merkle   : {block['merkle_root'][:16]}…")
            print(f"  txs      : {len(block['transactions'])}")
            for tx in block["transactions"]:
                print(
                    f"    ├─ [{tx['stage']:12s}] {tx['commodity']} "
                    f"{tx['quantity_litres']}L  "
                    f"{tx['tx_id'][:10]}…"
                )
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_wallet(args: argparse.Namespace) -> None:
    """Show this node's wallet info."""
    try:
        result = http_get(args.http, "/wallet")
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_peers(args: argparse.Namespace) -> None:
    """List connected peers."""
    try:
        result = http_get(args.http, "/peers")
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_connect(args: argparse.Namespace) -> None:
    """Connect this node to a peer."""
    h, p = args.target.split(":")
    try:
        result = http_post(args.http, "/connect", {"host": h, "port": int(p)})
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


def cmd_mempool(args: argparse.Namespace) -> None:
    """Show unconfirmed transactions."""
    try:
        result = http_get(args.http, "/mempool")
        pretty(result)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Argument parser
# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Petroleum Supply Chain Blockchain CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── start ──────────────────────────────────────────────────────────
    sp = sub.add_parser("start", help="Start a blockchain node")
    sp.add_argument("--p2p-port",  type=int, default=5000, help="P2P TCP port")
    sp.add_argument("--http-port", type=int, default=8000, help="HTTP API port")
    sp.add_argument("--role",      default="node",
                    choices=["producer", "refinery", "distributor", "retailer", "node"],
                    help="Supply chain role of this node")
    sp.add_argument("--difficulty", type=int, default=3, help="PoW difficulty")
    sp.add_argument("--peer",      action="append", metavar="HOST:PORT",
                    help="Seed peer to connect on startup (repeatable)")
    sp.add_argument("--wallet-file", metavar="FILE",
                    help="Path to PEM wallet file (created if missing)")

    # ── tx ─────────────────────────────────────────────────────────────
    sp = sub.add_parser("tx", help="Submit a supply chain transaction")
    sp.add_argument("--http",   type=int, default=8000, help="Node HTTP port")
    sp.add_argument("--to",     required=True, help="Receiver wallet address")
    sp.add_argument("--commodity", required=True, help="e.g. crude_oil, petrol, diesel")
    sp.add_argument("--qty",    type=float, required=True, help="Quantity in litres")
    sp.add_argument("--stage",  required=True,
                    choices=["PRODUCTION","REFINERY","DISTRIBUTION","RETAIL"],
                    help="Supply chain stage")
    sp.add_argument("--notes",  default="", help="Optional annotation")

    # ── mine ───────────────────────────────────────────────────────────
    sp = sub.add_parser("mine", help="Mine a block from the mempool")
    sp.add_argument("--http", type=int, default=8000)

    # ── chain ──────────────────────────────────────────────────────────
    sp = sub.add_parser("chain", help="Print the blockchain")
    sp.add_argument("--http", type=int, default=8000)

    # ── wallet ─────────────────────────────────────────────────────────
    sp = sub.add_parser("wallet", help="Show this node's wallet info")
    sp.add_argument("--http", type=int, default=8000)

    # ── peers ──────────────────────────────────────────────────────────
    sp = sub.add_parser("peers", help="List connected peers")
    sp.add_argument("--http", type=int, default=8000)

    # ── connect ────────────────────────────────────────────────────────
    sp = sub.add_parser("connect", help="Connect this node to a peer")
    sp.add_argument("--http",   type=int, default=8000)
    sp.add_argument("--target", required=True, metavar="HOST:PORT")

    # ── mempool ────────────────────────────────────────────────────────
    sp = sub.add_parser("mempool", help="Show unconfirmed transactions")
    sp.add_argument("--http", type=int, default=8000)

    return parser


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "start":   cmd_start,
        "tx":      cmd_tx,
        "mine":    cmd_mine,
        "chain":   cmd_chain,
        "wallet":  cmd_wallet,
        "peers":   cmd_peers,
        "connect": cmd_connect,
        "mempool": cmd_mempool,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
