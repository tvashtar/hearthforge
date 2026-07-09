# SRD Attribution

This directory vendors the Dungeons & Dragons 5th Edition Systems Reference
Document (SRD 5.1), © Wizards of the Coast, licensed under the Creative
Commons Attribution 4.0 International License (CC-BY-4.0):
https://creativecommons.org/licenses/by/4.0/legalcode

Sources (edition-tagged; currently the 2014 rules / SRD 5.1):
- `2014/text/` — markdown conversion from https://github.com/tvashtar/dnd-5e-srd
  (fork of https://github.com/vitusventure/5thSRD)
- `2014/structured/` — JSON records from https://github.com/5e-bits/5e-database
  (`src/2014/en/`)

Both are re-distributions of the same SRD 5.1 content. Re-run
`scripts/sync_srd.py` to refresh from upstream. A future migration to the
2024 rules (SRD 5.2) adds a `2024/` directory beside `2014/`.
