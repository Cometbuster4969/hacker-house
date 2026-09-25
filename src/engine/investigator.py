"""The investigating agent.

A hypothesis-driven loop over graph/retrieval tools. Every step records *why* the tool
was chosen, what it returned, and how the fraud probability moved — the trace is
saved to traces/<case>.json and rendered in the dashboard.

Control flow (see docs/ARCHITECTURE.md):
  intake -> context -> baseline -> window+scoring -> hypothesis tools (chosen by what
  the evidence so far suggests) -> memory retrieval -> assessment -> policy (initial)
  -> evidence request if the value of information is positive -> simulated response
  -> policy (final) -> write case to memory/graph -> render.

Nothing in here chooses actions: policy.decide() is the only writer.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd

from . import calibration, config, episode, patterns, rings
from .model import logit, sigmoid
from .narrative import PATTERN_TEXT, money, sar_narrative, span_text
from .policy import PolicyInput, decide
from .store import InvestigationStore, device_label, is_specific_device

log = logging.getLogger(__name__)

# Likelihood ratios for structural findings and for the (simulated) customer answer.
# Structural LRs are conservative round numbers checked against the closed cases:
# every closed case with a sub-$500 structuring burst or the shared-proxy device ring
# was confirmed fraud (9/9), card testing 16/16.
LR = {"structuring": 10.0, "device_ring": 10.0, "card_testing": 5.0, "customer_report": 2.0,
      "recurring": 0.2, "denied": 2.5, "confirmed": 1 / 4, "no_reply": 1.0, "prior_hhg_link": 3.0}
# Simulation of replies (policy 5: "simulate them in your own system"): the customer's
# answer is drawn deterministically from the evidence-only probability.
SIM_DENY_AT = 0.60
SIM_CONFIRM_BELOW = 0.40
SIM_CONFIRM_BELOW_REPORT = 0.20


def bayes(p: float, lr: float) -> float:
    return float(np.clip(sigmoid(logit(p) + np.log(lr)), 0.02, 0.98))


@dataclass
class Trace:
    steps: list = field(default_factory=list)

    def add(self, tool, why, result, p=None, entity_ids=None, params=None):
        self.steps.append({"step": len(self.steps) + 1, "tool": tool, "why": why, "result": result,
                           "p_fraud": None if p is None else round(float(p), 3),
                           "params": params or {}, "entity_ids": entity_ids or []})
        return len(self.steps)


class Investigator:
    def __init__(self, store: InvestigationStore, memory, graph_writer=None, model_dir=None, p_by_id=None):
        self.s = store
        self.mem = memory
        model_dir = model_dir or config.MODELS_DIR
        self.pattern_model = joblib.load(model_dir / "pattern_model.joblib")
        self.cal = joblib.load(model_dir / "case_calibrator.joblib")
        self.graph_writer = graph_writer
        self.p_by_id = p_by_id if p_by_id is not None else store.df.set_index("TransactionID").p_txn

    # ------------------------------------------------------------------ main
    def investigate(self, case: pd.Series) -> tuple[dict, dict]:
        t0 = time.time()
        s, tr = self.s, Trace()
        s.reset_log()
        self.mem.calls = 0
        ev: list[dict] = []
        as_of = case.opened_at
        trig = case.trigger_type
        tr.add("intake", "every investigation starts from the alert",
               f"{trig}: {case.trigger_text}", params={"as_of": str(as_of)})

        # 1. flagged transaction ------------------------------------------------
        f = s.txn(case.flagged_txn_id).copy()
        f["p_txn"] = float(self.p_by_id.get(f.TransactionID, f.p_txn))
        assert f.card_id == case.card_id and f.customer_id == case.customer_id, "case pack mismatch"
        dev = f.device_profile_id if isinstance(f.device_profile_id, str) else ""
        tr.add("txn_context", "read the flagged transaction and its device/region attributes",
               f"{money(f.TransactionAmt)} {f.channel} product {f.ProductCD} at {f.ts:%Y-%m-%d %H:%M}, region "
               f"{'' if pd.isna(f.addr1) else int(f.addr1)}, device {device_label(dev) or 'none'} "
               f"({f.id_15 if isinstance(f.id_15, str) else 'n/a'}), risk_score {f.risk_score:.2f}",
               entity_ids=[f.TransactionID])

        # 2. baseline ---------------------------------------------------------------
        hist = s.customer_history(case.customer_id, f.ts, days=120)
        uid_hist = hist[hist.uid == f.uid]
        seen_region = (not pd.isna(f.addr1)) and (f.addr1 in set(hist.addr1.dropna()))
        seen_uid_region = (not pd.isna(f.addr1)) and (f.addr1 in set(uid_hist.addr1.dropna()))
        old = hist[hist.ts < f.ts - pd.Timedelta(days=14)]
        seen_dev = bool(dev) and dev in set(old.device_profile_id.dropna())
        seen_card_region = (not pd.isna(f.addr1)) and (f.addr1 in set(old[old.card_id == f.card_id].addr1.dropna()))
        typical_amt = float(uid_hist.TransactionAmt.median()) if len(uid_hist) else np.nan
        tr.add("cardholder_baseline", "establish what normal looks like before judging the alert",
               f"{len(hist)} customer transactions in 120 days ({len(uid_hist)} by this cardholder key); "
               f"region {'seen' if seen_region else 'new'} for the customer; device "
               f"{'seen' if seen_dev else ('new' if dev else 'n/a')}", entity_ids=[case.customer_id])

        # 3. window + model -----------------------------------------------------
        win = s.customer_window(case.customer_id, f.ts - pd.Timedelta(days=episode.LOOKBACK_DAYS), as_of).copy()
        win["p_txn"] = self.p_by_id.reindex(win.TransactionID).values
        hi = win[win.p_txn >= episode.MEMBER_THRESHOLD]
        tr.add("score_window", "score every transaction on the customer's cards in the look-back window with the "
               "calibrated model (trained on the bank's own closed cases)",
               f"{len(win)} transactions scored; {len(hi)} at or above {episode.MEMBER_THRESHOLD}; flagged "
               f"transaction {f.p_txn:.3f}", p=f.p_txn, entity_ids=list(hi.TransactionID[:10]))

        # 4. prior cases (graph hop) ----------------------------------------------
        pc = s.prior_cases(case.customer_id, as_of)
        uid_prior = int(f.known_fraud_uid)
        if uid_prior:
            tr.add("prior_cases", "a cardholder with confirmed fraud before is the strongest single signal",
                   f"{uid_prior} transaction(s) of this cardholder key are in confirmed closed cases", p=None)

        # 5. hypothesis tools ------------------------------------------------------
        findings = {"structuring": None, "struct_ring": None, "device_ring": None, "card_testing": None,
                    "recurring": None}
        if f.channel == "online" and 425 <= f.TransactionAmt < 500 or \
                ((win.channel == "online") & win.TransactionAmt.between(425, 499.99)).sum() >= 3:
            st = patterns.detect_structuring(win)
            findings["structuring"] = st
            tr.add("structuring_scan", "amounts just under $500 suggest threshold avoidance; look for a burst",
                   (f"{st['n']} online purchases on {st['card_id']} within {st['span_min']} minutes totalling "
                    f"{money(st['total'])}, each just under $500") if st else "no burst", entity_ids=
                   list(st["rows"].TransactionID) if st else [])
            if st:
                sr = rings.structuring_ring(s, as_of, exclude_card=st["card_id"])
                findings["struct_ring"] = sr
                tr.add("population_structuring_sweep", "is the same modus operandi hitting other customers?",
                       (f"same burst shape on {len(sr.cards) - (st['card_id'] in sr.cards)} other card(s): "
                        f"{', '.join(c for c in sr.cards if c != st['card_id'])}; closed precedents "
                        f"{', '.join(sr.linked_closed_cases) or 'none'}") if sr else "no other cards", entity_ids=
                       (sr.cards if sr else []))
        devices = set(win[win.p_txn >= episode.MEMBER_THRESHOLD].device_profile_id.dropna()) | ({dev} if dev else set())
        if trig == "analyst_request" or (dev and (is_specific_device(dev) or f.id_23 == f.id_23)):
            best = None
            for d in sorted(devices):
                r = rings.device_ring(s, d, as_of, self.p_by_id)
                if r and (best is None or len(r.customers) > len(best.customers)):
                    best = r
            findings["device_ring"] = best
            tr.add("device_neighbors", "devices connect people: who else used this device profile in the last 30 days?",
                   (f"{device_label(best.element)} used by {len(best.customers)} customers / {len(best.cards)} cards "
                    f"in 30 days, {best.anomaly}; closed precedents {', '.join(best.linked_closed_cases[:4]) or 'none'}")
                   if best else f"no anomalous shared use of {len(devices)} device profile(s)",
                   entity_ids=(best.cards[:10] if best else []))
        if ((win.TransactionAmt < 5) & (win.channel == "online")).sum() >= 3:
            ct = patterns.detect_card_testing(win)
            findings["card_testing"] = ct
            tr.add("card_testing_scan", "several sub-$5 online authorizations are the R5 signature",
                   (f"{len(ct['tests'])} small authorizations on {ct['card_id']} within an hour, then "
                    f"{money(ct['purchase_amt'])}") if ct else "no testing sequence",
                   entity_ids=(ct["tests"] + [ct["purchase"]]) if ct else [])
        if trig == "customer_report":
            rec = patterns.recurring_match(hist, f)
            findings["recurring"] = rec if len(rec) else None
            tr.add("recurring_check", "R7: a disputed charge may be the customer's own monthly charge",
                   f"{len(rec)} earlier monthly charge(s) of the same product and amount" if len(rec) else
                   "no monthly recurring charge of this product and amount", entity_ids=list(rec.TransactionID))

        # 6. episode --------------------------------------------------------------
        ep = episode.reconstruct(win, f.TransactionID, f.ts)
        ep_rows = ep.txns
        if findings["structuring"] is not None and f.TransactionID in set(findings["structuring"]["rows"].TransactionID):
            ep_rows = findings["structuring"]["rows"].sort_values("ts")
        if findings["device_ring"] is not None:
            ring_ids = set(findings["device_ring"].txn_ids)
            ep_rows = pd.concat([ep_rows, win[win.TransactionID.isin(ring_ids)]]).drop_duplicates("TransactionID").sort_values("ts")
        if findings["card_testing"] is not None:
            ct = findings["card_testing"]
            ep_rows = pd.concat([ep_rows, ct["rows"]]).drop_duplicates("TransactionID").sort_values("ts")
            ep_rows = ep_rows[(ep_rows.p_txn >= episode.MEMBER_THRESHOLD) | ep_rows.TransactionID.isin(
                set(ct["tests"]) | {ct["purchase"], f.TransactionID})]
        n_members = int((ep_rows.p_txn >= episode.MEMBER_THRESHOLD).sum())
        tr.add("reconstruct_episode", "chain model-flagged transactions across the customer's cards with gaps "
               "<= 48 h (the rule the bank's own closed cases follow)",
               f"{len(ep_rows)} transaction(s) {span_text(ep_rows)}, exposure {money(float(ep_rows.TransactionAmt.abs().sum()))}",
               entity_ids=list(ep_rows.TransactionID))

        # 7. assessment -----------------------------------------------------------
        x = calibration.case_vector(f.p_txn, float(ep_rows.p_txn.max()), n_members, uid_prior)
        p_model = self.cal(x)
        p = p_model
        tr.add("assess_model", "combine flagged score, episode strength and cardholder history into a case "
               "probability (logistic calibrator fitted on replayed closed cases, prior shifted to 50/50)",
               f"case probability {p_model:.2f}", p=p_model)
        struct = []
        if findings["structuring"] is not None and findings["struct_ring"] is not None:
            p = bayes(p, LR["structuring"]); struct.append("structuring")
        if findings["device_ring"] is not None and f.TransactionID in set(findings["device_ring"].txn_ids):
            p = bayes(p, LR["device_ring"]); struct.append("device_ring")
        if findings["card_testing"] is not None:
            p = bayes(p, LR["card_testing"]); struct.append("card_testing")
        if findings["recurring"] is not None:
            p = bayes(p, LR["recurring"])
        # agent memory: earlier investigations touching the same cards/devices
        prior_hhg = self.mem.agent_links(set(ep_rows.card_id) | {f.card_id},
                                         {d for d in devices if is_specific_device(d)}, case.customer_id)
        prior_hhg = [c for c in prior_hhg if c["verdict"] == "fraud" and c["case_id"] != case.case_id]
        if prior_hhg:
            p = bayes(p, LR["prior_hhg_link"]); struct.append("agent_memory")
            tr.add("agent_memory", "earlier investigations written to the graph are evidence for this one",
                   "; ".join(f"{c['graph_case_id']} ({c['pattern']}) via {c['why']}" for c in prior_hhg), p=p)
        if struct:
            tr.add("assess_structure", "structural findings are independent of the transaction model",
                   f"{', '.join(struct)} -> probability {p:.2f}", p=p)
        p_evidence = p
        if trig == "customer_report":
            p = bayes(p, LR["customer_report"])
            tr.add("assess_report", "the customer's own report is evidence, but reports can be mistaken",
                   f"probability {p:.2f} with the dispute", p=p)

        # independent signals ---------------------------------------------------------
        sig = []
        if f.p_txn >= 0.5:
            sig.append("model")
        if n_members >= 2:
            sig.append("episode")
        if uid_prior:
            sig.append("history")
        if (isinstance(f.id_15, str) and f.id_15 == "New") or isinstance(f.id_23, str):
            if f.p_txn >= 0.2 or struct:
                sig.append("device")
        if not seen_card_region and not pd.isna(f.addr1) and len(old) and f.p_txn >= 0.2:
            sig.append("region")
        sig += struct
        if trig == "customer_report":
            sig.append("customer_report")
        legit_sig = []
        if f.p_txn < 0.05:
            legit_sig.append("model")
        if seen_region or seen_dev:
            legit_sig.append("familiar region/device")
        if n_members <= 1 and not struct:
            legit_sig.append("isolated")

        # pattern -------------------------------------------------------------------
        pattern, pdesc, pconf = self._pattern(findings, ep_rows, hist, f)

        # memory retrieval ----------------------------------------------------------
        uids = set(ep_rows.uid)
        links = self.mem.graph_links(case.customer_id, uids, set(ep_rows.card_id), devices, as_of)
        feats = patterns.episode_features(ep_rows, hist[hist.ts < ep_rows.ts.min()])
        near = self.mem.knn(feats, as_of, k=5)
        near_fraud = sum(n.outcome == "confirmed_fraud" for n in near)
        near_cleared = self.mem.knn(feats, as_of, k=3, outcome="cleared")
        prec = []
        for lk in links[:3]:
            prec.append(lk.case_id)
        rl = findings["device_ring"].linked_closed_cases if findings["device_ring"] else []
        rl += findings["struct_ring"].linked_closed_cases if findings["struct_ring"] else []
        for c in rl[:4]:
            if c not in prec:
                prec.append(c)
        self._near_cleared = near_cleared
        self._prec_pool = (near, near_cleared)
        tr.add("retrieve_memory", "GraphRAG: graph-linked closed cases plus behavioural nearest neighbours",
               f"{len(links)} graph-linked closed case(s); 5 nearest precedents: " +
               ", ".join(f"{n.case_id} ({n.outcome.replace('_', ' ')}, {n.pattern})" for n in near),
               entity_ids=prec)

        # 8. initial decision ---------------------------------------------------------
        conflicting = (trig == "customer_report" and f.p_txn < 0.05 and not struct) or \
                      (f.risk_score >= 0.85 and f.p_txn < 0.05)
        v0 = "fraud" if (p >= 0.70 and len(sig) >= 2) else ("legitimate" if p <= config.STOP_LOW else "uncertain")
        stop_now = (p >= config.STOP_HIGH and len(sig) >= 2) or (p <= config.STOP_LOW and len(legit_sig) >= 2)
        ring = findings["device_ring"] or findings["struct_ring"]
        shared = ""
        if findings["device_ring"] is not None and "device_ring" in struct:
            shared = f"device profile {device_label(findings['device_ring'].element)}"
        own_cards = set(ep_rows.card_id)
        ring_cards = sorted(set(ring.cards) - own_cards) if ring is not None and struct else []
        cross = bool(ring_cards) or any(c["customer_id"] != case.customer_id for c in prior_hhg)
        exposure0 = round(float(ep_rows.TransactionAmt.abs().sum()), 2)
        request = None
        if not stop_now:
            rtype = "step_up_auth" if pattern == "card_testing" else "customer_validation"
            request = {"type": rtype, "asked_after_step": len(tr.steps)}
        base = dict(pattern=pattern, trigger=trig, shared_origin=shared, connected_cards=len(ring_cards),
                    cross_customer=cross, recurring_match=findings["recurring"] is not None,
                    card_testing_cleared_max=(findings["card_testing"]["max_cleared"] if findings["card_testing"] else 0),
                    cards_confirmed_fraud=0, n_signals=len(sig))
        d0 = decide(PolicyInput(p=p, verdict=v0, exposure=exposure0, evidence_requested=bool(request),
                                request_type=request["type"] if request else "", conflicting=conflicting, **base))
        tr.add("policy_initial", "the policy engine is the only writer of actions",
               ", ".join(a["action"] for a in d0.actions), p=p)

        # 9. evidence request + simulated reply ------------------------------------
        response, resp_text, p1 = None, "", p
        if request:
            # A customer who has already disputed the charge is only assumed to withdraw it when the
            # evidence is clearly benign; in between we assume silence (the conservative outcome).
            confirm_below = SIM_CONFIRM_BELOW_REPORT if trig == "customer_report" else SIM_CONFIRM_BELOW
            if p_evidence >= SIM_DENY_AT:
                response = "denied"
            elif p_evidence < confirm_below:
                response = "confirmed"
            else:
                response = "no_reply"
            resp_text = self._reply_text(request["type"], response, trig, f, findings)
            request["assumed_response"] = resp_text
            p1 = bayes(p, LR[response])
            tr.add("evidence_request", f"stopping rule not met (p={p:.2f}, {len(sig)} fraud signal(s)); one "
                   "answer from the cardholder changes the action set, so the information is worth asking for",
                   f"{request['type']}: assumed '{resp_text}' (simulated from the evidence-only probability "
                   f"{p_evidence:.2f}: deny >= {SIM_DENY_AT}, confirm < {confirm_below}, otherwise no reply)", p=p1)
        # final verdict
        if response == "denied":
            v1 = "fraud"
        elif response == "confirmed":
            v1 = "legitimate"
        elif response == "no_reply":
            v1 = "uncertain"
        else:
            v1 = "fraud" if p1 >= config.STOP_HIGH else ("legitimate" if p1 <= config.STOP_LOW else "uncertain")
        if findings["recurring"] is not None and response is None:
            v1 = "uncertain"
        legit = v1 == "legitimate"
        # precedents used as memory: graph-linked first, then behavioural neighbours of the
        # outcome we concluded (cleared look-alikes for a legitimate verdict)
        if legit:
            prec = [lk.case_id for lk in links if lk.outcome == "cleared"][:2]
            pool = list(near_cleared) + [n for n in near if n.outcome == "cleared"]
        else:
            pool = [n for n in near if n.outcome == "confirmed_fraud"]
        for n in pool:
            if len(prec) >= 6:
                break
            if n.case_id not in prec:
                prec.append(n.case_id)
        aff = pd.DataFrame(columns=ep_rows.columns) if legit else ep_rows
        exposure = round(float(aff.TransactionAmt.abs().sum()), 2) if len(aff) else 0.0
        if legit:
            pattern, pdesc = "none", ""
            ring_cards, shared, cross = [], "", False
            base.update(pattern="none", shared_origin="", connected_cards=0, cross_customer=False)
        # A customer report *is* the customer's denial (R2) once our own evidence corroborates it.
        pol_resp = response
        if response is None and trig == "customer_report" and v1 == "fraud":
            pol_resp = "denied"
        d1 = decide(PolicyInput(p=p1, verdict=v1, exposure=exposure, response=pol_resp,
                                evidence_requested=bool(request),
                                conflicting=conflicting and v1 == "uncertain", **base))
        if not request:
            d0 = d1
        tr.add("policy_final", "re-run the policy on the updated state (3b)",
               ", ".join(a["action"] for a in d1.actions), p=p1)

        # 10. write case to memory / graph ---------------------------------------
        graph_case_id = f"CASE-{case.case_id}"
        conn_cards = sorted((own_cards - {f.card_id}) | set(ring_cards)) if not legit else []
        conn_devs = []
        if findings["device_ring"] is not None and not legit and "device_ring" in struct:
            conn_devs = [device_label(findings["device_ring"].element)]
        rec = {"case_id": case.case_id, "graph_case_id": graph_case_id, "customer_id": case.customer_id,
               "cards": sorted(own_cards | set(conn_cards)) if not legit else [f.card_id],
               "devices": sorted(devices) if not legit else [], "verdict": v1, "pattern": pattern,
               "txn_ids": list(aff.TransactionID), "opened_at": str(as_of),
               "p": round(float(p1), 4), "exposure": exposure}
        self.mem.write(rec)
        written = False
        if self.graph_writer is not None:
            try:
                written = bool(self.graph_writer(rec))
            except Exception as e:  # noqa: BLE001
                log.warning("TigerGraph write failed: %s", e)
        tr.add("write_case", "3a: a case written to the graph becomes evidence for the next investigation",
               f"{graph_case_id} stored in agent memory" + (" and TigerGraph" if written else
                                                            " (TigerGraph not configured: memory file only)"))

        # 11. evidence list -----------------------------------------------------------
        ev = self._evidence(case, f, hist, uid_hist, seen_region, seen_dev, typical_amt, win, ep_rows, n_members,
                            uid_prior, findings, near, near_fraud, links, prior_hhg, p_model, legit, request, resp_text)
        answer = self._render(case, f, v1, p1, pattern, pdesc, aff, exposure, conn_cards, conn_devs, ev, prec,
                              graph_case_id, written, request, d0, d1, p, response, sig, legit_sig, stop_now,
                              findings, struct, near, near_fraud, time.time() - t0, as_of)
        trace = {"case_id": case.case_id, "as_of": str(as_of), "steps": tr.steps,
                 "queries": s.query_log, "signals": sig, "legit_signals": legit_sig,
                 "p_model": round(p_model, 3), "p_initial": round(p, 3), "p_final": round(p1, 3)}
        return answer, trace

    # ------------------------------------------------------------------ helpers
    def _pattern(self, findings, ep_rows, hist, f):
        if findings["structuring"] is not None and findings["struct_ring"] is not None:
            st, sr = findings["structuring"], findings["struct_ring"]
            others = [c for c in sr.cards if c != st["card_id"]]
            return "undocumented", (
                f"Threshold structuring: {st['n']} online purchases of {money(st['rows'].TransactionAmt.min())}-"
                f"{money(st['rows'].TransactionAmt.max())} within {st['span_min']:.0f} minutes, each just under a $500 "
                f"limit, totalling {money(st['total'])}. The same burst shape appears on {len(others)} other "
                f"customers' cards ({', '.join(others[:4])}) in the prior 30 days, so it is coordinated rather than "
                f"card testing (no small probes) or a known category. Found by a population sweep for sub-$500 "
                f"bursts after the amounts on this card clustered below $500."), 0.9
        if findings["device_ring"] is not None and f.TransactionID in set(findings["device_ring"].txn_ids):
            r = findings["device_ring"]
            return "undocumented", (
                f"Shared-device ring: one device profile ({device_label(r.element)}), {r.anomaly}, made purchases "
                f"on {len(r.cards)} cards belonging to {len(r.customers)} different customers within 30 days. "
                f"No single cardholder's behaviour explains it; the common element is the device. Found by "
                f"expanding from the flagged transaction's device to every card that used it."), 0.9
        if findings["card_testing"] is not None:
            return "card_testing", "", 0.9
        hh = hist[hist.ts < ep_rows.ts.min()]
        pp = self.pattern_model.predict_proba(patterns.episode_features(ep_rows, hh))
        pp = {k: v for k, v in pp.items() if k in patterns.KNOWN and k != "card_testing"}
        # Definitional constraints, checked against the 4,665 confirmed closed cases:
        # CNP patterns are 100% online; "new device" cases always carry id_15 == New and
        # plain CNP cases never do. (Out-of-region and takeover are left to the model: in the
        # bank's labels both are card-present, separated by device/match-flag behaviour.)
        online = ep_rows[ep_rows.channel == "online"]
        if online.empty:
            pp.pop("card_not_present_fraud", None)
            pp.pop("card_not_present_new_device", None)
        elif online.id_15.eq("New").any():
            pp.pop("card_not_present_fraud", None)
        else:
            pp.pop("card_not_present_new_device", None)
        if len(online) == len(ep_rows):
            pp.pop("out_of_region_use", None)
        best = max(pp, key=pp.get)
        return best, "", pp[best] / sum(pp.values())

    @staticmethod
    def _reply_text(rtype, response, trig, f, findings):
        amt = money(f.TransactionAmt)
        if rtype == "step_up_auth":
            return {"denied": "One-time passcode challenge failed; the cardholder, reached by phone, says they made no online purchases",
                    "confirmed": "Cardholder passed the one-time passcode challenge and confirmed the purchase",
                    "no_reply": "No response to the one-time passcode challenge within 24 hours"}[response]
        if trig == "customer_report":
            return {"denied": f"Customer, shown the {amt} transaction's time, product and channel, maintains they did not make it and still holds the card",
                    "confirmed": f"Customer, shown the {amt} transaction's time, product and channel, recognises it (made by a household member) and withdraws the dispute",
                    "no_reply": "Customer did not reply to the follow-up within 24 hours"}[response]
        return {"denied": f"Customer states they did not make the {amt} transaction and still has the card",
                "confirmed": f"Customer confirms they made the {amt} transaction",
                "no_reply": "No reply from the customer within 24 hours"}[response]

    def _evidence(self, case, f, hist, uid_hist, seen_region, seen_dev, typical_amt, win, ep_rows, n_members,
                  uid_prior, findings, near, near_fraud, links, prior_hhg, p_model, legit, request, resp_text):
        ev = []
        g = lambda claim, ref, ids: ev.append({"claim": claim, "source": "graph", "ref": ref, "entity_ids": ids})
        reg = "" if pd.isna(f.addr1) else f" in billing region {int(f.addr1)}"
        g(f"Flagged {money(f.TransactionAmt)} {f.channel.replace('_', '-')} purchase (product {f.ProductCD}){reg} on "
          f"{f.ts:%Y-%m-%d %H:%M}; bank risk score {f.risk_score:.2f}, our transaction model {f.p_txn:.2f} "
          f"(model trained on the closed cases; ROC-AUC 0.95 vs 0.86 for the risk score on a Sep-Oct holdout)",
          f"query:txn_context(txn_id={f.TransactionID})", [f.TransactionID, f.card_id])
        base = f"Customer has {len(hist)} transactions in the prior 120 days; " + (
            "no billing region on the flagged transaction" if pd.isna(f.addr1) else
            f"billing region {int(f.addr1)} is {'familiar' if seen_region else 'new'} for the customer")
        if f.channel == "online" and isinstance(f.device_profile_id, str):
            base += f"; device profile {device_label(f.device_profile_id)} is {'established (seen > 14 days earlier)' if seen_dev else 'not established'}" \
                    f"{' and marked New for the account' if f.id_15 == 'New' else ''}"
            if isinstance(f.id_23, str):
                base += f", connection through {f.id_23.replace('IP_PROXY:', '').lower()} proxy"
        g(base, f"query:cardholder_baseline(customer_id={case.customer_id}, days=120)", [case.customer_id])
        if uid_prior:
            g(f"This cardholder key (card + billing region + account-open day) already has {uid_prior} transaction(s) "
              f"in confirmed closed fraud cases", f"query:prior_cases(customer_id={case.customer_id})",
              [c.case_id for c in links if c.link == "same cardholder (uid)"][:5] or [case.customer_id])
        if n_members >= 2 or len(ep_rows) > 1:
            g(f"{len(ep_rows)} transaction(s) {span_text(ep_rows)} on card(s) {', '.join(sorted(set(ep_rows.card_id)))} "
              f"form one episode (gaps <= 48 h); {n_members} of them score >= {episode.MEMBER_THRESHOLD} on the model"
              + ("; the rest are tied in by the structural finding below" if n_members < len(ep_rows) else ""),
              "query:card_window(customer_id=%s, days=10)" % case.customer_id, list(ep_rows.TransactionID))
        else:
            g(f"No other transaction on the customer's cards in the 10-day window scores as likely fraud; the flagged "
              f"transaction is isolated", "query:card_window(customer_id=%s, days=10)" % case.customer_id,
              [f.TransactionID])
        if findings["structuring"] is not None:
            st = findings["structuring"]
            g(f"{st['n']} online purchases within {st['span_min']:.0f} minutes, each between "
              f"{money(st['rows'].TransactionAmt.min())} and {money(st['rows'].TransactionAmt.max())} (just under $500)",
              f"query:structuring_scan(card_id={st['card_id']})", list(st["rows"].TransactionID))
        if findings["struct_ring"] is not None:
            sr = findings["struct_ring"]
            g(f"The same sub-$500 burst appears on {len(sr.cards)} cards of {len(sr.customers)} customers in the prior "
              f"30 days; closed cases {', '.join(sr.linked_closed_cases[:5])} document the same modus operandi",
              "query:population_structuring_sweep(days=30)", sr.cards[:12] + sr.linked_closed_cases[:5])
        if findings["device_ring"] is not None:
            r = findings["device_ring"]
            g(f"Device profile {device_label(r.element)} ({r.anomaly}) was used on {len(r.cards)} cards of "
              f"{len(r.customers)} customers in the prior 30 days" +
              (f"; closed cases {', '.join(r.linked_closed_cases[:4])} involve the same device" if r.linked_closed_cases else ""),
              "query:device_neighbors(days=30)", r.cards[:15] + r.linked_closed_cases[:4])
        if findings["card_testing"] is not None:
            ct = findings["card_testing"]
            g(f"{len(ct['tests'])} online authorizations under $5 within an hour on {ct['card_id']}, then "
              f"{money(ct['purchase_amt'])}", f"query:card_testing_scan(card_id={ct['card_id']})",
              ct["tests"] + [ct["purchase"]])
        if findings["recurring"] is not None:
            rc = findings["recurring"]
            g(f"{len(rc)} earlier monthly charges of the same product and amount by this cardholder",
              "query:recurring_check", list(rc.TransactionID))
        elif case.trigger_type == "customer_report":
            g("No monthly recurring charge of the same product and amount in the customer's history (R7 does not apply)",
              "query:recurring_check", [f.card_id])
        if near:
            nc = self._near_cleared
            g(f"Behavioural nearest neighbours among closed cases: {near_fraud} of {len(near)} confirmed fraud (" +
              ", ".join(f"{n.case_id} {n.pattern}, d={n.distance:.1f}" for n in near) + ")" +
              (f"; closest cleared look-alikes: " + ", ".join(f"{n.case_id} d={n.distance:.1f}" for n in nc) if nc else ""),
              "query:similar_cases(k=5, vector=episode_behaviour)", [n.case_id for n in near] + [n.case_id for n in nc])
        for c in prior_hhg[:2]:
            g(f"Earlier investigation {c['graph_case_id']} ({c['verdict']}, {c['pattern']}) shares {c['why']}",
              "query:agent_case_links", [c["case_id"]])
        if case.trigger_type == "customer_report":
            ev.append({"claim": "Customer reported the charge as not made by them: " + case.trigger_text.split("message: ")[-1],
                       "source": "customer", "ref": "case_pack:trigger_text", "entity_ids": [case.customer_id]})
        ev.append({"claim": "Policy rules applied: see next_best_actions reasons; thresholds from Fraud Policy v1.0 sections 2, 3, 3a and 6",
                   "source": "document", "ref": "HHGOA_IEEE/README.md#fraud-policy", "entity_ids": []})
        if request:
            ev.append({"claim": resp_text + " (simulated reply; assumption recorded in evidence_requests)",
                       "source": "customer", "ref": "evidence_request:1", "entity_ids": []})
        return ev

    def _render(self, case, f, v1, p1, pattern, pdesc, aff, exposure, conn_cards, conn_devs, ev, prec, gcid, written,
                request, d0, d1, p0, response, sig, legit_sig, stop_now, findings, struct, near, near_fraud, lat, as_of):
        # an analyst hand-off is where the case stands, whatever the verdict
        if "ESCALATE_TO_ANALYST" in d1.names():
            status = "escalated"
        else:
            status = {"fraud": "closed_fraud", "legitimate": "closed_legitimate"}.get(v1, "open")
        # summary
        sm = []
        if v1 == "legitimate":
            sm.append(f"{money(f.TransactionAmt)} {f.channel.replace('_', '-')} transaction {f.TransactionID} on {f.card_id} assessed as legitimate "
                      f"(probability {p1:.2f}).")
            if f.p_txn < 0.1:
                sm.append(f"Our model scores it {f.p_txn:.2f} despite a risk score of {f.risk_score:.2f}, and no other "
                          "transaction in the window looks fraudulent.")
            if response == "confirmed":
                sm.append("The cardholder's (simulated) confirmation settles it; alert closed under R3.")
        else:
            sm.append(f"{PATTERN_TEXT[pattern].capitalize()}: {len(aff)} transaction(s) {span_text(aff)} on "
                      f"{', '.join(sorted(set(aff.card_id)))} totalling {money(exposure)} (probability {p1:.2f}).")
            reasons = []
            if "model" in sig:
                reasons.append(f"model score {f.p_txn:.2f}")
            if "history" in sig:
                reasons.append("prior confirmed fraud on the same cardholder")
            if "device" in sig:
                reasons.append("new device / proxy")
            if "region" in sig:
                reasons.append("region new to the cardholder")
            if "structuring" in struct:
                reasons.append("sub-$500 burst repeated across customers")
            if "device_ring" in struct:
                reasons.append(f"device shared by {len(findings['device_ring'].customers)} customers")
            if reasons:
                sm.append("Evidence: " + ", ".join(reasons) + ".")
            if response == "denied":
                sm.append("The (simulated) customer denial settles the verdict.")
            elif response == "no_reply":
                sm.append("No reply was assumed, so the card is monitored and pending authorizations declined.")
            if conn_cards:
                sm.append(f"{len(conn_cards)} connected card(s) identified.")
        summary = " ".join(sm)
        # stop reason
        if request and response in ("denied", "confirmed"):
            stop = (f"The {request['type'].replace('_', ' ')} reply (assumed: {response}) settles the question; "
                    f"probability {p0:.2f} -> {p1:.2f}. Further steps would not change the actions.")
        elif request:
            stop = (f"Customer unreachable (assumed no reply); probability stays {p1:.2f}. R4 actions protect the card; "
                    + ("the analyst takes over with the evidence." if "ESCALATE_TO_ANALYST" in d1.names() else
                       "further graph queries would not change the decision, so the case stays open pending the customer."))
        elif stop_now and p1 >= config.STOP_HIGH:
            stop = f"Probability {p1:.2f} >= 0.85 on {len(sig)} independent signals ({', '.join(sig)}); stopping rule met."
        else:
            stop = f"Probability {p1:.2f} <= 0.15 on independent evidence ({', '.join(legit_sig)}); stopping rule met."
        # SAR
        if d1.file_sar:
            how, why = [], []
            if findings["structuring"] is not None:
                how.append("The purchases were placed within minutes of each other, each just below a $500 threshold, "
                           "a pattern that avoids single-transaction limits.")
            if findings["struct_ring"] is not None:
                sr = findings["struct_ring"]
                why.append(f"The same burst shape was found on {len(sr.cards)} cards of {len(sr.customers)} different "
                           f"customers in the preceding 30 days, indicating coordinated abuse (policy R9).")
            if findings["device_ring"] is not None and conn_devs:
                r = findings["device_ring"]
                how.append(f"All came from device profile {conn_devs[0]}, {r.anomaly}.")
                why.append(f"That device was used on {len(r.cards)} cards of {len(r.customers)} customers within 30 days, "
                           f"which no legitimate cardholder explains (policy R6).")
            if "history" in sig:
                why.append("The same cardholder already had transactions confirmed as fraud in closed cases.")
            if exposure > 1000:
                why.append(f"The amount exceeds the $1,000 reporting threshold.")
            narrative = sar_narrative({"episode_rows": aff, "customer_id": case.customer_id, "exposure": exposure,
                                       "sar_how": how, "sar_why": why, "final_actions": d1.actions, "as_of": as_of,
                                       "response_text": (request or {}).get("assumed_response", "") and
                                       f"When contacted: {request['assumed_response']} (simulated)."})
            subjects = sorted({case.customer_id} | set(aff.card_id) | set(conn_cards)) + conn_devs
            sar = {"file": True, "reason": d1.sar_reason, "narrative": narrative, "subjects": subjects,
                   "total_amount_usd": exposure,
                   "activity_dates": [f"{aff.ts.min():%Y-%m-%d}", f"{aff.ts.max():%Y-%m-%d}"]}
        else:
            sar = {"file": False, "reason": d1.sar_reason, "narrative": "", "subjects": [], "total_amount_usd": 0,
                   "activity_dates": []}
        if request:
            what = (f"Assumed reply ({response.replace('_', ' ')}) moved the probability from {p0:.2f} to {p1:.2f}; "
                    + {"denied": "R2 now applies, so the card is blocked and the case confirmed.",
                       "confirmed": "R3 applies, so the alert is closed as legitimate.",
                       "no_reply": "R4 applies: monitor the card and decline pending authorizations."}[response])
        else:
            what = "nothing"
        return {
            "case_id": case.case_id,
            "case": {
                "status": status, "verdict": v1, "fraud_probability": round(float(p1), 2), "pattern": pattern,
                "pattern_description": pdesc if pattern == "undocumented" else "",
                "affected_txn_ids": list(aff.TransactionID),
                "first_suspicious_txn_id": aff.TransactionID.iloc[0] if len(aff) else "",
                "connected_card_ids": conn_cards, "connected_device_profiles": conn_devs,
                "exposure_usd": exposure, "evidence": ev, "similar_prior_cases": prec, "summary": summary,
                "written_to_graph": written, "graph_case_id": gcid if written else ""},
            "evidence_requests": [request] if request else [],
            "next_best_actions": {"initial": d0.actions, "final": d1.actions, "what_changed": what},
            "sar": sar, "stop_reason": stop,
            "tool_calls": int(self.s.tool_calls + self.mem.calls), "tokens": 0, "latency_s": round(lat, 2)}
