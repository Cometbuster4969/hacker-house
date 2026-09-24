# TigerGraph × Hacker House Goa — Fraud Investigation Dataset (IEEE-CIS edition)

Six months of card transactions from the **IEEE-CIS Fraud Detection** dataset, published by Vesta Corporation, with **every original row and every original column kept**. Two things changed: the yes/no fraud label is gone, and every transaction carries a **risk score** from the bank's detection model instead. On top sit the things an investigation needs: customers, a real calendar, a channel, the bank's closed cases, and the 20 cases you'll be judged on.

The data is anonymized by its publisher. No real people.

## The task

Load the data into TigerGraph. Build an agent that takes a case from the case pack, investigates it using the graph and the closed cases, works out **what kind of fraud it is** (if any), **how far it goes**, and **what to do next** under the Fraud Policy section below, and knows when it needs more evidence before deciding. Run it on all 20 cases.

For each case your agent produces **three things**, in one answer file (format in the Answer Format section below):

1. **A case**: the internal investigation record. Status, verdict, pattern, evidence, affected transactions, connected cards, exposure, and the past cases you retrieved. Write it into the graph too. That is your case memory: the next investigation should be able to find it.
2. **A suspicious activity report**, when the policy calls for one: the regulatory filing, written so it stands on its own.
3. **The next best action**: what the bank should do and who must approve it, both before and after any evidence your agent asked for. The recommendation is allowed to change as evidence comes in. Show that it did.

Submit the 20 answer files. We score them against an answer key you don't have.

## Files in this folder

| File | What it is |
|---|---|
| `README.md` | This document |
| `transactions.csv` | 590,742 transactions, all 393 original Vesta columns plus `customer_id`, `ts`, `channel`, `risk_score`. No fraud flag. About 708 MB |
| `identity.csv` | 144,432 identity records, all 41 original columns, joined to transactions on `TransactionID`. Online transactions only |
| `closed_cases_history.csv` | 5,565 finished investigations, July to October. 4,665 confirmed fraud, 900 cleared |
| `case_pack.csv` | The 20 exam cases with their trigger |

UTF-8 CSV, header row, amounts in USD. `transactions.csv` joins to `identity.csv` on `TransactionID`.

## The 20 Cases

| Case | Opened | Trigger | Flagged txn | Card | Customer | Score |
|---|---|---|---|---|---|---|
| HHG-001 | 2016-12-05 01:55:28 | risk_score | 3514030 | C12382-K1 | C12382 | 0.61 |
| HHG-002 | 2016-11-22 23:27:07 | risk_score | 3478782 | C11891-K1 | C11891 | 0.79 |
| HHG-003 | 2016-12-10 15:01:21 | customer_report | 3530164 | C08623-K2 | C08623 | — |
| HHG-004 | 2016-12-29 07:53:54 | customer_report | 3583227 | C08106-K1 | C08106 | — |
| HHG-005 | 2016-12-08 03:38:37 | risk_score | 3523199 | C02923-K1 | C02923 | 0.54 |
| HHG-006 | 2016-11-22 02:30:00 | customer_report | 3476682 | C07297-K1 | C07297 | — |
| HHG-007 | 2016-12-05 03:46:14 | risk_score | 3514948 | C09933-K2 | C09933 | 0.87 |
| HHG-008 | 2016-12-20 03:08:56 | customer_report | 3558054 | C13171-K2 | C13171 | — |
| HHG-009 | 2016-12-28 17:10:53 | customer_report | 3581141 | C08299-K1 | C08299 | — |
| HHG-010 | 2016-12-02 18:18:27 | risk_score | 3506725 | C10434-K1 | C10434 | 0.90 |
| HHG-011 | 2016-12-29 06:27:44 | customer_report | 3583368 | C11923-K2 | C11923 | — |
| HHG-012 | 2016-12-18 05:00:31 | risk_score | 3553342 | C05876-K2 | C05876 | 0.55 |
| HHG-013 | 2016-12-09 05:39:29 | risk_score | 3526826 | C07671-K2 | C07671 | 0.76 |
| HHG-014 | 2016-11-22 20:11:00 | analyst_request | 3478561 | C13487-K1 | C13487 | — |
| HHG-015 | 2016-11-17 19:03:36 | risk_score | 3464869 | C03042-K1 | C03042 | 0.77 |
| HHG-016 | 2016-12-12 01:39:08 | customer_report | 3534820 | C09988-K1 | C09988 | — |
| HHG-017 | 2016-11-12 00:46:24 | risk_score | 3450629 | C04570-K1 | C04570 | 0.57 |
| HHG-018 | 2016-11-27 14:41:26 | customer_report | 3491361 | C02354-K2 | C02354 | — |
| HHG-019 | 2016-12-01 22:28:53 | risk_score | 3503878 | C07987-K2 | C07987 | 0.90 |
| HHG-020 | 2016-12-03 12:04:26 | risk_score | 3509359 | C12265-K2 | C12265 | 0.52 |
