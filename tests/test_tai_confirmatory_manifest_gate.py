import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from research.tai_confirmatory_manifest_gate import load_verified_manifest, COMPARISONS, SEEDS

def fixture():
    rows = []
    for comparison in sorted(COMPARISONS):
        for seed in SEEDS:
            r = dict(independently_audited=True, round_ids=list(range(1, 51)),
                     client_count=20, correct=5000, total=10000,
                     artifact_sha256="a"*64, source_sha="b"*40,
                     train_partition_sha="c"*64, test_partition_sha="d"*64)
            rows.append(dict(comparison=comparison, seed=seed,
                             baseline=r.copy(), treatment=r.copy()))
    return dict(schema="tai-independent-evidence-v1",
                auditor="independent-auditor", paired_rows=rows)

def check(doc, corrupt_digest=False):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)/"manifest.json"
        p.write_text(json.dumps(doc))
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        return load_verified_manifest(p, "0"*64 if corrupt_digest else digest)

class Gates(unittest.TestCase):
    def test_complete(self):
        self.assertEqual(len(check(fixture())), 40)
    def test_missing_pair(self):
        x=fixture(); x["paired_rows"].pop()
        with self.assertRaises(ValueError): check(x)
    def test_unaudited(self):
        x=fixture(); x["paired_rows"][0]["baseline"]["independently_audited"]=False
        with self.assertRaises(ValueError): check(x)
    def test_missing_round(self):
        x=fixture(); x["paired_rows"][0]["baseline"]["round_ids"].pop()
        with self.assertRaises(ValueError): check(x)
    def test_partition_mismatch(self):
        x=fixture(); x["paired_rows"][0]["treatment"]["test_partition_sha"]="e"*64
        with self.assertRaises(ValueError): check(x)
    def test_manifest_digest(self):
        with self.assertRaises(ValueError): check(fixture(), True)
    def test_duplicate(self):
        x=fixture(); x["paired_rows"][0]=x["paired_rows"][1]
        with self.assertRaises(ValueError): check(x)
    def test_bad_counts(self):
        x=fixture(); x["paired_rows"][0]["baseline"]["correct"]=10001
        with self.assertRaises(ValueError): check(x)
    def test_unregistered_comparison(self):
        x=fixture(); x["paired_rows"][0]["comparison"]="CIFAR10:ditto:vs:per_fedavg"
        with self.assertRaises(ValueError): check(x)

if __name__=="__main__":
    unittest.main()
