"""
cli.py – Command-line interface for the petroleum supply chain blockchain node.

Usage
-----
  # Start a node.  --authorize pins which wallets may mint new supply; it is
  # baked into genesis, so every node in one network must be given the same set.
  python cli.py start --p2p-port 5000 --http-port 8000 --role producer \\
       --authorize <producer_address> --data-dir ./data/producer

  # Show custody state (who is holding what, replayed from the chain)
  python cli.py inventory --http 8000

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
        authorized_producers=args.authorize or [],
        data_dir=args.data_dir,
    )

    # Connect to seed peers before starting HTTP (non-blocking P2P is already up)
    if args.peer:
        for peer_str in args.peer:
            h, p = peer_str.split(":")
            print(f"[*] Connecting to peer {h}:{p} …")
            node.p2p.connect_to_peer(h, int(p))

    producers = sorted(node.blockchain.authorized_producers)
    print(f"\n{'='*55}")
    print(f"  Petroleum Supply Chain Node")
    print(f"  Role     : {args.role}")
    print(f"  Address  : {node.wallet.address}")
    print(f"  P2P port : {args.p2p_port}")
    print(f"  HTTP API : http://localhost:{args.http_port}")
    print(f"  Genesis  : {node.blockchain.chain[0].hash[:24]}…")
    print(f"  Height   : {node.blockchain.height}")
    if args.data_dir:
        print(f"  Data dir : {args.data_dir}")
    if producers:
        print(f"  Producers authorised to mint supply:")
        for addr in producers:
            print(f"    • {addr}")
    else:
        print(f"  Producers: none authorised — PRODUCTION will be rejected.")
        print(f"             Pass --authorize <address> on every node.")
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


def cmd_inventory(args: argparse.Namespace) -> None:
    """Show who currently holds how much of what, replayed from the chain."""
    try:
        path = "/inventory"
        if args.address:
            path += f"?address={args.address}"
        result = http_get(args.http, path)
    except urllib.error.URLError as e:
        print(f"[!] Error: {e.reason}")
        sys.exit(1)

    if not result.get("success"):
        print(f"[!] {result.get('error')}")
        sys.exit(1)

    if args.address:
        print(f"\n  {args.address[:24]}…")
        inv = result["inventory"]
        if not inv:
            print("    (holds nothing)")
        for commodity, qty in sorted(inv.items()):
            print(f"    {commodity:<12s} {qty:>14,.2f} L")
        print()
        return

    balances = result["balances"]
    producers = set(result.get("authorized_producers", []))
    print(f"\n{'='*55}")
    print("  Custody state (derived from confirmed blocks)")
    print(f"{'='*55}")
    if not balances:
        print("\n  (no petroleum in the system yet)\n")
        return
    for address, inv in sorted(balances.items()):
        tag = "  [authorised producer]" if address in producers else ""
        print(f"\n  {address[:24]}…{tag}")
        for commodity, qty in sorted(inv.items()):
            print(f"    {commodity:<12s} {qty:>14,.2f} L")
    print()


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
    sp.add_argument("--authorize", action="append", metavar="ADDRESS",
                    help="Wallet address permitted to mint new supply "
                         "(repeatable). Baked into genesis, so EVERY node in "
                         "the network must be given the same set or their "
                         "genesis hashes differ and they will not sync.")
    sp.add_argument("--data-dir", metavar="DIR",
                    help="Directory to persist the ledger and wallet key, so "
                         "the node survives a restart with its history intact")

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

    # ── inventory ──────────────────────────────────────────────────────
    sp = sub.add_parser("inventory",
                        help="Show custody state (who holds what) from the chain")
    sp.add_argument("--http", type=int, default=8000)
    sp.add_argument("--address", help="Show only this address's holdings")

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
        "inventory": cmd_inventory,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
