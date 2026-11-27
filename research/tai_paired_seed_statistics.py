"""Strict ten-seed paired inference. CSV must contain paired correct/total and provenance."""
import csv
import itertools
import json
import random
from collections import defaultdict

SEEDS = {11, 23, 37, 53, 71, 83, 97, 109, 127, 149}
FIELDS = ('source_sha', 'train_partition_sha', 'test_partition_sha')

def validate(rows):
    cohorts = defaultdict(dict)
    for row in rows:
        name, seed = row['comparison'], int(row['seed'])
        if seed not in SEEDS or seed in cohorts[name]:
            raise ValueError('invalid or duplicate seed: %s/%s' % (name, seed))
        for prefix in ('baseline', 'treatment'):
            c, n = int(row[prefix+'_correct']), int(row[prefix+'_total'])
            if n <= 0 or not 0 <= c <= n:
                raise ValueError('invalid correct/total: %s/%s' % (name, seed))
            if int(row[prefix+'_rounds']) != 50 or int(row[prefix+'_clients']) != 20:
                raise ValueError('not a 50-round 20-client run: %s/%s' % (name, seed))
        for field in FIELDS:
            a, b = row['baseline_'+field].strip(), row['treatment_'+field].strip()
            if not a or a != b:
                raise ValueError('provenance mismatch: %s/%s/%s' % (name, seed, field))
        cohorts[name][seed] = 100*(int(row['treatment_correct'])/int(row['treatment_total']) -
                                    int(row['baseline_correct'])/int(row['baseline_total']))
    for name, values in cohorts.items():
        if set(values) != SEEDS:
            raise ValueError('incomplete 10-seed cohort: %s' % name)
    return cohorts

def sign_flip(values):
    observed = abs(sum(values))
    return sum(abs(sum(s*v for s,v in zip(signs,values))) >= observed-1e-12
               for signs in itertools.product((-1,1), repeat=len(values))) / 2**len(values)

def bootstrap(values, iterations=20000):
    rng = random.Random(20261120)
    n = len(values)
    draws = sorted(sum(rng.choice(values) for _ in range(n))/n for _ in range(iterations))
    return [draws[int(.025*(iterations-1))],draws[int(.975*(iterations-1))]]

def analyze(rows):
    output = []
    for name, cohort in sorted(validate(rows).items()):
        values = [cohort[s] for s in sorted(SEEDS)]
        output.append(dict(comparison=name, seeds=sorted(SEEDS), n_seeds=10,
                           mean_effect_pp=sum(values)/10, effects_pp=values,
                           exact_two_sided_sign_flip_p=sign_flip(values),
                           paired_bootstrap_95ci_pp=bootstrap(values)))
    running = 0.
    for rank, i in enumerate(sorted(range(len(output)),
                                    key=lambda i: output[i]['exact_two_sided_sign_flip_p'])):
        running = max(running, min(1., (len(output)-rank)*output[i]['exact_two_sided_sign_flip_p']))
        output[i]['holm_adjusted_p'] = running
    return output

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence_csv')
    args = parser.parse_args()
    with open(args.evidence_csv, newline='') as f:
        print(json.dumps(analyze(list(csv.DictReader(f))), indent=2))
