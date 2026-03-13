import numpy as np
import pandas as pd

from orion.analysis.cross_validation import PurgedKFold


def test_purged_kfold_no_overlap():
    """
    Verify that PurgedKFold generates train/test sets with no overlap
    and respects purge/embargo logic.
    """
    n_samples = 100
    # Create simple timeline: index=0..99
    # Assume 1 second per sample
    # T0 (entry) = i
    # T1 (exit) = i + 2 (hold for 2 steps)

    indices = np.arange(n_samples)
    t0 = pd.Series(indices, index=indices)
    t1 = pd.Series(indices + 2, index=indices)

    pkf = PurgedKFold(n_splits=2, embargo_pct=0.0)

    # 2 Splits of 100 samples => 50 Test each.
    # Fold 0: Test [0..50], Train [50..100]
    # Fold 1: Test [50..100], Train [0..50]

    splits = list(pkf.split(events_times=t1, test_times=t0))
    assert len(splits) == 2

    # Check Fold 0
    train0, test0 = splits[0]
    assert len(test0) == 50
    assert test0[0] == 0

    # Purging Check
    # Test 0 Range: T0=[0..49], T1_max = 49+2 = 51.
    # Train 0: T0=[50..99]
    # Overlap: Train T0 <= Test T1 max?
    # 50 <= 51? Yes.
    # 51 <= 51? Yes.
    # So indices 50 and 51 should be purged from Train set.

    assert 50 not in train0
    assert 51 not in train0
    assert 52 in train0  # 52 > 51, safe.


def test_purged_kfold_embargo():
    """
    Verify embargo removes data AFTER test set.
    """
    n_samples = 100
    indices = np.arange(n_samples)
    t0 = pd.Series(indices, index=indices)  # T0 same as index
    t1 = pd.Series(indices + 1, index=indices)  # Short hold (1 step)

    # Embargo 10% = 10 samples
    pkf = PurgedKFold(n_splits=2, embargo_pct=0.10)

    splits = list(pkf.split(events_times=t1, test_times=t0))
    train0, test0 = splits[0]

    # Fold 0: Test=[0..50) -> indices 0..49. Max T1 = 50.
    # Train starts at 50.
    # Embargo of 10 samples applies after test end (index 50).
    # Indices [50..59] should be embargoed.

    # Check 55 (inside embargo)
    assert 55 not in train0
    # Check 60 (after embargo)
    assert 60 in train0
