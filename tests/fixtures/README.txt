RECONSTRUCTED test fixtures. These are NOT backed-up originals.

ticket_orig_from_transcript.py
  Reconstructed verbatim from the pre-edit read stored in
  ~/.local/share/opencode/opencode.db, message part
  prt_10ee7f8e7001RiCSrNWgERMd1o (session ts 1791317566850, never printed).
  505 lines. Line 27: VAULT = os.path.expanduser("~/Documents/KTS Records")
  This is the ORIGINAL ~/.local/bin/ticket before sandbox support was added.

ticket_orig_sandbox.py
  Same reconstruction with exactly one change: line 27 points at the
  sandbox vault (~/Documents/KTS Records-sandbox). Used by the parity
  tests (test13) so the original logic runs sandbox-only.

Provenance check (A3, 2026-10-06): diff against live ~/.local/bin/ticket
is exactly 4 hunks / 20 lines (docstring, importlib paths loader,
enforce_isolation, trailing newline).
