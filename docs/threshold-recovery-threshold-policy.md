# Threshold Recovery Threshold Policy

## Current Policy

Current secure aggregation uses:

- `minimum_cohort_size == cohort_size`
- abort on any missing key advertisement
- abort on any missing masked update

That is the repository's no-dropout policy.

## Future Policy Requirements

If threshold recovery is enabled in the future, the threshold policy must
be explicit and signed into the session configuration:

- `cohort_size`
- `minimum_survivor_count`
- maximum tolerated dropout count
- participant numbering policy
- recovery phase deadlines

## Non-Negotiable Constraints

- `minimum_survivor_count` must never be inferred ad hoc at runtime
- the policy must be visible to workers before they create shares
- the same threshold must be enforced consistently across share generation, share release, and finalization

## Current Decision

No threshold policy is activated because no vetted dependency passed the
evaluation. The repository remains complete-cohort only.
