# Social post

**LinkedIn / X (thread)**

1/ Our HHGOA fraud agent learned the hard way that "risk score > 0.7" is not a verdict. v1 called 17 of 20 alerts fraud. v2 learns from the bank's own closed cases and gets 0.82 verdict accuracy on a leakage-free replay of 544 real cases. #TigerGraph #HHGOA

2/ It rebuilds the whole fraud episode across a customer's cards (Jaccard 0.84 vs analysts). It also finds typologies nobody labelled:
- sub-$500 structuring bursts;
- a Samsung device behind an anonymous proxy on 20 cards of 20 customers in 30 days.

3/ The LLM-free policy engine is a pure function of Fraud Policy v1.0. It alone writes actions and SAR decisions, so the explanation can never contradict the action. Blocks on cleared cases: 8%.

4/ Then it kept watching. With no case pack, it swept Nov–Dec and opened 15 alerts of its own, including the full 28-customer device ring.

Blog: docs/BLOG_POST.md · Code: github.com/Cometbuster4969/hacker-house

*(Posted after the 24 Sept deadline.)*
