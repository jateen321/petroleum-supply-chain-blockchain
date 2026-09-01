"""
node.py – Full blockchain node: combines Blockchain + Wallet + P2P + HTTP API.

Each node represents a supply chain participant (Producer, Refinery,
Distributor, or Petrol Station).  It exposes:

  HTTP REST API (via http.server, no external deps):
    GET  /chain           – return full blockchain + mempool as JSON
    GET  /peers           – list connected peer addresses
    GET  /wallet          – return this node's wallet address
    GET  /mempool         – unconfirmed transactions
    GET  /inventory       – custody state replayed from the chain
                            (optionally ?address=<hex>)
    POST /transaction     – submit a new supply chain transaction
    POST /mine            – mine a block from current mempool
    POST /connect         – connect to a peer node

  P2P TCP layer (via p2p_network.P2PNode):
    - Gossip transactions and blocks to all peers
    - Request chain on startup to sync
"""

import json
import logging
import threading
import time
import sys
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import urlparse, parse_qs

from blockchain import Blockchain
from transaction import Transaction, create_transaction, Stage
from wallet import Wallet
from p2p_network import (
    P2PNode,
    MSG_NEW_TX,
    MSG_NEW_BLOCK,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ──────────────────────────────────────────────────────────────────────────────
# Node
# ──────────────────────────────────────────────────────────────────────────────

class Node:
    """
    A fully-fledged supply chain blockchain node.

    Parameters
    ----------
    p2p_host    : host for P2P TCP server (default "0.0.0.0")
    p2p_port    : port for P2P TCP server
    http_port   : port for HTTP REST API
    role        : human-readable participant role (e.g. "producer")
    difficulty  : PoW difficulty
    wallet      : optional pre-existing Wallet; new one generated if None
    authorized_producers : addresses allowed to mint new supply.  Baked into
                  genesis, so every node in a network must be given the same
                  list or their genesis hashes will differ and they will not sync.
    data_dir    : directory for the persisted ledger and wallet key.  When set,
                  the node survives a restart with the same identity and history.
    """

    def __init__(
        self,
        p2p_host: str = "0.0.0.0",
        p2p_port: int = 5000,
        http_port: int = 8000,
        role: str = "node",
        difficulty: int = 3,
        wallet: Optional[Wallet] = None,
        authorized_producers: Optional[list] = None,
        data_dir: Optional[str] = None,
    ):
        self.role = role
        self.p2p_port = p2p_port
        self.http_port = http_port
        self.data_dir = data_dir
        self.chain_path = os.path.join(data_dir, "chain.json") if data_dir else None
        self.wallet_path = os.path.join(data_dir, "wallet.pem") if data_dir else None

        if data_dir:
            os.makedirs(data_dir, exist_ok=True)

        # Wallet – reuse the stored key so a restarted node keeps its identity
        # (and therefore its custody balances) instead of becoming a stranger.
        self.wallet = wallet or self._load_or_create_wallet()
        logger.info("Wallet address: %s (role=%s)", self.wallet.address, role)

        # Blockchain – restored from disk when available
        self.blockchain = self._load_or_create_chain(
            difficulty, authorized_producers or []
        )

        # P2P layer
        self.p2p = P2PNode(
            host=p2p_host,
            port=p2p_port,
            on_new_tx=self._handle_new_tx,
            on_new_block=self._handle_new_block,
            on_chain_req=self._handle_chain_request,
            on_chain_resp=self._handle_chain_response,
        )

        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_or_create_wallet(self) -> Wallet:
        if self.wallet_path and os.path.exists(self.wallet_path):
            logger.info("Loaded wallet from %s", self.wallet_path)
            return Wallet.load(self.wallet_path)
        wallet = Wallet()
        if self.wallet_path:
            wallet.save(self.wallet_path)
            os.chmod(self.wallet_path, 0o600)   # private key: owner-only
            logger.info("Generated and saved new wallet to %s", self.wallet_path)
        return wallet

    def _load_or_create_chain(
        self, difficulty: int, authorized_producers: list
    ) -> Blockchain:
        if self.chain_path and os.path.exists(self.chain_path):
            try:
                chain = Blockchain.load(self.chain_path)
                logger.info(
                    "Restored ledger from %s (height=%d)",
                    self.chain_path,
                    chain.height,
                )
                requested = set(authorized_producers)
                if requested and requested != chain.authorized_producers:
                    logger.warning(
                        "--authorize was given but the stored genesis pins a "
                        "different producer registry; the stored chain wins. "
                        "Delete %s to start a network with a new registry.",
                        self.chain_path,
                    )
                return chain
            except (ValueError, OSError, json.JSONDecodeError) as e:
                logger.error(
                    "Stored ledger at %s is unusable (%s) – refusing to start "
                    "on a corrupt chain.",
                    self.chain_path,
                    e,
                )
                raise

        return Blockchain(
            difficulty=difficulty, authorized_producers=authorized_producers
        )

    def _persist(self) -> None:
        """Write the ledger to disk if this node has a data directory."""
        if not self.chain_path:
            return
        try:
            self.blockchain.save(self.chain_path)
        except OSError as e:
            logger.error("Failed to persist ledger to %s: %s", self.chain_path, e)

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start P2P server and HTTP server (blocks on HTTP)."""
        self.p2p.start()
        # Request chain from peers after a short delay to let connections form
        threading.Timer(2.0, self.p2p.request_chain_from_all).start()
        self._start_http()

    def _start_http(self) -> None:
        node_ref = self  # closure

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                logger.debug("HTTP %s - %s", self.address_string(), format % args)

            # ---- routing ----------------------------------------
            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/chain":
                    self._respond(node_ref.api_get_chain())
                elif path == "/peers":
                    self._respond(node_ref.api_get_peers())
                elif path == "/wallet":
                    self._respond(node_ref.api_get_wallet())
                elif path == "/mempool":
                    self._respond(node_ref.api_get_mempool())
                elif path == "/inventory":
                    address = parse_qs(parsed.query).get("address", [None])[0]
                    self._respond(node_ref.api_get_inventory(address))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                if path == "/transaction":
                    self._respond(node_ref.api_post_transaction(body))
                elif path == "/mine":
                    self._respond(node_ref.api_post_mine())
                elif path == "/connect":
                    self._respond(node_ref.api_post_connect(body))
                else:
                    self.send_response(404)
                    self.end_headers()

            def _respond(self, data: dict, status: int = 200):
                body = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("0.0.0.0", self.http_port), Handler)
        logger.info(
            "HTTP API listening on http://localhost:%d  (role=%s)",
            self.http_port,
            self.role,
        )
        server.serve_forever()

    # ------------------------------------------------------------------
    # P2P event handlers
    # ------------------------------------------------------------------

    def _handle_new_tx(self, tx_dict: dict) -> None:
        with self._lock:
            added = self.blockchain.add_transaction(tx_dict)
        if added:
            logger.info("Relayed tx %s…", tx_dict.get("tx_id", "?")[:10])
            # Relay to other peers (gossip)
            self.p2p.broadcast_transaction(tx_dict)

    def _handle_new_block(self, block_dict: dict) -> None:
        with self._lock:
            # If the block is ahead of our tip we are simply behind; ask for the
            # full chain rather than rejecting it outright.
            if block_dict.get("index", -1) > len(self.blockchain.chain):
                self.p2p.request_chain_from_all()
                return

            # Full validation: PoW, linkage, signatures AND custody.  Mining
            # effort alone is never sufficient reason to accept a peer's block.
            accepted, reason = self.blockchain.add_block(block_dict)
            if not accepted:
                if reason in ("does not extend our tip",
                              "previous_hash does not match our tip"):
                    self.p2p.request_chain_from_all()
                else:
                    logger.warning(
                        "Rejected block #%s from peer: %s",
                        block_dict.get("index"), reason,
                    )
                return

            logger.info(
                "Accepted block #%d (%s…) from peer",
                block_dict["index"],
                block_dict["hash"][:12],
            )
            self._persist()
        # Relay
        self.p2p.broadcast_block(block_dict)

    def _handle_chain_request(self) -> dict:
        with self._lock:
            return self.blockchain.to_dict()

    def _handle_chain_response(self, chain_list: list) -> None:
        with self._lock:
            replaced = self.blockchain.replace_chain(chain_list)
            if replaced:
                self._persist()
        if replaced:
            logger.info(
                "Chain replaced via fork resolution – new height %d",
                self.blockchain.height,
            )

    # ------------------------------------------------------------------
    # HTTP API handlers
    # ------------------------------------------------------------------

    def api_get_chain(self) -> dict:
        with self._lock:
            return {
                "chain": [b.to_dict() for b in self.blockchain.chain],
                "length": len(self.blockchain.chain),
                "height": self.blockchain.height,
            }

    def api_get_peers(self) -> dict:
        peers = self.p2p.peer_addresses
        return {
            "peers": [{"host": h, "port": p} for h, p in peers],
            "count": len(peers),
        }

    def api_get_wallet(self) -> dict:
        return {
            "address": self.wallet.address,
            "public_key": self.wallet.public_key_hex,
            "role": self.role,
        }

    def api_get_mempool(self) -> dict:
        with self._lock:
            return {
                "mempool": self.blockchain.mempool,
                "count": len(self.blockchain.mempool),
            }

    def api_post_transaction(self, body: dict) -> dict:
        """
        Expected body:
          {
            "receiver"        : "<address hex>",
            "commodity"       : "crude_oil" | "petrol" | "diesel" | "LPG",
            "quantity_litres" : 5000,
            "stage"           : "PRODUCTION" | "REFINERY" | "DISTRIBUTION" | "RETAIL",
            "notes"           : "(optional)"
          }
        """
        required = {"receiver", "commodity", "quantity_litres", "stage"}
        missing = required - body.keys()
        if missing:
            return {"success": False, "error": f"Missing fields: {missing}"}

        try:
            tx = create_transaction(
                sender_wallet=self.wallet,
                receiver_address=body["receiver"],
                commodity=body["commodity"],
                quantity_litres=float(body["quantity_litres"]),
                stage=body["stage"],
                notes=body.get("notes", ""),
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

        with self._lock:
            added, reason = self.blockchain.add_transaction_verbose(tx)
        if added:
            self.p2p.broadcast_transaction(tx.to_dict())
            return {"success": True, "tx_id": tx.tx_id, "transaction": tx.to_dict()}
        return {"success": False, "error": reason}

    def api_post_mine(self) -> dict:
        with self._lock:
            if not self.blockchain.mempool:
                return {"success": False, "error": "Mempool is empty – nothing to mine"}
            try:
                block = self.blockchain.mine_pending_transactions(self.wallet.address)
            except Exception as e:
                return {"success": False, "error": str(e)}
            self._persist()
        logger.info("Mined block #%d hash=%s…", block.index, block.hash[:12])
        self.p2p.broadcast_block(block.to_dict())
        return {"success": True, "block": block.to_dict()}

    def api_get_inventory(self, address: Optional[str] = None) -> dict:
        """
        Current custody state derived from the chain.

        With no address, returns every holder's inventory; with one, just that
        holder's.  This is the endpoint that makes the ledger's whole point
        visible: balances are never stored, only replayed from confirmed blocks.
        """
        with self._lock:
            try:
                ledger = self.blockchain.custody_state()
            except Exception as e:
                return {"success": False, "error": str(e)}

            if address:
                return {
                    "success": True,
                    "address": address,
                    "inventory": ledger.inventory_of(address),
                }
            return {
                "success": True,
                "balances": ledger.all_balances(),
                "authorized_producers": sorted(self.blockchain.authorized_producers),
            }

    def api_post_connect(self, body: dict) -> dict:
        host = body.get("host", "localhost")
        port = body.get("port")
        if not port:
            return {"success": False, "error": "Missing 'port'"}
        ok = self.p2p.connect_to_peer(host, int(port))
        if ok:
            self.p2p.request_chain_from_all()
        return {"success": ok, "peer": f"{host}:{port}"}
