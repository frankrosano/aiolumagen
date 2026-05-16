---
inclusion: manual
---

# References — Lumagen Documentation

Primary-source Lumagen docs and third-party drivers live in the **sibling repo** `esphome-lumagen/References/` (gitignored, local-only). They are the canonical reference for everything `pylumagen` parses or emits.

Pull this in (`#references`) when working on `protocol.py`, `commands.py`, `state.py`, or anywhere you need to verify a wire-format detail.

## What's There

Located at `../esphome-lumagen/References/` from this repo:

| File | What it is |
|---|---|
| `Tip0011_RS232CommandInterface_111023.pdf` | **The** Lumagen RS-232 command reference. Authoritative for every `ZQ` query, command syntax, `!`-prefixed response, and report code under "Full v4" mode. The 11/2023 edition predates the firmware's "Full v5" / `!I25` mode (deduced empirically — see `protocol.py` for the v5 layout). |
| `Radiance_Pro_Manual_070621.pdf` | User manual. Useful for vocabulary (aspect modes, memories, HDR pipeline) so enum names and docstrings match Lumagen's terms. |
| `crestron-driver/` | Crestron sample modules (mostly binary). Behavioral oracle for cross-checking command strings and parsing. |
| `Pronto/Lumagen_Pronto_Codes.db` | Pronto IR code database. Discrete-IR equivalents of OSD/aspect/memory commands. |
| `radiance_pro120325/` | Reverse-engineered firmware updater + extracted blobs + Python re-implementation. Not currently consumed by `pylumagen`. |

## Rules

- **The protocol of record is `Tip0011_RS232CommandInterface_111023.pdf`.** When third-party drivers and the PDF disagree, trust the PDF and document the discrepancy in code with a comment.
- **Cite Tip0011 sections in code comments** where a parser decision depends on it (`# Tip0011 §3.4: ZQS01 returns...`). Do not paste long verbatim quotes.
- **Don't commit anything from `References/`** to this repo or any sibling repo. It's gitignored at the source.
- **PDFs aren't directly readable** by the agent. If a passage is needed, ask for a `pdftotext` extract and paste it into the conversation.
- The richer references doc lives in the sibling repo at `../esphome-lumagen/.kiro/steering/references.md`.
