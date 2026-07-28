# Threshold Recovery Protocol Architecture

## Goal

Describe the repository-compatible architecture for future dropout
recovery without enabling it now.

## Architecture Sketch

1. `AcquireTask` signs a secure-aggregation configuration that includes
   recovery policy and participant numbering.
2. Workers generate fresh session keys and recovery shares after the
   signed cohort roster is frozen.
3. Encrypted per-peer recovery payloads are relayed through dedicated
   signed coordinator RPCs.
4. Workers submit masked updates as they do today.
5. If all contributions arrive, finalize exactly as today.
6. If a dropout is detected but the minimum survivor threshold still
   holds, the protocol enters a recovery stage instead of immediate
   abort.
7. Recovery messages are collected, survivor set is fixed, and only the
   protocol-approved material is reconstructed.
8. Finalization proceeds only after successful recovery and transcript
   validation.

## Deliberate Non-Implementation

This architecture is documentation only. The repository must not expose a
live recovery path until a vetted dependency exists and a separate
implementation/validation pass lands.

## Why This Is Blocked

The missing piece is not general architecture understanding; it is the
absence of an acceptable threshold-sharing dependency stack for the
current C++ plus Python runtime split.
