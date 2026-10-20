# Contributing

Thanks for considering a contribution. This project is deliberately small: a policy, a playbook, and one dependency-free server.

## What is most useful

1. **Real measurements.** Ledger summaries showing free share, escalation rate, and latency from an actual repository. Open an issue with the numbers and the task mix.
2. **Judge improvements.** Rubrics or checklists that lowered escalation rate without raising the defect rate.
3. **Provider adapters.** Notes or patches for other OpenAI-compatible free tiers.
4. **Bug reports with a trace.** Include the `server.py models` output and the failing tool result, with secrets removed.

## Ground rules

- Keep the server **stdlib-only**. A pull request that adds a dependency needs a strong justification.
- No secrets, tokens, customer data, or private repository content in issues, tests, or fixtures.
- Add a test for behavior changes. Unit tests must pass without an API key.
- Keep the tier policy honest: do not move T2 work to free models in examples.

## Development

```bash
git clone https://github.com/a32fred/frugal-agents.git
cd frugal-agents/mcp/free-models

python3 -m unittest discover -s tests -p "test_*.py" -v   # no key needed
bash tests/smoke_test.sh                                   # needs NIM_API_KEY
```

## Pull requests

- One logical change per PR.
- Describe the problem, the change, and how you verified it.
- If the change affects cost or routing, include before/after numbers from the ledger.
- Update `CHANGELOG.md` under `Unreleased`.

## License

By contributing, you agree that your contribution is licensed under the MIT License.
