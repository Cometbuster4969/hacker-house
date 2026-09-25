"""Data store: the full 590,742-transaction graph neighbourhood, as columnar tables.

Two build paths, same output (``data/store/tx.parquet``):

1. Official dataset (``data/HHGOA_IEEE/transactions.csv`` + ``identity.csv``) — the
   card_id is derived exactly like the organisers' case pack: one ``Kn`` per distinct
   (card2..card6) combination within a customer, numbered by first appearance.
2. A prepared column extract of the same official files
   (``data/HHGOA_IEEE/prepared/transactions_core.csv``, ``transactions_signals.csv``,
   ``identity_signals.csv``) — used when the 708 MB raw file is not available.

The store is the local mirror of the TigerGraph graph (same vertex/edge semantics);
``InvestigationStore`` answers the same questions the installed GSQL queries answer,
always bounded by ``as_of`` so an investigation can never see the future.
"""
from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)

SIG_COLS = [f"C{i}" for i in range(1, 15)] + [f"D{i}" for i in range(1, 16)] + [f"M{i}" for i in range(1, 10)]
ID_COLS = ["id_15", "id_23", "id_34", "DeviceType"]


def _device_key(row) -> str:
    vals = []
    for c in ("DeviceInfo", "id_30", "id_31", "id_33"):
        v = row.get(c)
        vals.append(v.strip() if isinstance(v, str) and v.strip() else "UNK")
    return "" if all(v == "UNK" for v in vals) else "|".join(vals)


def _build_from_raw(raw_dir: Path) -> pd.DataFrame:
    core_cols = ["TransactionID", "TransactionDT", "TransactionAmt", "ProductCD", "card2", "card3",
                 "card4", "card5", "card6", "customer_id", "ts", "channel", "risk_score", "addr1",
                 "addr2", "dist1", "dist2", "P_emaildomain", "R_emaildomain"] + SIG_COLS
    parts = []
    for ch in pd.read_csv(raw_dir / "transactions.csv", usecols=core_cols, chunksize=100_000,
                          dtype={"TransactionID": str, "addr1": str, "addr2": str}):
        parts.append(ch)
    df = pd.concat(parts, ignore_index=True)
    key = df[["card2", "card3", "card4", "card5", "card6"]].astype(str).agg("|".join, axis=1)
    df["_ck"] = key
    first = df.drop_duplicates(["customer_id", "_ck"])[["customer_id", "_ck"]].copy()
    first["k"] = first.groupby("customer_id").cumcount() + 1
    df = df.merge(first, on=["customer_id", "_ck"], how="left")
    df["card_id"] = df.customer_id + "-K" + df.k.astype(str)
    idt = pd.read_csv(raw_dir / "identity.csv", dtype={"TransactionID": str},
                      usecols=["TransactionID", "id_15", "id_23", "id_30", "id_31", "id_33", "id_34",
                               "DeviceType", "DeviceInfo"])
    idt["device_profile_id"] = idt.apply(_device_key, axis=1).replace("", np.nan)
    df = df.merge(idt[["TransactionID", "device_profile_id"] + ID_COLS], on="TransactionID", how="left")
    df["has_device_record"] = df.device_profile_id.notna()
    return df.drop(columns=["_ck", "k", "card2", "card3", "card4", "card5", "card6"])


def _build_from_prepared(pdir: Path) -> pd.DataFrame:
    core = pd.read_csv(pdir / "transactions_core.csv", dtype={"TransactionID": str, "addr1": str, "addr2": str})
    idt = pd.read_csv(pdir / "identity_signals.csv", dtype={"TransactionID": str})
    sig = pd.read_csv(pdir / "transactions_signals.csv", dtype={"TransactionID": str})
    df = core.merge(idt.drop(columns=["device_profile_id"], errors="ignore"), on="TransactionID", how="left")
    return df.merge(sig, on="TransactionID", how="left")


def build_store(force: bool = False) -> Path:
    out = config.STORE_DIR / "tx.parquet"
    if out.exists() and not force:
        return out
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    if (config.DATA_DIR / "transactions.csv").exists():
        log.info("Building store from official transactions.csv/identity.csv")
        df = _build_from_raw(config.DATA_DIR)
    elif (config.PREPARED_DIR / "transactions_core.csv").exists():
        log.info("Building store from prepared extract in %s", config.PREPARED_DIR)
        df = _build_from_prepared(config.PREPARED_DIR)
    else:
        raise FileNotFoundError(
            "No transaction data. Put transactions.csv + identity.csv in data/HHGOA_IEEE/ "
            "or run scripts/fetch_prepared_data.sh")
    df["ts"] = pd.to_datetime(df["ts"])
    df["addr1"] = df["addr1"].astype("string").str.replace(r"\.0$", "", regex=True)
    df = df.sort_values("ts").reset_index(drop=True)
    df.to_parquet(out)
    log.info("Store written: %s rows -> %s", len(df), out)
    return out


def load_closed_cases() -> pd.DataFrame:
    cc = pd.read_csv(config.DATA_DIR / "closed_cases_history.csv", dtype=str)
    for c in ("opened_at", "closed_at"):
        cc[c] = pd.to_datetime(cc[c])
    cc["exposure_usd"] = cc["exposure_usd"].astype(float)
    cc["n_txns"] = cc["n_txns"].astype(int)
    return cc


def load_case_pack() -> pd.DataFrame:
    cp = pd.read_csv(config.DATA_DIR / "case_pack.csv", dtype=str)
    cp["opened_at"] = pd.to_datetime(cp["opened_at"])
    return cp


def device_label(profile: str | float) -> str:
    """'DeviceInfo|OS|browser|screen' -> 'DeviceInfo | OS | browser | screen' (answer-format style)."""
    if not isinstance(profile, str) or not profile:
        return ""
    return " | ".join(profile.split("|"))


def is_specific_device(profile) -> bool:
    """A device profile identifies hardware only if model, OS and screen are all known.

    Generic profiles ('Windows|UNK|chrome 62.0|UNK') are shared by thousands of
    unrelated customers; linking on them manufactures fake rings.
    """
    if not isinstance(profile, str) or not profile:
        return False
    parts = profile.split("|")
    return len(parts) == 4 and parts[0] not in ("UNK", "Windows", "MacOS", "iOS Device", "Trident/7.0") \
        and parts[1] != "UNK" and parts[3] != "UNK"


class InvestigationStore:
    """Query layer over the store. Mirrors the installed GSQL queries (as_of bounded)."""

    def __init__(self, df: pd.DataFrame, closed: pd.DataFrame):
        self.df = df
        self.closed = closed
        self.by_id = df.set_index("TransactionID", drop=False)
        self._cust_idx = df.groupby("customer_id").indices
        self._card_idx = df.groupby("card_id").indices
        dv = df[df.device_profile_id.notna()]
        self._dev_idx = {k: dv.index[v] for k, v in dv.groupby("device_profile_id").indices.items()}
        self.tool_calls = 0
        self.query_log: list[dict] = []

    # ---- helpers ------------------------------------------------------------
    def _log(self, name: str, **params):
        self.tool_calls += 1
        self.query_log.append({"query": name, "params": {k: str(v) for k, v in params.items()}})

    def reset_log(self):
        self.tool_calls = 0
        self.query_log = []

    @cached_property
    def id_set(self) -> set:
        return set(self.df.TransactionID)

    @cached_property
    def card_set(self) -> set:
        return set(self.df.card_id.dropna())

    @cached_property
    def customer_set(self) -> set:
        return set(self.df.customer_id.dropna())

    @cached_property
    def closed_case_set(self) -> set:
        return set(self.closed.case_id)

    # ---- GSQL-equivalent queries -------------------------------------------
    def txn(self, tid: str) -> pd.Series:
        self._log("txn_context", txn_id=tid)
        return self.by_id.loc[tid]

    def customer_window(self, customer_id: str, start, end) -> pd.DataFrame:
        """q_card_window: every transaction on any of the customer's cards in [start, end)."""
        self._log("card_window", customer_id=customer_id, start=start, end=end)
        idx = self._cust_idx.get(customer_id)
        if idx is None:
            return self.df.iloc[0:0]
        d = self.df.iloc[idx]
        return d[(d.ts >= start) & (d.ts < end)]

    def customer_history(self, customer_id: str, as_of, days: int = 120) -> pd.DataFrame:
        """q_cardholder_baseline: history before as_of."""
        self._log("cardholder_baseline", customer_id=customer_id, as_of=as_of, days=days)
        idx = self._cust_idx.get(customer_id)
        if idx is None:
            return self.df.iloc[0:0]
        d = self.df.iloc[idx]
        return d[(d.ts < as_of) & (d.ts >= as_of - pd.Timedelta(days=days))]

    def device_neighbors(self, profile: str, start, end) -> pd.DataFrame:
        """q_device_neighbors: transactions from the same device profile in [start, end)."""
        self._log("device_neighbors", device_profile=profile, start=start, end=end)
        idx = self._dev_idx.get(profile)
        if idx is None:
            return self.df.iloc[0:0]
        d = self.df.loc[idx]
        return d[(d.ts >= start) & (d.ts < end)]

    def window(self, start, end) -> pd.DataFrame:
        self._log("population_window", start=start, end=end)
        return self.df[(self.df.ts >= start) & (self.df.ts < end)]

    def prior_cases(self, customer_id: str, as_of) -> pd.DataFrame:
        """q_prior_cases (graph hop Customer->ClosedCase), closed before as_of."""
        self._log("prior_cases", customer_id=customer_id, as_of=as_of)
        c = self.closed
        return c[(c.customer_id == customer_id) & (c.closed_at < as_of)]
