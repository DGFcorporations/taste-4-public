# VARIANT: taste-4 — radical minimalist

**Thesis under test:** no embeddings at all. Flat rules in flat files
(git-diffable), full-corpus injection (the whole taste is the prompt), FTS5
lexical scoring, fixed interview, single-shot pipeline. The bet: at corpus
sizes under ~5k standards, semantic retrieval is optional complexity.

## Permutation

| Axis | Choice | Divergence from control |
|---|---|---|
| A schema | A4 flat rules `{directive, confidence}` | the axis under test |
| B retrieval | B5 full-corpus injection (no retrieval — inject ALL) | zero retrieval error |
| C embedding | **C4 none — FTS5/lexical only** | the axis under test |
| D scorer | D1 rules (lexical violations + keyword positive) | held w/ control |
| E interview | E2 fixed (same 3 questions every task) | consistent signal |
| F hone | F3 none (single-shot, trust the packet) | max speed |
| G storage | G3 flat files (md/JSONL, git-diffable corpus) | max transparency |
| H memory | H7 batch consolidation (nightly digest → candidates) | batch not live |
| I injection | I1 directive blocks | held w/ control |
| J pipeline | J3 single-pass | the axis under test |

## The question it answers

The cheapest possible honest TASTE. If it keeps pace with embedded
variants, the whole system gets radically simpler — flat files and zero
model dependencies. The private build keeps the corpus under gitignored
`data/corpus/` so owner rationale never enters this repo.
