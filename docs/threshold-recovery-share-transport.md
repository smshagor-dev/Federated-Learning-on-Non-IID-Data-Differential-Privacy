# Threshold Recovery Share Transport

## Required Properties

Any future share-transport layer must:

- use the repository's signed-message discipline
- use an independent replay/sequence stream
- bind each share to the exact secure-aggregation session and survivor set
- avoid revealing plaintext secret material to storage or logs

## Recommended Architecture

Future work should add dedicated recovery RPCs rather than overloading the
existing key-advertisement or masked-update messages. The likely shape is:

1. worker generates encrypted recovery payloads for named peers
2. coordinator relays only ciphertext plus signed metadata
3. surviving workers submit signed recovery responses
4. coordinator reconstructs only the material allowed by the protocol once survivor threshold is met

## Current State

No share-transport RPCs or storage models exist today. This is
intentional, not an omission within a partially integrated feature.
