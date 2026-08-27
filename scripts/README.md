# Development Scripts

* `scripts/test` - Run the test suite.
* `scripts/test-js` - Run app/pages/*'s static JS bundle tests (Node's built-in test runner +
  jsdom; run `npm install --prefix app/pages` once first). Separate from `scripts/test` on
  purpose -- this project's Python verification loop has no Node dependency otherwise.
* `scripts/lint` - Run the automated code linting/formatting tools.
* `scripts/check` - Run the code linting, checking that it passes.
* `scripts/coverage` - Check that code coverage is complete.
* `scripts/build` - Build source and wheel packages.

Styled after GitHub's ["Scripts to Rule Them All"](https://github.com/github/scripts-to-rule-them-all),
mirroring `focusari_asgi`'s own scripts/ (no `install`/`docs`/`benchmark`/`sync-version` yet —
no docs site or benchmark suite exists for this project yet).
