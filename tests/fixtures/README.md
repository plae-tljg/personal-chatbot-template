# Test fixtures

`github/` is a snapshot of real GitHub API responses for the four configured
accounts, captured once and committed.

Why snapshot instead of mocking:

- **Real messiness.** 42 repositories with snake_case and CamelCase names, forks
  mixed with originals, one repository with no language, descriptions in English
  and Chinese. A hand-written mock would be tidier than reality and would pass
  tests that reality fails.
- **No network in CI.** The unauthenticated GitHub API allows 60 requests/hour.
  A test suite that depends on it is a test suite that fails on a busy afternoon.
- **Determinism.** Same input, same database, same answers, every run.

**A note on content:** the README fixtures are verbatim snapshots of public
GitHub READMEs, so they contain whatever those READMEs contain — including the
author's own local paths (``/home/...``) and placeholder API keys like
``sk-xxxx``. Nothing here is private: all of it is already published on GitHub.
They are kept verbatim because scrubbing them would make the corpus tidier than
reality, and the tests exist to survive reality.

The layout mirrors `data/cache`, which is why the commands point at
`tests/fixtures` (the parent) rather than at `tests/fixtures/github`:

```bash
python -m personal_chatbots build --offline --cache tests/fixtures
```

To refresh a fixture, delete the relevant file and run a normal build; the new
response is cached into `data/cache/github/`, and can be copied over. Do that
deliberately: a fixture update is a data change, and it should be reviewed like
one.
