"""
ledger.py – Custody state: who is holding how much of what.

Why this module exists
----------------------
Signatures and Proof-of-Work prove that a transaction was *authorised* by its
sender and that history has not been rewritten.  Neither proves that the sender
actually **had** the petroleum they shipped.

Without a custody model a signed ledger still permits:

  * shipping the same 10,000 L to two different refineries (a double-spend),
  * a brand-new wallet conjuring 999,999,999 L out of nothing,
  * a refinery shipping out more product than it ever received.

`CustodyLedger` closes that hole.  It is a *derived* state machine: it holds no
authority of its own, it is replayed from the chain, and any node replaying the
same blocks arrives at exactly the same balances.  This is the supply chain
equivalent of Bitcoin's UTXO set.

Stage semantics
---------------
PRODUCTION    Oil enters the system.  This is the only stage that *mints*
              commodity from nothing, so it is restricted to addresses in the
              producer registry (see `blockchain.Blockchain` – the registry is
              embedded in the genesis block and therefore consensus-critical).

REFINERY      A conversion, not a pure transfer: the refinery burns crude oil
              from its own inventory and produces refined product for the
              receiver.  Modelled here as a 1:1 volume conversion, i.e. you may
              never ship out more product than the crude you consume.  A real
              refinery has per-product yield curves; that is deliberately out of
              scope and documented as a limitation in the README.

DISTRIBUTION  Pure custody transfer.  The sender must already hold at least
RETAIL        `quantity_litres` of exactly that commodity.
"""

from typing import Dict, Iterable, Set, Tuple

# The input commodity a refinery consumes when producing refined product.
REFINERY_INPUT_COMMODITY = "crude_oil"

# Volumes are floats, and repeated debits/credits accumulate representation
# error.  Balance checks are therefore made with a tolerance rather than an
# exact >= comparison, so that shipping out exactly what you hold succeeds.
EPSILON = 1e-9


class CustodyError(Exception):
    """Raised when a transaction cannot be applied to the custody state."""


class CustodyLedger:
    """
    Per-address, per-commodity inventory derived by replaying transactions.

    The ledger is authoritative about *quantities only*.  Signature validity,
    stage validity and quantity sign are enforced by `Transaction.is_valid()`
    before a transaction ever reaches here.
    """

    def __init__(self, authorized_producers: Iterable[str] = ()):
        self.authorized_producers: Set[str] = set(authorized_producers)
        # address -> commodity -> litres held
        self._balances: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------

    def balance_of(self, address: str, commodity: str) -> float:
        """Litres of *commodity* currently held by *address*."""
        return self._balances.get(address, {}).get(commodity, 0.0)

    def inventory_of(self, address: str) -> Dict[str, float]:
        """All non-zero holdings of *address* as {commodity: litres}."""
        return {
            commodity: qty
            for commodity, qty in self._balances.get(address, {}).items()
            if qty > EPSILON
        }

    def all_balances(self) -> Dict[str, Dict[str, float]]:
        """Full custody state, excluding dust/zero entries."""
        return {
            address: inv
            for address in self._balances
            if (inv := self.inventory_of(address))
        }

    def total_supply(self, commodity: str) -> float:
        """Total litres of *commodity* in existence across all holders."""
        return sum(
            holdings.get(commodity, 0.0) for holdings in self._balances.values()
        )

    # ------------------------------------------------------------------
    # Mutating state
    # ------------------------------------------------------------------

    def _credit(self, address: str, commodity: str, qty: float) -> None:
        self._balances.setdefault(address, {})
        self._balances[address][commodity] = (
            self._balances[address].get(commodity, 0.0) + qty
        )

    def _debit(self, address: str, commodity: str, qty: float) -> None:
        held = self.balance_of(address, commodity)
        if held + EPSILON < qty:
            raise CustodyError(
                f"insufficient custody: {address[:12]}… holds "
                f"{held:,.2f} L of {commodity}, tried to move {qty:,.2f} L"
            )
        self._balances[address][commodity] = held - qty

    def check(self, tx: dict) -> Tuple[bool, str]:
        """
        Test whether *tx* would apply cleanly, without mutating state.

        Returns (ok, reason).  `reason` is empty when ok is True.
        """
        try:
            self.copy().apply(tx)
        except CustodyError as e:
            return False, str(e)
        return True, ""

    def apply(self, tx: dict) -> None:
        """
        Apply *tx* to the custody state.

        Raises CustodyError if the sender cannot cover the movement, or if an
        unauthorised address attempts to mint.  The ledger is left unchanged on
        failure only when callers use `check()` first or operate on a `copy()`;
        `apply()` itself validates the debit before crediting, so a partial
        application cannot leave value created out of nothing.
        """
        stage = tx["stage"]
        sender = tx["sender"]
        receiver = tx["receiver"]
        commodity = tx["commodity"]
        qty = float(tx["quantity_litres"])

        if stage == "PRODUCTION":
            # Minting: oil enters the system here and nowhere else.
            if sender not in self.authorized_producers:
                raise CustodyError(
                    f"unauthorised producer: {sender[:12]}… is not in the "
                    f"producer registry and may not create new supply"
                )
            self._credit(receiver, commodity, qty)

        elif stage == "REFINERY":
            # Conversion: burn crude, produce refined product 1:1 by volume.
            self._debit(sender, REFINERY_INPUT_COMMODITY, qty)
            self._credit(receiver, commodity, qty)

        elif stage in ("DISTRIBUTION", "RETAIL"):
            # Pure transfer of an existing holding.
            self._debit(sender, commodity, qty)
            self._credit(receiver, commodity, qty)

        else:
            raise CustodyError(f"unknown stage: {stage!r}")

    # ------------------------------------------------------------------
    # Copying
    # ------------------------------------------------------------------

    def copy(self) -> "CustodyLedger":
        """Deep-enough copy for speculative application (mempool projection)."""
        clone = CustodyLedger(self.authorized_producers)
        clone._balances = {
            addr: dict(holdings) for addr, holdings in self._balances.items()
        }
        return clone

    def __repr__(self) -> str:
        holders = len(self.all_balances())
        return (
            f"CustodyLedger(holders={holders}, "
            f"producers={len(self.authorized_producers)})"
        )
