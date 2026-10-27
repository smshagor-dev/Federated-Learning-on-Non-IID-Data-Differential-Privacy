# Native Byzantine-Robust Aggregation: supplementary summary data

Companion summary dataset for the manuscript *Native Implementation and Performance Evaluation of Byzantine Robust Aggregation in Federated Learning*.

## Data and verification

- `Benchmark_6row_Machine_Readable.csv`: six CPU pilot timings transcribed from the original manuscript.
- `Table_S1_Algorithm_Conditions.csv`: constraints and idealized work for four aggregation rules.
- `Table_S2_Validation_Results.csv`: seven author-reported validation outcomes.
- `Table_S3_Synthetic_Attack_Configuration.csv`: 10-client and 25-round controlled attack setup.
- `Table_S4_Convergence_Outcomes.csv`: categorical endpoint outcomes, not full learning curves.
- `Table_S5_CPU_Pilot_Benchmarks.csv`: six original reported timings and explicit missing median timing rows.
- `Table_S6_Derived_CPU_Timings.csv`: mathematical calculations from rounded timing medians.
- `Table_S7a_Environment.csv`, `Table_S7b_Measurement_Protocol.csv`: reported settings and missing experimental metadata.
- `Table_S8_Evidence_Limitations.csv`: audit of the evidence boundary.
- `Reported_Configuration_Metadata.json`: reported configuration, nulls for unknown metadata.
- `verify_reported_timings.py`: stdlib Python consistency check.

Run from the repository root:

```bash
python research/native-byzantine-robust-aggregation/verify_reported_timings.py
```

## Provenance and limitations

These are source-manuscript **summary tables**, not raw measurements. The original individual timing samples,
model updates, convergence trajectories, actual source of the evaluated native C++20 extension, hardware
inventory, IID/non-IID dataset experiments, more advanced poisoning attacks, SIMD/OpenMP ablations, Krum-only
convergence, median CPU benchmarks, and end-to-end federated round timings were **not supplied**.

Only two timing repetitions are reported; median and variability estimates cannot be independently checked.
The six speedup ratios can be recomputed from the rounded published summary values, but this is not a
new experiment. Do not call this dataset a full code-reproducibility release or evidence of unmeasured results.

The rest of this repository is a separate federated-learning platform; **this directory does not establish
that its C++20 implementation is the one used in this study**. The author can archive a tag/release with Zenodo
after checking the final snapshot. A record URL supplied previously by the author, DOI
`10.5281/zenodo.22112309`, has not been verified against these particular files.
