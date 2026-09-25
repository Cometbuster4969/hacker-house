# Demo artefacts

| File | What it is |
|---|---|
| `walkthrough.mp4` | 2:45 narrated walkthrough, 1920×1080, 7 slides |
| `dashboard.html` | **the real analyst dashboard** (`src/ui/app.py`'s own HTML) with the five API responses inlined — opens from disk with no server, no dataset, no API key |

The video is a narrated slide walkthrough rendered from the run artefacts in `cases/`, `traces/`,
`benchmark/` and `monitoring/`; there is no staged data in it. The build environment had no browser
available, so the live UI was not screen-captured — run `python main.py serve` for that, or open
`dashboard.html` here, which is the same page with the same data.

Rebuild the snapshot (nothing else needs regenerating):

```bash
pip install -e ".[ui]"
python scripts/build_dashboard_snapshot.py
```
