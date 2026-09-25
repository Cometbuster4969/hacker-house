#!/usr/bin/env bash
# Fetch a column extract of the official HHGOA_IEEE dataset (590,742 transactions,
# 144,432 identity records) when the 708 MB Google-Drive original is not available.
# Source: public repo kashish-005/hhgoa-fraud-investigation, data/prepared/ — built by
# its dataset_prep/build_transactions.py directly from the organisers' transactions.csv.
# NOTE: this is the HHGOA dataset (disguised IDs/amounts), NOT the Kaggle IEEE-CIS files.
set -euo pipefail
DEST="${1:-data/HHGOA_IEEE/prepared}"
mkdir -p "$DEST"
for f in transactions_core.csv transactions_signals.csv identity_signals.csv; do
  if [ ! -s "$DEST/$f" ]; then
    echo "fetching $f ..."
    gh api -H "Accept: application/vnd.github.raw" \
      "repos/kashish-005/hhgoa-fraud-investigation/contents/data/prepared/$f" > "$DEST/$f"
  fi
done
wc -l "$DEST"/*.csv
