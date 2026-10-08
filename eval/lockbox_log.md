# Lockbox log

This log is append-only. The lockbox is scored **once**, at the `eval-freeze` tag (`DESIGN.md` §10.3). The only allowed rerun is for a demonstrable implementation bug, and both scores are then kept side by side.

| timestamp | tag / commit | split sha | lang | P [Wilson 95%] | n matched | C₂₅₂ | C₂₆₅ | H₂₆₅ | F_NM | $/100 | s/line | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-08T02:37:40.732203+00:00 | eval-freeze / c8cd80a3bee0411d4df599c640b649e58bf03a21 | c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd | en | 0.809 [0.726, 0.872] | 110 | 0.788 | 0.761 | n/a | 0 | 0.073 | 0.263 | B2 run 20261008T023626Z-ea37e3a1; lockbox side, strict; mode live; llm anthropic; code_dirty False |
| 2026-10-08T02:39:08.334778+00:00 | eval-freeze / c8cd80a3bee0411d4df599c640b649e58bf03a21 | c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd | fr | 0.876 [0.803, 0.925] | 113 | 0.876 | 0.846 | n/a | 0 | 0.076 | 0.274 | B2 run 20261008T023750Z-be2dd33e; lockbox side, strict; mode live; llm anthropic; code_dirty False |
| 2026-10-08T02:42:27.309433+00:00 | eval-freeze / c8cd80a3bee0411d4df599c640b649e58bf03a21 | c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd | en | 0.989 [0.939, 0.998] | 89 | 0.779 | 0.752 | n/a | 0 | 0.229 | 0.631 | B3 run 20261008T023928Z-56f85fb8; lockbox side, strict; mode live; llm anthropic; code_dirty False |
| 2026-10-08T02:45:55.994356+00:00 | eval-freeze / c8cd80a3bee0411d4df599c640b649e58bf03a21 | c9a6d1f0aaafdd9f3b7499bf50f59f786f00270c3350e7d5cad7982b855039bd | fr | 0.988 [0.933, 0.998] | 80 | 0.699 | 0.675 | n/a | 0 | 0.237 | 0.676 | B3 run 20261008T024244Z-de394c39; lockbox side, strict; mode live; llm anthropic; code_dirty False |
