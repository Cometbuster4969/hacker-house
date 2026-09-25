"""Pattern identification.

Two layers:
1. Deterministic typology detectors that follow the README's own definitions
   (card testing R5; the two undocumented typologies found in the analyst notes:
   sub-$500 threshold structuring and the shared anonymous-proxy device ring).
2. A learned classifier over episode-level features, trained on the 4,665 confirmed
   closed cases (their analyst-assigned pattern is the label). It separates the five
   documented typologies from each other.

Undocumented patterns are never forced into a known label (policy R9).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

KNOWN = ["card_testing", "card_not_present_fraud", "card_not_present_new_device",
         "out_of_region_use", "account_takeover"]


def episode_features(ep: pd.DataFrame, history: pd.DataFrame) -> dict:
    """ep: episode rows; history: cardholder rows before the episode (same uids / customer)."""
    n = len(ep)
    online = (ep.channel == "online")
    w = (ep.ProductCD == "W")
    hist_regions = set(history.addr1.dropna())
    hist_uid_regions = set(history[history.uid.isin(set(ep.uid))].addr1.dropna()) if "uid" in history else set()
    hist_devices = set(history.device_profile_id.dropna())
    new_dev = ep.id_15.eq("New")
    small = (ep.TransactionAmt < 5) & online
    return {
        "n": n,
        "log_n": np.log1p(n),
        "frac_online": float(online.mean()) if n else 0,
        "frac_w": float(w.mean()) if n else 0,
        "mixed_channel": int(online.any() and (~online).any()),
        "frac_new_dev": float(new_dev.mean()) if n else 0,
        "any_new_dev": int(new_dev.any()),
        "frac_found_dev": float(ep.id_15.eq("Found").mean()) if n else 0,
        "any_proxy": int(ep.id_23.notna().any()),
        "frac_has_device": float(ep.device_profile_id.notna().mean()) if n else 0,
        "n_small_online": int(small.sum()),
        "frac_small": float(small.mean()) if n else 0,
        "max_amt": float(ep.TransactionAmt.max()) if n else 0,
        "mean_amt": float(ep.TransactionAmt.mean()) if n else 0,
        "n_regions": int(ep.addr1.nunique()),
        "frac_region_unseen": float((~ep.addr1.isin(hist_regions) & ep.addr1.notna()).mean()) if n else 0,
        "frac_uid_region_unseen": float((~ep.addr1.isin(hist_uid_regions) & ep.addr1.notna()).mean()) if n else 0,
        "frac_dev_unseen": float((~ep.device_profile_id.isin(hist_devices) & ep.device_profile_id.notna()).mean()) if n else 0,
        "span_h": float((ep.ts.max() - ep.ts.min()).total_seconds() / 3600) if n else 0,
        "n_products": int(ep.ProductCD.nunique()),
        "frac_m4_m2": float(ep.M4.eq("M2").mean()) if n else 0,
        "frac_m6_f": float(ep.M6.eq("F").mean()) if n else 0,
        "n_emails": int(ep.P_emaildomain.nunique()),
        "hist_n": len(history),
    }


FEATURES = list(episode_features(pd.DataFrame(columns=[
    "channel", "ProductCD", "addr1", "uid", "device_profile_id", "id_15", "id_23", "TransactionAmt",
    "ts", "M4", "M6", "P_emaildomain"]), pd.DataFrame(columns=["addr1", "uid", "device_profile_id"])).keys())


@dataclass
class PatternModel:
    clf: HistGradientBoostingClassifier
    classes: list

    def predict_proba(self, feats: dict) -> dict:
        x = pd.DataFrame([feats])[FEATURES]
        p = self.clf.predict_proba(x)[0]
        return dict(zip(self.classes, map(float, p)))


def train_pattern_model(rows: list[dict], labels: list[str]) -> PatternModel:
    X = pd.DataFrame(rows)[FEATURES]
    clf = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.08, max_leaf_nodes=31,
                                         class_weight="balanced", random_state=0)
    clf.fit(X, labels)
    return PatternModel(clf, list(clf.classes_))


# ---- deterministic detectors ------------------------------------------------

def detect_card_testing(rows: pd.DataFrame) -> dict | None:
    """R5 / pattern 1: >=3 small (<$5) online authorisations on one card within an hour,
    followed by a larger purchase."""
    r = rows.sort_values("ts")
    small = r[(r.TransactionAmt < 5) & (r.channel == "online")]
    for card, g in small.groupby("card_id"):
        ts = list(g.ts)
        for i in range(len(ts)):
            j = i
            while j + 1 < len(ts) and ts[j + 1] - ts[i] <= pd.Timedelta(hours=1):
                j += 1
            if j - i + 1 >= 3:
                burst = g.iloc[i:j + 1]
                after = r[(r.card_id == card) & (r.ts > burst.ts.max()) &
                          (r.ts <= burst.ts.max() + pd.Timedelta(hours=24)) & (r.TransactionAmt >= 20)]
                if len(after):
                    return {"card_id": card, "tests": list(burst.TransactionID),
                            "purchase": after.iloc[0].TransactionID,
                            "purchase_amt": float(after.iloc[0].TransactionAmt),
                            "max_cleared": float(after.TransactionAmt.max()),
                            "rows": pd.concat([burst, after])}
    return None


def detect_structuring(rows: pd.DataFrame, cap: float = 500.0) -> dict | None:
    """Undocumented typology (closed cases CC-3748/3841/3907/4086/4124): three or more
    online purchases within an hour, each just under a $500 authorisation threshold."""
    r = rows[(rows.channel == "online") & (rows.TransactionAmt < cap) & (rows.TransactionAmt >= 0.85 * cap)]
    r = r.sort_values("ts")
    for card, g in r.groupby("card_id"):
        ts = list(g.ts)
        for i in range(len(ts)):
            j = i
            while j + 1 < len(ts) and ts[j + 1] - ts[i] <= pd.Timedelta(hours=1):
                j += 1
            if j - i + 1 >= 3:
                b = g.iloc[i:j + 1]
                # Organic bursts are one price point bought repeatedly ($499.95 x 4 gift cards);
                # the structuring in closed cases CC-3748..4124 uses *different* amounts chosen
                # to stay under the limit. Reject bursts that repeat one price (within 10 cents).
                amts = sorted(b.TransactionAmt)
                repeated = any(y - x < 0.10 for x, y in zip(amts, amts[1:]))
                if repeated or amts[-1] - amts[0] < 5:
                    continue
                return {"card_id": card, "rows": b, "n": len(b), "total": round(float(b.TransactionAmt.sum()), 2),
                        "span_min": round((b.ts.max() - b.ts.min()).total_seconds() / 60, 1)}
    return None


def recurring_match(history: pd.DataFrame, flagged: pd.Series, tol: float = 0.02) -> pd.DataFrame:
    """R7: prior charges from the same cardholder (uid), same product code, same amount
    (+/-2%), recurring roughly monthly (gaps 25-35 days). No merchant column exists, so
    ProductCD is the documented proxy."""
    h = history[(history.uid == flagged.uid) & (history.ProductCD == flagged.ProductCD)]
    h = h[(h.TransactionAmt - flagged.TransactionAmt).abs() <= tol * flagged.TransactionAmt]
    if len(h) < 2:
        return h.iloc[0:0]
    ts = sorted(list(h.ts) + [flagged.ts])
    gaps = [(b - a).days for a, b in zip(ts, ts[1:])]
    monthly = sum(25 <= g <= 35 for g in gaps)
    return h if monthly >= 2 else h.iloc[0:0]
