# taste-4

This repository contains the public engine code and mechanics for TASTE variant 4. It solves the problem of providing a radically minimalist, personal rule-evaluation engine by removing semantic embeddings entirely in favor of flat rules, full-corpus injection, and lexical scoring. Designed as an experiment to see if a system can remain effective at smaller corpus sizes with zero model dependencies and plain text files, it is the most lightweight variant in the TASTE system.

**What's never here:** the owner's corpus (standards, decision records,
rationale, persona-derived layers). Those live in the private repo and are
the system's learned knowledge — publishing them would publish the person.

Each trial cycle: corpus pruned → keepers stay private → mechanics worth
sharing land here via the publish decision.

## Tech Stack
- **Language:** Python 3.11+
- **Core Frameworks:** FastAPI, Pydantic, Uvicorn
- **Integration:** MCP (Model Context Protocol)

## How to Run
The system operates as an HTTP server bound to a Unix socket for secure, contract-based transport.

To run the server:
```bash
python -m taste.serve
```
By default, the server will bind to `.taste-4.sock` in the current working directory, or to the path specified in the `TASTE_SOCK` environment variable.

## Key Features
- **Zero Embeddings:** Bypasses semantic retrieval entirely; relies on FTS5 lexical scoring and full-corpus injection.
- **Flat File Storage:** All rules and decisions are stored as plain `.md` and `.jsonl` files for maximum transparency and Git diffability.
- **Single-Shot Pipeline:** Optimized for maximum speed without iterative refinement loops.
- **No External Dependencies:** Avoids complex model dependencies by fully relying on standard textual matching and HTTP over Unix sockets.
