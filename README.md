# ⛽ Petroleum Supply Chain Blockchain

> A blockchain built **from scratch** in Python — no Ethereum, no Solidity, no magic.
> Just raw cryptography + networking + a ledger that tracks **oil from the ground to your fuel tank**.

---

## 🗺️ What This Project Does

Imagine oil moving through these hands:

```
🛢️  Oil Field (Producer)
        ↓  ships crude oil
🏭  Refinery
        ↓  ships petrol/diesel
🚛  Distributor
        ↓  ships fuel
⛽  Petrol Station
        ↓  sells to
🧑  Customer
```

Every single transfer is recorded as a **signed, tamper-proof transaction** on a **blockchain** shared across a **peer-to-peer (P2P) network**. No one can fake a record. No one can secretly edit a past transfer.

---

## 📁 Project Structure

```
Blockchain/
│
├── utils.py          → SHA-256 hashing + Merkle Tree
├── wallet.py         → Digital wallet (ECDSA keys, sign, verify)
├── transaction.py    → Supply chain transaction model
├── blockchain.py     → Core ledger (blocks + mining + validation)
├── p2p_network.py    → Peer-to-peer TCP networking
├── node.py           → Full node (blockchain + wallet + P2P + HTTP API)
├── cli.py            → Command-line tool to operate nodes
├── demo.py           → End-to-end working demo
│
├── tests/
│   ├── test_blockchain.py   → 16 unit tests
│   └── test_transaction.py  → 20 unit tests
│
├── requirements.txt
└── TUTORIAL.md       ← 📖 READ THIS to understand everything
```

---

## ⚡ Quick Start (5 minutes)

```bash
# 1. Go to project folder
cd /path/to/Blockchain

# 2. Create virtual environment and install dependencies
python3 -m venv .venv
.venv/bin/pip install cryptography pytest

# 3. Run the unit tests (should all pass ✅)
.venv/bin/python -m pytest tests/ -v

# 4. Run the full end-to-end demo
.venv/bin/python demo.py
```

---

## 🖥️ Running a Live Network (3 Terminals)

```bash
# Terminal 1 – Producer node
.venv/bin/python cli.py start --p2p-port 5000 --http-port 8000 --role producer

# Terminal 2 – Refinery node (connects to producer on startup)
.venv/bin/python cli.py start --p2p-port 5001 --http-port 8001 --role refinery --peer localhost:5000

# Get the refinery's wallet address (from Terminal 3)
curl http://localhost:8001/wallet

# Submit a transaction FROM the producer TO the refinery
curl -X POST http://localhost:8000/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "<PASTE_REFINERY_ADDRESS_HERE>",
    "commodity": "crude_oil",
    "quantity_litres": 5000,
    "stage": "PRODUCTION"
  }'

# Mine the transaction into a block
curl -X POST http://localhost:8000/mine

# See the blockchain on both nodes (they should match!)
.venv/bin/python cli.py chain --http 8000
.venv/bin/python cli.py chain --http 8001
```

---

## 📖 Want to Understand How It Works?

Read **[TUTORIAL.md](./TUTORIAL.md)** — it explains every concept from zero:
hashing → wallets → transactions → blocks → mining → P2P networking → fork resolution.

---

## 🧪 Test Results

```
36 passed in 0.77s ✅
```

---

## 🛠️ Tech Stack

| What | How |
|---|---|
| Language | Python 3.10+ |
| Cryptography | ECDSA secp256k1 (`cryptography` library) |
| Consensus | Proof of Work (SHA-256, configurable difficulty) |
| Networking | Raw TCP sockets (`socket` module) |
| API | HTTP REST (`http.server`, zero external deps) |
| Testing | `pytest` |
