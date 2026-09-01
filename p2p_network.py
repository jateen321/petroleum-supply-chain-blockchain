"""
p2p_network.py – Peer-to-peer TCP networking for the supply chain blockchain.

Architecture
------------
Each node runs a TCP server that listens for incoming peer connections.
Messages are newline-delimited JSON objects sent over persistent TCP sockets.

Message types
-------------
  HANDSHAKE        – sent on connect; shares the sender's P2P port & peer list
  NEW_TRANSACTION  – broadcast an unconfirmed transaction to all peers
  NEW_BLOCK        – broadcast a newly mined block
  REQUEST_CHAIN    – ask a peer for their full blockchain
  CHAIN_RESPONSE   – response to REQUEST_CHAIN

Fork resolution
---------------
On receiving a CHAIN_RESPONSE the node calls Blockchain.replace_chain() which
accepts the incoming chain only if it is longer AND valid.
"""

import json
import secrets
import socket
import threading
import logging
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

MSG_HANDSHAKE = "HANDSHAKE"
MSG_NEW_TX = "NEW_TRANSACTION"
MSG_NEW_BLOCK = "NEW_BLOCK"
MSG_REQUEST_CHAIN = "REQUEST_CHAIN"
MSG_CHAIN_RESPONSE = "CHAIN_RESPONSE"


def make_message(msg_type: str, payload: dict) -> bytes:
    """Serialize a typed message to a newline-terminated JSON bytes object."""
    msg = json.dumps({"type": msg_type, "payload": payload})
    return (msg + "\n").encode("utf-8")


def recv_message(sock: socket.socket) -> Optional[dict]:
    """
    Read one newline-delimited JSON message from *sock*.
    Returns None on connection close or error.
    """
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
            if b"\n" in buf:
                line, _ = buf.split(b"\n", 1)
                return json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, OSError, ConnectionResetError):
        return None


# ---------------------------------------------------------------------------
# P2PNode
# ---------------------------------------------------------------------------

class P2PNode:
    """
    Peer-to-peer TCP server for the blockchain node.

    Parameters
    ----------
    host           : bind address (usually "0.0.0.0")
    port           : P2P listening port
    on_new_tx      : callback(tx_dict)   – called when a NEW_TRANSACTION arrives
    on_new_block   : callback(block_dict) – called when a NEW_BLOCK arrives
    on_chain_req   : callback() -> chain_dict – returns our chain for REQUEST_CHAIN
    on_chain_resp  : callback(chain_list) – called with peer's chain for fork resolution
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_new_tx: Callable[[dict], None],
        on_new_block: Callable[[dict], None],
        on_chain_req: Callable[[], dict],
        on_chain_resp: Callable[[list], None],
    ):
        self.host = host
        self.port = port
        self._on_new_tx = on_new_tx
        self._on_new_block = on_new_block
        self._on_chain_req = on_chain_req
        self._on_chain_resp = on_chain_resp

        # connected peers: (host, port) -> socket
        self._peers: Dict[Tuple[str, int], socket.socket] = {}
        self._peers_lock = threading.Lock()

        # known peer addresses (for handshake gossip)
        self._known_peers: Set[Tuple[str, int]] = set()

        # Random per-node identifier echoed in every handshake.  A node bound to
        # 0.0.0.0 cannot recognise its own address in a gossiped peer list, so
        # it would happily dial itself; seeing our own nonce come back is the
        # only reliable way to detect and drop that self-connection.
        self.node_nonce = secrets.token_hex(8)
        self._self_addrs: Set[Tuple[str, int]] = set()

        self._server_sock: Optional[socket.socket] = None
        self._running = False

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the TCP listener in a background daemon thread."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(32)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        logger.info("P2P server listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect_to_peer(self, host: str, port: int) -> bool:
        """
        Initiate an outbound TCP connection to *host*:*port*.
        Returns True on success.
        """
        # Resolve to an IP first.  Inbound connections are only ever known by
        # their numeric address, so dialling "localhost" while accepting from
        # "127.0.0.1" would otherwise register the same peer under two keys and
        # leave a duplicate socket per pair.
        try:
            host = socket.gethostbyname(host)
        except OSError:
            pass    # unresolvable; fall through and let create_connection report

        key = (host, port)
        with self._peers_lock:
            if key in self._peers:
                return True     # already connected
        try:
            sock = socket.create_connection((host, port), timeout=5)
            # We dialled this address, so we know it is a real listening port
            # and it is safe to gossip onward to other peers.
            self._register_peer(key, sock, listening=True)
            # Send our handshake immediately
            self._send_handshake(sock)
            return True
        except OSError as e:
            logger.warning("Could not connect to peer %s:%d – %s", host, port, e)
            return False

    def _register_peer(
        self, key: Tuple[str, int], sock: socket.socket, listening: bool = False
    ) -> None:
        """
        Track a connected peer.

        *listening* marks whether `key` is the peer's real P2P listening address
        (true for outbound dials and for inbound peers once their handshake has
        told us their real port).  Only such addresses go into `_known_peers`,
        because that set is gossiped to other nodes — publishing the ephemeral
        source port of an inbound socket would send every other node dialling a
        port nobody is listening on.
        """
        with self._peers_lock:
            self._peers[key] = sock
            if listening:
                self._known_peers.add(key)
        # Spawn a reader thread for this peer
        t = threading.Thread(
            target=self._peer_reader, args=(key, sock), daemon=True
        )
        t.start()
        logger.info("Peer connected: %s:%d", *key)

    def _remove_peer(self, key: Tuple[str, int]) -> None:
        with self._peers_lock:
            sock = self._peers.pop(key, None)
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        logger.info("Peer disconnected: %s:%d", *key)

    @property
    def peer_addresses(self) -> List[Tuple[str, int]]:
        with self._peers_lock:
            return list(self._peers.keys())

    # ------------------------------------------------------------------
    # Accept loop
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                # addr[1] is the peer's ephemeral source port, not the port it
                # listens on; the handshake corrects this shortly.
                key = (addr[0], addr[1])
                self._register_peer(key, client_sock, listening=False)
            except OSError:
                break

    # ------------------------------------------------------------------
    # Per-peer reader
    # ------------------------------------------------------------------

    def _peer_reader(self, key: Tuple[str, int], sock: socket.socket) -> None:
        """Read and dispatch messages from one peer in a dedicated thread."""
        try:
            buf = b""
            while self._running:
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                        self._dispatch(msg, key, sock)
                    except json.JSONDecodeError:
                        pass
        finally:
            self._remove_peer(key)

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def _dispatch(
        self, msg: dict, sender_key: Tuple[str, int], sender_sock: socket.socket
    ) -> None:
        msg_type = msg.get("type")
        payload = msg.get("payload", {})

        if msg_type == MSG_HANDSHAKE:
            self._handle_handshake(payload, sender_key, sender_sock)
        elif msg_type == MSG_NEW_TX:
            self._on_new_tx(payload)
        elif msg_type == MSG_NEW_BLOCK:
            self._on_new_block(payload)
        elif msg_type == MSG_REQUEST_CHAIN:
            chain_data = self._on_chain_req()
            self._send_to(sender_sock, MSG_CHAIN_RESPONSE, chain_data)
        elif msg_type == MSG_CHAIN_RESPONSE:
            chain_list = payload.get("chain", [])
            self._on_chain_resp(chain_list)
        else:
            logger.debug("Unknown message type from %s:%d: %s", *sender_key, msg_type)

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    def _send_handshake(self, sock: socket.socket) -> None:
        """Send our P2P address and known peer list to a newly connected peer."""
        with self._peers_lock:
            peers = [{"host": h, "port": p} for h, p in self._known_peers]
        self._send_to(
            sock,
            MSG_HANDSHAKE,
            {"p2p_port": self.port, "nonce": self.node_nonce, "known_peers": peers},
        )

    def _handle_handshake(
        self, payload: dict, sender_key: Tuple[str, int], sock: socket.socket
    ) -> None:
        # Record corrected port from handshake (accept() gives ephemeral port)
        # A handshake carrying our own nonce means we dialled ourselves.
        if payload.get("nonce") == self.node_nonce:
            p2p_port = payload.get("p2p_port")
            if p2p_port:
                self._self_addrs.add((sender_key[0], p2p_port))
            logger.debug("Dropping self-connection from %s:%d", *sender_key)
            self._remove_peer(sender_key)
            return

        p2p_port = payload.get("p2p_port")
        if p2p_port:
            correct_key = (sender_key[0], p2p_port)
            with self._peers_lock:
                if correct_key != sender_key and sender_key in self._peers:
                    self._peers[correct_key] = self._peers.pop(sender_key)
                self._known_peers.discard(sender_key)
                # Now verified: this is where the peer actually listens, so it
                # may be gossiped on.
                self._known_peers.add(correct_key)
            if correct_key != sender_key:
                logger.debug("Corrected peer key to %s:%d", *correct_key)

        # Try to connect to peers we don't know yet
        for peer_info in payload.get("known_peers", []):
            h, p = peer_info.get("host"), peer_info.get("port")
            if h and p:
                key = (h, p)
                with self._peers_lock:
                    known = key in self._peers or key in self._known_peers
                is_self = (h, p) in self._self_addrs or p == self.port
                if not known and not is_self:
                    threading.Thread(
                        target=self.connect_to_peer, args=(h, p), daemon=True
                    ).start()

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    def broadcast(self, msg_type: str, payload: dict) -> None:
        """Send a message to ALL connected peers."""
        data = make_message(msg_type, payload)
        with self._peers_lock:
            peers = list(self._peers.values())
        for sock in peers:
            try:
                sock.sendall(data)
            except OSError:
                pass

    def _send_to(self, sock: socket.socket, msg_type: str, payload: dict) -> None:
        data = make_message(msg_type, payload)
        try:
            sock.sendall(data)
        except OSError as e:
            logger.warning("Send failed: %s", e)

    def request_chain_from_all(self) -> None:
        """Ask all peers to send us their chain (triggers fork resolution)."""
        self.broadcast(MSG_REQUEST_CHAIN, {})

    def broadcast_transaction(self, tx_dict: dict) -> None:
        self.broadcast(MSG_NEW_TX, tx_dict)

    def broadcast_block(self, block_dict: dict) -> None:
        self.broadcast(MSG_NEW_BLOCK, block_dict)
