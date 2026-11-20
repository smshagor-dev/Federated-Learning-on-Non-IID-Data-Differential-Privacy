import unittest
from research.tai_paired_seed_statistics import SEEDS, validate, analyze, sign_flip

def rows():
    return [dict(comparison='test', seed=str(s), baseline_correct='5000', baseline_total='10000',
                 treatment_correct='6000', treatment_total='10000',
                 baseline_source_sha='a', treatment_source_sha='a',
                 baseline_train_partition_sha='b', treatment_train_partition_sha='b',
                 baseline_test_partition_sha='c', treatment_test_partition_sha='c',
                 baseline_rounds='50', treatment_rounds='50',
                 baseline_clients='20', treatment_clients='20') for s in sorted(SEEDS)]

class EvidenceGates(unittest.TestCase):
    def test_exact_two_sided(self):
        self.assertEqual(sign_flip([1]*10), 2/1024)
        self.assertEqual(sign_flip([0]*10), 1)
    def test_valid(self):
        self.assertAlmostEqual(analyze(rows())[0]['mean_effect_pp'], 10)
    def test_missing_seed(self):
        with self.assertRaises(ValueError): validate(rows()[:-1])
    def test_duplicate(self):
        with self.assertRaises(ValueError): validate(rows()+rows()[:1])
    def test_partition_mismatch(self):
        r=rows(); r[0]['treatment_test_partition_sha']='wrong'
        with self.assertRaises(ValueError): validate(r)
    def test_one_round(self):
        r=rows(); r[0]['baseline_rounds']='1'
        with self.assertRaises(ValueError): validate(r)
    def test_bad_denominator(self):
        r=rows(); r[0]['treatment_correct']='10001'
        with self.assertRaises(ValueError): validate(r)

if __name__ == '__main__': unittest.main()
