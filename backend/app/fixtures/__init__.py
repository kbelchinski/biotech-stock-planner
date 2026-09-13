"""Demo data.

Two layers, kept strictly apart:
- Provider response fixtures (bpiq/, alpaca/): synthetic payloads in the documented external
  schemas, served over a mock HTTP transport and processed by the same adapters as live data.
- Mock financial enrichment (mock_financials.json): demo-only cash/burn values that no approved
  provider supplies. Never merged into provider payloads and never loaded in live mode.

All companies and tickers are fictional.
"""
