# demo/

Demo/seed tooling for NanobarAPI. Nothing here is part of the installed `nanobar_api`
package or its test suite — this is example/dev tooling only.

## seed_kahnban_bricks.py

Drives real, varied HTTP traffic through [focusari_kahnban](../../focusari_kahnban)
(a real, separately-built kanban app used here purely as a realistic traffic source),
captures it through this project's own regression-brick pipeline
(`SnapshotMiddleware` -> eventbus -> `generate_bricks`), and creates/binds `Nanobar`s for
what it captured.

Run from this repo's root:

```sh
uv run python demo/seed_kahnban_bricks.py
```

It creates three persistent (not temp-dir) SQLite databases under `demo/data/`
(gitignored):

- `demo/data/kahnban.db` — kahnban's own data (boards/lists/cards created by the seeded
  traffic), pointed there via `focusari_kahnban.db.configure()` so this script never
  touches kahnban's own dev database.
- `demo/data/events.db` — the raw captured request/response snapshot events.
- `demo/data/regression_bricks.db` — the generated `RegressionBrick`s, plus the
  `Nanobar`s and `nanobar_regression_bricks` bindings created from them. This is also
  what [`demo/dashboard`](dashboard/) reads by default.

All three are real files you can open afterward, e.g. `sqlite3 demo/data/regression_bricks.db`.

The traffic driven covers 2 boards, 2-3 lists per board, 5 cards spread across lists, an
in-list reorder (position-race territory), a cross-board move *attempt* (kahnban's own
`card_service` rejects this by design — the capture is the rejection response, which is
still a legitimate regression brick), a card delete (gap-closing position logic), and a
couple of read-only GETs.

**Safe to re-run.** Nanobars are keyed by a stable `(method, path-template)` identity
that doesn't depend on any particular board/list/card id, so re-running never creates
duplicate Nanobars. Regression bricks dedupe by content-hash of the exact captured
request+response and are only ever generated once per event — but since each run creates
fresh boards/lists/cards with new random ids, the captured content differs run to run, so
brick *counts* will typically keep growing across runs rather than staying flat. See the
module's own docstring for the full explanation.
