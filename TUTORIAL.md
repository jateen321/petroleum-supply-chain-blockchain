# 📖 TUTORIAL — Petroleum Supply Chain Blockchain

**Audience:** Friends who know basic Python. No blockchain experience needed.  
**Goal:** By the end, you'll understand every single line of this project.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Hashing — The Foundation of Everything](#2-hashing--the-foundation-of-everything)
3. [The Merkle Tree — Proof of Data Integrity](#3-the-merkle-tree--proof-of-data-integrity)
4. [Wallets & Digital Signatures](#4-wallets--digital-signatures)
5. [Transactions — What Gets Recorded](#5-transactions--what-gets-recorded)
6. [Blocks — Bundling Transactions Together](#6-blocks--bundling-transactions-together)
7. [Proof of Work — Making Blocks Hard to Fake](#7-proof-of-work--making-blocks-hard-to-fake)
8. [The Blockchain — Chaining Blocks Together](#8-the-blockchain--chaining-blocks-together)
9. [The P2P Network — How Nodes Talk to Each Other](#9-the-p2p-network--how-nodes-talk-to-each-other)
10. [Fork Resolution — Longest Chain Wins](#10-fork-resolution--longest-chain-wins)
11. [The Node — Putting It All Together](#11-the-node--putting-it-all-together)
12. [Running It Yourself](#12-running-it-yourself)
13. [Full Supply Chain Flow Summary](#13-full-supply-chain-flow-summary)
14. [Custody — Why Signatures Aren't Enough](#14-custody--why-signatures-arent-enough)
15. [Persistence — Surviving a Restart](#15-persistence--surviving-a-restart)

---

## 1. The Big Picture

### Why do we need a blockchain for oil?

Think about what can go wrong in the petroleum supply chain:
- A refinery **lies** about how many litres it refined.
- A distributor **secretly dilutes** fuel and covers it up.
- A petrol station **claims** it bought certified fuel, but didn't.

With a **traditional database**, whoever controls the database can change any record.

With a **blockchain**:
- Every transfer is a **signed transaction** (like a cheque with a unique signature).
- Transactions are bundled into **blocks**.
- Each block is **mathematically linked** to the one before it — change one thing, and the whole chain breaks.
- The chain lives on **multiple computers simultaneously** (P2P network) — there's no single database to hack.

### The supply chain stages we track:

```
Stage 1: PRODUCTION   → Oil field ships crude oil to refinery
Stage 2: REFINERY     → Refinery ships petrol/diesel to distributor
Stage 3: DISTRIBUTION → Distributor ships fuel to petrol station
Stage 4: RETAIL       → Station sells to customer
```

---

## 2. Hashing — The Foundation of Everything

📄 File: `utils.py`

A **hash function** converts any data → a fixed-size fingerprint.

### Key properties:
| Property | Meaning |
|---|---|
| **Deterministic** | Same input → always same output |
| **One-way** | You can't reverse the hash to get the input |
| **Avalanche effect** | Change 1 character → completely different hash |
| **Collision resistant** | Two different inputs almost never give same hash |

### Example:
```python
from utils import sha256

print(sha256("hello"))
# → 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824

print(sha256("hello!"))  # Just added "!"
# → ce06092fb948d9af71e72b29a33e5efc9a9b0e7d3db6c7e5bdd5c4a3a3a62e  (totally different!)
```

### What we use hashing for:
- **Transaction ID** = hash of the transaction data
- **Block hash** = hash of the block header (index + timestamp + nonce + …)
- **Wallet address** = hash of the public key

### The Merkle Root (double-hashing for blocks):
We use `double_sha256` (hash of hash) for block hashes — same as Bitcoin — for extra security.

---

## 3. The Merkle Tree — Proof of Data Integrity

📄 File: `utils.py` → `merkle_root()`

A **Merkle tree** efficiently proves that a set of transactions hasn't been tampered with.

### How it works (with 4 transactions):

```
Transactions:  TxA    TxB    TxC    TxD
               │      │      │      │
Hash each:    H(A)   H(B)   H(C)   H(D)
               │      │      │      │
Pair & hash: H(AB) = H(H(A)+H(B))   H(CD) = H(H(C)+H(D))
               │                      │
Root:        Merkle Root = H(H(AB) + H(CD))
```

The **Merkle Root** is stored in every block. If even one byte of any transaction changes → the Merkle Root changes → the block hash changes → the blockchain detects tampering.

---

## 4. Wallets & Digital Signatures

📄 File: `wallet.py`

### What's a Wallet?

Each supply chain participant (e.g. ONGC, Indian Oil Refinery, HP Distributor) has a **wallet** — a pair of cryptographic keys.

```
Private Key  →  Keep this SECRET. Used to SIGN transactions.
Public Key   →  Share this freely. Anyone can use it to VERIFY your signature.
Address      →  SHA-256(public key). This is your identity on the chain.
```

### ECDSA — Elliptic Curve Digital Signature Algorithm

We use a specific curve called **secp256k1** (the same one Bitcoin uses).

```python
from wallet import Wallet

# Create a new wallet (generates a random private key)
w = Wallet()

print(w.address)      # "a3f9b2e1..." (64 hex chars) — your blockchain identity
print(w.public_key_hex)  # "02..." (66 hex chars) — compressed public key

# Sign a message
msg = "I am sending 5000 litres of crude oil"
signature = w.sign(msg)

# Verify it
is_valid = Wallet.verify(msg, signature, w.public_key_hex)
print(is_valid)  # True

# If message was tampered:
is_valid = Wallet.verify("I am sending 50000 litres", signature, w.public_key_hex)
print(is_valid)  # False ← signature mismatch!
```

### Why does this matter?

When the oil field submits a transaction saying "I sent 10,000 litres", they **sign** it with their private key.
- Anyone on the network can **verify** the signature using the public key.
- No one can **forge** a transaction on behalf of someone else without their private key.

---

## 5. Transactions — What Gets Recorded

📄 File: `transaction.py`

### Transaction Structure

```python
@dataclass
class Transaction:
    sender          # wallet address of who's sending
    sender_pub_key  # their public key (for signature verification)
    receiver        # wallet address of who's receiving
    commodity       # "crude_oil", "petrol", "diesel", "LPG"
    quantity_litres # how much (must be > 0)
    stage           # "PRODUCTION", "REFINERY", "DISTRIBUTION", "RETAIL"
    timestamp       # when this happened
    tx_id           # SHA-256 fingerprint of the above (auto-computed)
    signature       # ECDSA signature by sender's private key
    notes           # optional field (e.g. batch ID, GPS location)
```

### Creating a transaction (the easy way):

```python
from wallet import Wallet
from transaction import create_transaction

# Each participant has a wallet
oil_field   = Wallet()
refinery    = Wallet()

# Oil field ships crude oil to refinery
tx = create_transaction(
    sender_wallet    = oil_field,
    receiver_address = refinery.address,
    commodity        = "crude_oil",
    quantity_litres  = 10000,
    stage            = "PRODUCTION",
    notes            = "Batch #RJ-2026-01, GPS: 26.9N 70.9E"
)

print(tx.tx_id)      # unique fingerprint of this transfer
print(tx.is_valid()) # True — signature checks out
```

### Validation rules:
- ✅ quantity must be > 0
- ✅ sender ≠ receiver (can't send to yourself)
- ✅ stage must be one of the 4 valid stages
- ✅ signature must match sender's public key

⚠️ These four rules check that a transaction is **well-formed and authorised**.
They do *not* check that the sender actually **had** the oil — that is a separate
layer, and it is the most important one in this whole project.
See [14. Custody — Why Signatures Aren't Enough](#14-custody--why-signatures-arent-enough).

---

## 6. Blocks — Bundling Transactions Together

📄 File: `blockchain.py` → `Block`

Think of a **block** like a page in a ledger. Each page:
- Contains a list of transactions
- Has a unique fingerprint (the block **hash**)
- References the fingerprint of the previous page (the **previous_hash**)

### Block structure:
```python
@dataclass
class Block:
    index           # block number (0, 1, 2, ...)
    timestamp       # when it was created
    transactions    # list of transaction dicts
    previous_hash   # hash of the block before this one
    merkle_root_val # Merkle root of all transaction IDs
    nonce           # magic number found during mining
    hash            # SHA-256 of this block's header (depends on nonce)
    miner_address   # who mined this block
```

### The Genesis Block (Block #0):

```python
# Genesis = the very first block, created when the blockchain starts
# It has no previous block, so previous_hash = "000...0" (64 zeros)
# It carries no transfers — only the PRODUCER REGISTRY: the list of wallets
# allowed to bring new petroleum into existence.
```

Putting the registry *inside* genesis is deliberate. It means the list is covered
by the genesis Merkle root and therefore by the genesis hash, so:
- it cannot be edited without changing the genesis hash and breaking the chain,
- it travels with the chain automatically when a new node syncs,
- two nodes given different producer lists get different genesis hashes and
  simply refuse to talk to each other.

---

## 7. Proof of Work — Making Blocks Hard to Fake

📄 File: `blockchain.py` → `Block.mine()`

### The Problem

If anyone can add a block instantly, they could:
1. Go back to Block #3
2. Insert a fake transaction ("I sent 100,000L crude oil")
3. Recalculate all the hashes from #3 onwards
4. Replace the chain

### The Solution: Make hashing **expensive**

**Proof of Work** requires: find a `nonce` (a number) such that:

```
SHA256(block_header + nonce) starts with N zeros
```

With `difficulty = 3`, you need a hash starting with `"000"`.

Example: `0004b8f2a3c9...` ✅

Since SHA-256 is unpredictable, you have to **guess and check** millions of times.

```python
def mine(self, difficulty: int) -> None:
    target = "0" * difficulty   # e.g. "000"
    self.nonce = 0
    self.hash = self.compute_hash()
    while not self.hash.startswith(target):
        self.nonce += 1          # try again
        self.hash = self.compute_hash()
    # Found! self.hash now starts with "000..."
```

### Why does this protect the chain?

To fake Block #3, an attacker would need to:
1. Redo the PoW for Block #3 (find a valid nonce) — costs time
2. Redo the PoW for Block #4 (which now has a different `previous_hash`) — costs more time
3. Redo the PoW for blocks #5, #6, … all the way to the tip
4. Do all this **faster than the honest network is adding new blocks**

With enough honest miners, this becomes computationally infeasible.

---

## 8. The Blockchain — Chaining Blocks Together

📄 File: `blockchain.py` → `Blockchain`

### How blocks link together:

```
Block #0 (Genesis)         Block #1                Block #2
┌─────────────────┐       ┌─────────────────┐     ┌─────────────────┐
│ prev_hash: 000…│       │ prev_hash: A1B2…│     │ prev_hash: C3D4…│
│ hash: A1B2…    │──────▶│ hash: C3D4…    │────▶│ hash: E5F6…    │
│ txs: []        │       │ txs: [tx1]     │     │ txs: [tx2]     │
└─────────────────┘       └─────────────────┘     └─────────────────┘
```

If someone changes `tx1` in Block #1:
- Block #1's `hash` changes → it no longer matches Block #2's `prev_hash`
- Block #2 is now broken → its hash changes → Block #3 is broken
- The whole chain from Block #1 onwards is invalidated 🚨

### Chain validation checks (all 6 must pass):

```python
def is_chain_valid(self) -> bool:
    for i, block in enumerate(self.chain):
        # 1. Genesis has correct previous_hash (all zeros)
        # 2. Block's stored hash == recomputed hash
        # 3. Block links to previous block correctly
        # 4. Merkle root matches the transactions
        # 5. Hash satisfies PoW difficulty (starts with N zeros)
        # 6. All individual transactions have valid signatures
```

---

## 9. The P2P Network — How Nodes Talk to Each Other

📄 File: `p2p_network.py`

### What is P2P?

In a **peer-to-peer (P2P) network**, every computer is both a **client and a server**. There's no central authority.

Each node:
- Runs a **TCP server** to accept connections from peers
- Maintains open connections to all known peers
- When it learns something new (a transaction, a block), it **broadcasts** to all peers

### Message types:

| Message | When sent | What it carries |
|---|---|---|
| `HANDSHAKE` | On connect | Your P2P port + your known peers (gossip) |
| `NEW_TRANSACTION` | When a valid tx is received | The transaction dict |
| `NEW_BLOCK` | When a block is mined | The block dict |
| `REQUEST_CHAIN` | On startup / when behind | (empty — just a request) |
| `CHAIN_RESPONSE` | Reply to REQUEST_CHAIN | The full blockchain |

### Gossip Protocol:

```
You learn about a new transaction
    ↓
You broadcast it to all your peers
    ↓
Each peer validates it and broadcasts to THEIR peers
    ↓
Within seconds, the whole network knows
```

### Handshake:

When Node A connects to Node B:
- A tells B: "My P2P port is 5000, and I know these other peers: [5001, 5002]"
- B replies with its own known peers
- If B didn't know about 5001, it connects to 5001 as well → **automatic peer discovery**

```python
# In your code, connecting to a peer is one line:
node.p2p.connect_to_peer("localhost", 5001)
```

---

## 10. Fork Resolution — Longest Chain Wins

📄 File: `blockchain.py` → `Blockchain.replace_chain()`

### What is a Fork?

Imagine two miners on the network both find a valid block at the same time.
- Miner A broadcasts Block #5 (version A)
- Miner B broadcasts Block #5 (version B)

Half the network accepts A's block, half accepts B's. The chain has **forked**!

### How we resolve it: **Longest Valid Chain Rule**

The rule is simple:
> If a peer's chain is **longer** AND **valid**, replace ours with theirs.

```python
def replace_chain(self, new_chain_dicts: list) -> bool:
    if len(new_chain_dicts) <= len(self.chain):
        return False             # not longer, ignore
    if not candidate.is_chain_valid():
        return False             # not valid, ignore
    self.chain = candidate       # replace!
    return True
```

Eventually, one branch gets one block ahead. Every node switches to the longer chain. The other branch is discarded. **Consensus is reached automatically.**

---

## 11. The Node — Putting It All Together

📄 File: `node.py`

A **Node** is a running instance that combines:
- A `Blockchain` (the ledger)
- A `Wallet` (the node's identity)  
- A `P2PNode` (networking)
- An `HTTPServer` (REST API for humans/scripts to interact with)
- A `mempool` (pool of unconfirmed transactions waiting to be mined)

### The flow when you submit a transaction via HTTP:

```
curl POST /transaction
    ↓
Node validates the transaction (signature, quantity, stage)
    ↓
Transaction goes into the MEMPOOL
    ↓
Node broadcasts it to all P2P peers
    ↓
(Later) curl POST /mine
    ↓
Node gathers all mempool transactions into a new Block
    ↓
Proof of Work: find nonce until hash starts with "000..."
    ↓
Block is added to the local chain
    ↓
Block is broadcast to all peers via P2P
    ↓
Peers validate and add it to their chains too
```

### HTTP API cheat sheet:

```bash
# See the full blockchain
GET  /chain

# See your wallet address
GET  /wallet

# See unconfirmed transactions
GET  /mempool

# See connected peers
GET  /peers

# Submit a new transaction
POST /transaction   body: {receiver, commodity, quantity_litres, stage}

# Mine a block
POST /mine

# Connect to a peer
POST /connect       body: {host, port}
```

---

## 12. Running It Yourself

### Step 1 — Setup

```bash
cd /path/to/Blockchain
python3 -m venv .venv
source .venv/bin/activate   # on Mac/Linux
.venv/bin/pip install cryptography pytest
```

### Step 2 — Run the automated tests (optional but recommended)

```bash
.venv/bin/python -m pytest tests/ -v
```

You'll see 36 tests like:
```
tests/test_blockchain.py::TestBlock::test_mine_satisfies_difficulty PASSED
tests/test_transaction.py::TestSupplyChainFlow::test_full_four_stage_flow PASSED
```

### Step 3 — Run the in-process demo

```bash
.venv/bin/python demo.py
```

This starts 3 nodes internally, connects them, submits supply chain transactions, mines blocks, and prints the final blockchain. Takes about 10 seconds.

### Step 4 — Run a real 3-terminal network

**Terminal 1** — Producer (oil field):
```bash
.venv/bin/python cli.py start --p2p-port 5000 --http-port 8000 --role producer
```

**Terminal 2** — Refinery:
```bash
.venv/bin/python cli.py start --p2p-port 5001 --http-port 8001 --role refinery --peer localhost:5000
```

**Terminal 3** — Commands:

```bash
# Check both nodes are connected
curl http://localhost:8000/peers
curl http://localhost:8001/peers

# Get refinery's wallet address
curl http://localhost:8001/wallet
# → {"address": "abc123...", ...}

# Submit a transaction FROM producer's node
curl -X POST http://localhost:8000/transaction \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "abc123...",
    "commodity": "crude_oil",
    "quantity_litres": 5000,
    "stage": "PRODUCTION"
  }'

# See the transaction in the mempool
curl http://localhost:8000/mempool

# Mine it!
curl -X POST http://localhost:8000/mine

# Check the chain on BOTH nodes — they should be identical
.venv/bin/python cli.py chain --http 8000
.venv/bin/python cli.py chain --http 8001
```

---

## 13. Full Supply Chain Flow Summary

```
🛢️  Producer creates a Wallet
        ↓  generates private key, public key, address

💧  Crude oil shipment:
    Producer signs a Transaction (PRODUCTION stage, 10,000L crude_oil → Refinery)
        ↓
    Transaction goes into mempool
        ↓
    Mempool is broadcast to all peers
        ↓
    Producer mines Block #1 (PoW: finds valid nonce)
        ↓
    Block #1 is broadcast to all peers
        ↓
    All peers verify and add Block #1 to their chains

⚗️  Refinery refines crude → petrol:
    Refinery signs a Transaction (REFINERY stage, 8,500L petrol → Distributor)
        ↓  (same mining process)
    Block #2 added to all chains

🚛  Distributor ships to petrol station:
    Distributor signs a Transaction (DISTRIBUTION stage, 4,000L petrol → Station)
        ↓  (same mining process)
    Block #3 added to all chains

⛽  Petrol station sells to customer:
    Station signs a Transaction (RETAIL stage, 3,800L petrol → Customer)
        ↓  (same mining process)
    Block #4 added to all chains

✅  Final result:
    Tamper-proof record of 10,000L crude_oil → 8,500L petrol → 
    4,000L at depot → 3,800L to end customer.
    Stored on ALL nodes simultaneously. Immutable. Verified.
```

---

## Key Concepts Cheat Sheet

| Concept | What it is | Where in code |
|---|---|---|
| Hash | SHA-256 fingerprint of data | `utils.py` |
| Merkle Root | Hash of all tx hashes (data integrity) | `utils.py` |
| Wallet | Private/public keypair + your address | `wallet.py` |
| ECDSA | Crypto signature algorithm (secp256k1) | `wallet.py` |
| Transaction | One petroleum transfer, signed | `transaction.py` |
| Block | Batch of transactions + PoW hash | `blockchain.py` |
| Nonce | Magic number found during mining | `blockchain.py` |
| Proof of Work | "Hash must start with N zeros" | `blockchain.py` |
| Mempool | Waiting room for unconfirmed txs | `blockchain.py` |
| Blockchain | Linked list of blocks | `blockchain.py` |
| P2P Network | Nodes talking via TCP sockets | `p2p_network.py` |
| Gossip | Broadcast your knowledge to peers | `p2p_network.py` |
| Fork | Two conflicting chains exist briefly | `blockchain.py` |
| Longest chain | Fork resolution rule | `blockchain.py` |
| Node | One running participant on the network | `node.py` |

---

*Made with ❤️ for CSL7490 – Intro to Blockchain*  
*Every concept here is implemented in the code — go read it!*


---

## 14. Custody — Why Signatures Aren't Enough

This is the most important idea in the project, and the one most from-scratch
blockchain tutorials skip entirely.

### The problem

Imagine every rule from section 5 is satisfied. The transaction is well-formed.
The signature verifies perfectly against the sender's public key. Proof-of-Work
was done honestly. The block hash is valid.

**And the shipment is still completely fraudulent.**

```
Producer ships 10,000 L to Refinery A   ← signed, valid ✅
Producer ships THE SAME 10,000 L to B   ← also signed, also "valid" ✅
```

A signature proves *who authorised* a shipment. It says nothing about whether
that petroleum **existed** or whether the sender **had** it. Without another
layer, our ledger permits:

| Attack | Result |
|---|---|
| **Double-spend** | The same barrels sold twice |
| **Supply inflation** | Any new wallet invents 999,999,999 L from nothing |
| **Phantom stock** | A refinery that got 10,000 L ships out 500,000 L |

This is precisely the problem Bitcoin solves with the **UTXO set** — and it is
the reason a blockchain is more than a signed, append-only log.

### The solution: a derived custody ledger

`ledger.py` maintains **who is holding how much of what**:

```python
balances = {
    refinery_address:    {"crude_oil": 1500.0},
    distributor_address: {"petrol":    4500.0},
}
```

The critical design decision: **these balances are never stored on the chain.**
They are *recomputed* by replaying every confirmed block from genesis. That means:

- Every node independently derives **identical** state from the same blocks.
- There is no separate "balance database" that could drift out of sync or be
  tampered with independently of the chain.
- Restoring a node from disk re-derives balances from scratch — nothing to trust.

### The rules

```
PRODUCTION    Oil ENTERS the system (minting).
              → sender MUST be in the genesis producer registry.
              → credits the receiver.

REFINERY      A CONVERSION, not a transfer.
              → burns crude_oil from the refinery's own inventory,
              → credits the receiver with refined product,
              → output can never exceed the crude consumed.

DISTRIBUTION  A pure transfer.
RETAIL        → sender must ALREADY hold that exact commodity, in that amount.
```

### Where it is enforced

Custody is checked in **four** places, because an attacker can enter at any of them:

1. **Mempool admission** — against confirmed state *plus everything already
   queued*, so two conflicting shipments can't both sit waiting to be mined.
2. **Mining** — transactions that no longer apply are evicted rather than mined
   into an invalid block.
3. **Incoming peer blocks** — Proof-of-Work proves effort was spent, **never**
   that contents are legitimate. Every block from the network is re-validated
   for signatures *and* custody before it is appended.
4. **Full chain validation** — the entire chain is replayed, so a tampered or
   forged history fails even if every individual signature checks out.

Point 3 is the one worth remembering in an interview. `demo.py` mines a
**genuinely valid Proof-of-Work block** containing a fraudulent shipment and
gossips it over the real P2P network — and the network rejects it:

```
▶ Rogue node mines a VALID-PoW block containing 50,000 L
  it never received, and gossips it over P2P
  → producer height 4 → 4 (REJECTED)
```

---

## 15. Persistence — Surviving a Restart

A node that forgets everything when you close the terminal isn't a ledger.

Passing `--data-dir DIR` gives a node two files:

```
DIR/chain.json    the blockchain + mempool
DIR/wallet.pem    this node's private key (chmod 0600)
```

Two details matter more than the file format:

**Writes are atomic.** The chain is written to `chain.json.tmp` and then moved
into place with `os.replace()`, which is atomic on POSIX. A node killed
mid-write leaves the previous good ledger intact rather than a truncated file.

**Loads are re-validated, never trusted.** `Blockchain.load()` runs the full
validation suite — hashes, linkage, Merkle roots, Proof-of-Work, signatures and
a complete custody replay — before the chain is accepted. Hand-edit a stored
ledger to give yourself a million litres and the node refuses to start:

```python
>>> Blockchain.load("chain.json")
ValueError: chain.json: stored chain failed validation
```

Balances are **not** in the file. They are re-derived from the blocks on load,
which is exactly why a tampered file cannot smuggle in fake inventory.
