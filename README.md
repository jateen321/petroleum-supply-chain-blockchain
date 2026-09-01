# ⛽ Petroleum Supply Chain Blockchain

[![CI](https://github.com/jateen321/petroleum-supply-chain-blockchain/actions/workflows/ci.yml/badge.svg)](https://github.com/jateen321/petroleum-supply-chain-blockchain/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

> A blockchain built **from scratch** in Python — no Ethereum, no Solidity, no external frameworks.
> Raw cryptography + networking + a ledger that tracks **oil from the ground to your fuel tank**,
> and refuses to let anyone sell the same barrel twice.

---

## 🗺️ What This Project Does

Oil moves through these hands:

```
🛢️  Oil Field (Producer)
        ↓  ships crude oil                [PRODUCTION]
🏭  Refinery
        ↓  refines → petrol/diesel        [REFINERY]
🚛  Distributor
        ↓  ships fuel                     [DISTRIBUTION]
⛽  Petrol Station
        ↓  sells to                       [RETAIL]
🧑  Customer
```

Every transfer is a **signed, tamper-proof transaction** on a blockchain shared
across a **peer-to-peer network**. No one can fake a record, secretly edit a past
transfer — **or ship petroleum they never received.**

---

## 🔐 The Interesting Part: Signatures Aren't Enough

Most from-scratch blockchain projects stop at "transactions are signed and blocks
are hashed." That is **not sufficient** for a ledger tracking physical goods.

A valid signature proves a shipment was *authorised*. It does **not** prove the
sender actually *had* the oil. Without a state model, a signed ledger still permits:

| Attack | What it means |
|---|---|
| Double-spend | Ship the same 10,000 L to two different refineries |
| Supply inflation | A brand-new wallet conjures 999,999,999 L from nothing |
| Phantom stock | A refinery that received 10,000 L ships out 500,000 L |

This project closes that hole with a **custody ledger** ([`ledger.py`](./ledger.py)) —
the supply chain equivalent of Bitcoin's UTXO set:

- **Balances are never stored.** They are *derived* by replaying confirmed blocks,
  so every node independently computes identical state.
- **Only registered producers may mint supply.** The producer registry is embedded
  in the **genesis block**, so it is covered by the genesis hash, travels with the
  chain during sync, and nodes configured with different registries simply refuse
  to federate.
- **Refining is a conversion, not a transfer.** A refinery *burns* crude from its own
  inventory to produce refined product, and can never output more than it consumed.
- **Peer blocks are re-validated, not trusted.** Proof-of-Work proves effort was
  spent — never that the contents are legitimate. Every incoming block is re-checked
  for signatures *and* custody before it is appended.

The demo mines a **genuinely valid Proof-of-Work block** containing a fraudulent
shipment and gossips it over the real P2P network. The network rejects it.

---

## 📁 Project Structure

```
├── utils.py          → SHA-256 hashing + Merkle Tree
├── wallet.py         → Digital wallet (ECDSA keys, sign, verify)
├── transaction.py    → Supply chain transaction model
├── ledger.py         → Custody state: who holds what (double-spend defence)
├── blockchain.py     → Core ledger (blocks + mining + validation + persistence)
├── p2p_network.py    → Peer-to-peer TCP networking
├── node.py           → Full node (blockchain + wallet + P2P + HTTP API)
├── cli.py            → Command-line tool to operate nodes
├── demo.py           → End-to-end working demo (incl. attack scenarios)
│
├── tests/
│   ├── test_blockchain.py   → Blocks, chain validation, persistence
│   ├── test_transaction.py  → Wallets, signing, transaction model
│   └── test_ledger.py       → Custody, double-spend, registry tampering
│
├── requirements.txt
└── TUTORIAL.md       ← 📖 READ THIS to understand everything
```

---

## ⚡ Quick Start (5 minutes)

```bash
# 1. Create virtual environment and install dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Run the unit tests (should all pass ✅)
.venv/bin/python -m pytest tests/ -v

# 3. Run the full end-to-end demo
.venv/bin/python demo.py
```

The demo starts four real nodes, connects them over TCP, runs the full supply
chain, prints derived custody state, then attempts three attacks and shows each
being refused.

---

## 🖥️ Running a Live Network

Every node must be given the **same** `--authorize` set — it is baked into
genesis, so mismatched registries produce different genesis hashes and the nodes
will not sync.

```bash
# Terminal 1 – Producer node. Note the address it prints.
.venv/bin/python cli.py start --p2p-port 5000 --http-port 8000 --role producer \
    --data-dir ./data/producer

# Copy the producer's address, then restart it (and every other node) with:
#   --authorize <PRODUCER_ADDRESS>

# Terminal 2 – Refinery node
.venv/bin/python cli.py start --p2p-port 5001 --http-port 8001 --role refinery \
    --peer localhost:5000 --authorize <PRODUCER_ADDRESS> --data-dir ./data/refinery

# Get the refinery's wallet address
curl http://localhost:8001/wallet

# Ship crude oil from the producer to the refinery
curl -X POST http://localhost:8000/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "<REFINERY_ADDRESS>",
    "commodity": "crude_oil",
    "quantity_litres": 5000,
    "stage": "PRODUCTION"
  }'

# Mine it into a block
curl -X POST http://localhost:8000/mine

# See the chain and the derived custody state on both nodes (they should match)
.venv/bin/python cli.py chain     --http 8000
.venv/bin/python cli.py inventory --http 8000
.venv/bin/python cli.py inventory --http 8001
```

Because both nodes were started with `--data-dir`, you can **kill them and start
them again** — the ledger, the wallet identity and every balance come back.

### HTTP API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET`  | `/chain` | Full blockchain |
| `GET`  | `/peers` | Connected peers |
| `GET`  | `/wallet` | This node's address |
| `GET`  | `/mempool` | Unconfirmed transactions |
| `GET`  | `/inventory` | Custody state (`?address=` to filter) |
| `POST` | `/transaction` | Submit a shipment |
| `POST` | `/mine` | Mine a block |
| `POST` | `/connect` | Connect to a peer |

---

## 🧪 Tests

```
62 passed ✅
```

Covering hashing and Merkle trees, ECDSA signing, block mining and validation,
chain persistence and reload, fork resolution, and — most importantly — the
custody rules that make double-spending impossible.

---

## 🛠️ Tech Stack

| What | How |
|---|---|
| Language | Python 3.10+ |
| Cryptography | ECDSA secp256k1 (`cryptography` library) |
| Consensus | Proof of Work (SHA-256, configurable difficulty) |
| State model | Derived custody ledger (UTXO-equivalent) |
| Networking | Raw TCP sockets (`socket` module) |
| API | HTTP REST (`http.server`, zero external deps) |
| Persistence | Atomic JSON snapshots, re-validated on load |
| Testing | `pytest`, CI on Python 3.10 / 3.11 / 3.12 |

---

## ⚠️ Known Limitations

This is a **from-scratch educational implementation** built to demonstrate
blockchain mechanics. It is deliberately not production infrastructure, and the
gaps below are known rather than overlooked:

- **Fixed mining difficulty.** There is no retargeting algorithm, so the chain
  offers no real security guarantee against an attacker with meaningful hash
  power. Fork choice is longest-chain rather than most-cumulative-work; these are
  equivalent only because difficulty never changes.
- **No transport security or authentication.** The HTTP API and the P2P protocol
  are both plaintext and unauthenticated. Anyone who can reach a node's port can
  submit transactions. A real deployment would need TLS, authenticated peers, and
  rate limiting.
- **Permissioned by construction.** The producer registry is fixed at genesis and
  cannot be rotated without starting a new chain. Real consortium chains need
  governance for adding and revoking participants.
- **Refining is modelled as a 1:1 volume conversion.** Real refineries have
  per-product yield curves; crude in ≠ petrol out.
- **Single-threaded stdlib HTTP server**, and custody state is recomputed by full
  replay rather than maintained incrementally — both fine at demo scale, neither
  appropriate under load.
- **No peer discovery beyond handshake gossip**, and no defence against Sybil or
  eclipse attacks.

---

## 📖 Want to Understand How It Works?

Read **[TUTORIAL.md](./TUTORIAL.md)** — it explains every concept from zero:
hashing → wallets → transactions → blocks → mining → P2P networking → fork resolution.
