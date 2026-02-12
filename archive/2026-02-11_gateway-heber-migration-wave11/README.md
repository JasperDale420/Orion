# 2026-02-11 Gateway/Heber Migration Wave 11

Archived legacy connector code that still wrote local Silver VIX data.

- `legacy_code/vix_connector.py`
  - depended on direct local SQL sink writes to `silver_vix_data`.
  - replaced in runtime by `vix_proxy_connector` Heber-sourced flow.
