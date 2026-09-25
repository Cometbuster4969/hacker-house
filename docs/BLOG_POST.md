# A risk score is a reason to look: building a fraud investigator on a graph

*HHGOA 2026 · TigerGraph Agentic Fraud Investigation. Written after the deadline.*

The benchmark hands you 20 alerts and a warning buried in the README: *most transactions with a
risk score above 0.7 are legitimate.* Our first agent took the obvious route. It gathered graph
evidence, asked an LLM to reason, and let rules fill the gaps. It called 17 of the 20 cases fraud.
The README says half are legitimate. So we threw the verdict layer away and started again from
one question: **what does the bank actually know?**

## 1. Learn from the only ground truth there is

The bank's 5,565 closed cases are the only confirmed outcomes in the dataset. We trained a
gradient-boosted model on them, labelling each transaction by whether it sits inside a confirmed
fraud case. The features are strictly causal. The strongest one is whether *this cardholder*
already had confirmed fraud, and it counts a closed case only once the case has been **closed**
at the time of the alert.

The dataset's "customer" is an issuer-level bucket that pools many real people. We used the
well-known card + billing region + account-open-day key as a `Cardholder` identity. On a
Sep–Oct holdout, the model reached ROC-AUC 0.947 against 0.861 for the bank's risk score.

## 2. Reconstruct the episode, not the transaction

We measured the confirmed cases directly rather than guessing what an "episode" is:

- activity separated by gaps under 48 hours;
- across **all** of the customer's cards;
- exposure equal to the sum of the episode.

Rebuilding episodes with those rules scores a mean Jaccard of 0.84 against the analysts' own
transaction lists.

## 3. Let the graph find what nobody labelled

Two typologies don't fit any documented pattern.

- **Sub-$500 structuring.** Four online purchases in half an hour, each a different amount
  between $456 and $499. Organic bursts repeat one price (for example $499.95 four times);
  these don't. There are 22 such bursts in the whole dataset. Five of them are confirmed closed
  cases, and HHG-006 is another. A population sweep found the same shape on three other customers'
  cards in the month before, so the agent files the case as undocumented under R9.
- **An anonymous-proxy device ring.** One Samsung SM-G935F profile, marked *New* for every
  account and behind an anonymous proxy, appears on 20 cards of 20 customers in 30 days.
  HHG-014 is on it. The agent monitors the 19 connected cards (R6) and escalates.

We were just as careful about *false* rings. "Windows / Chrome / 1920×1080" is shared by
thousands of honest people. The agent links cards only through hardware-specific devices or
anomalous shared use.

## 4. Keep the policy out of the model's hands

`policy.decide()` is a pure function of the case state. It implements R1–R10, the case-vs-report
table and the routing thresholds, and it is the only thing that writes actions or decides a SAR.
The agent decides *what to investigate* and *whether a customer's answer is worth asking for*.
The policy decides *what to do*. A validator checks all 20 answer files against every rule that
can be checked mechanically.

## 5. Measure it honestly

We replayed 544 October closed cases through the whole agent, using models trained only on
July–August. Each alert was presented as a plain risk-score alert on a random transaction of
the episode. The results:

- 0.82 verdict accuracy on decided cases, with 10% left uncertain;
- blocks on only 8% of the cases the bank cleared;
- SAR decisions that agree with the analysts 91% of the time.

## 6. Keep watching

The same engine then swept November–December with no case pack. It opened 15 alerts of its own:

- 12 structuring bursts;
- the full SM-G935F ring: 28 customers and 60 transactions;
- one small candidate ring, left for a human to judge;
- one card-testing sequence.

**What we'd do next:** run it live on TigerGraph Savanna, so the agent's cases become graph
vertices that the next investigation traverses. The GSQL is written. In this run the agent's
memory lived in a JSON graph file.

*Data note: we used a public column extract of the official HHGOA_IEEE files, never the public
IEEE-CIS labels.*
