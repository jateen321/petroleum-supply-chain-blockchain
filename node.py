"""
node.py – Full blockchain node: combines Blockchain + Wallet + P2P + HTTP API.

Each node represents a supply chain participant (Producer, Refinery,
Distributor, or Petrol Station).  It exposes:

  HTTP REST API (via http.server, no external deps):
    GET  /chain           – return full blockchain + mempool as JSON
    GET  /peers           – list connected peer addresses
    GET  /wallet          – return this node's wallet address
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
from urllib.parse import urlparse

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
    """

    def __init__(
        self,
        p2p_host: str = "0.0.0.0",
        p2p_port: int = 5000,
        http_port: int = 8000,
        role: str = "node",
        difficulty: int = 3,
        wallet: Optional[Wallet] = None,
    ):
        self.role = role
        self.p2p_port = p2p_port
        self.http_port = http_port

        # Wallet
        self.wallet = wallet or Wallet()
        logger.info("Wallet address: %s (role=%s)", self.wallet.address, role)

        # Blockchain
        self.blockchain = Blockchain(difficulty=difficulty)

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
                path = urlparse(self.path).path
                if path == "/chain":
                    self._respond(node_ref.api_get_chain())
                elif path == "/peers":
                    self._respond(node_ref.api_get_peers())
                elif path == "/wallet":
                    self._respond(node_ref.api_get_wallet())
                elif path == "/mempool":
                    self._respond(node_ref.api_get_mempool())
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
        from blockchain import Block
        with self._lock:
            # Only accept if it extends our chain
            if block_dict.get("index") != len(self.blockchain.chain):
                # We might be behind – request the full chain
                self.p2p.request_chain_from_all()
                return
            # Validate hash satisfies PoW
            candidate = Block.from_dict(block_dict)
            target = "0" * self.blockchain.difficulty
            if not candidate.hash.startswith(target):
                logger.warning("Received block with invalid PoW – rejected")
                return
            if candidate.previous_hash != self.blockchain.last_block.hash:
                self.p2p.request_chain_from_all()
                return
            # Remove confirmed txs from mempool
            confirmed_ids = {tx["tx_id"] for tx in block_dict.get("transactions", [])}
            self.blockchain.mempool = [
                t for t in self.blockchain.mempool
                if t["tx_id"] not in confirmed_ids
            ]
            self.blockchain.chain.append(candidate)
            logger.info(
                "Accepted block #%d (%s…) from peer",
                block_dict["index"],
                block_dict["hash"][:12],
            )
        # Relay
        self.p2p.broadcast_block(block_dict)

    def _handle_chain_request(self) -> dict:
        with self._lock:
            return self.blockchain.to_dict()

    def _handle_chain_response(self, chain_list: list) -> None:
        with self._lock:
            replaced = self.blockchain.replace_chain(chain_list)
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
            added = self.blockchain.add_transaction(tx)
        if added:
            self.p2p.broadcast_transaction(tx.to_dict())
            return {"success": True, "tx_id": tx.tx_id, "transaction": tx.to_dict()}
        return {"success": False, "error": "Transaction rejected (duplicate or invalid)"}

    def api_post_mine(self) -> dict:
        with self._lock:
            if not self.blockchain.mempool:
                return {"success": False, "error": "Mempool is empty – nothing to mine"}
            try:
                block = self.blockchain.mine_pending_transactions(self.wallet.address)
            except Exception as e:
                return {"success": False, "error": str(e)}
        logger.info("Mined block #%d hash=%s…", block.index, block.hash[:12])
        self.p2p.broadcast_block(block.to_dict())
        return {"success": True, "block": block.to_dict()}

    def api_post_connect(self, body: dict) -> dict:
        host = body.get("host", "localhost")
        port = body.get("port")
        if not port:
            return {"success": False, "error": "Missing 'port'"}
        ok = self.p2p.connect_to_peer(host, int(port))
        if ok:
            self.p2p.request_chain_from_all()
        return {"success": ok, "peer": f"{host}:{port}"}
