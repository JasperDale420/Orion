from collections.abc import Generator

import numpy as np
import pandas as pd


class PurgedKFold:
    """
    K-Fold Cross Validation with Purging and Embargoing for Financial Time Series.

    References:
    - Lopez de Prado, M. (2018). Advances in Financial Machine Learning.

    The goal is to prevent leakage:
    1. Purging: Drop training observations whose labels overlap with the test set time range.
    2. Embargoing: Drop training observations immediately following the test set to prevent serial correlation leakage.
    """

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01):
        """
        Args:
            n_splits: Number of folds.
            embargo_pct: Percentage of total sample size to embargo after each test split.
        """
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    def split(
        self, events_times: pd.Series, test_times: pd.Series | None = None
    ) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:
        """
        Generate indices for train/test splits.

        Args:
            events_times: pd.Series with index corresponding to the dataset (e.g. integer index)
                          and values containing (t1) exit times (timestamps).
                          The index of this series serves as X features index.
            test_times: pd.Series containing the start time (t0) for each observation.
                        If None, assumes index is t0.

        Yields:
            (train_indices, test_indices)
        """
        # 1. Determine Sample Size and Indices
        n_samples = events_times.shape[0]
        indices = np.arange(n_samples)

        # Calculate Embargo Size
        embargo = int(n_samples * self.embargo_pct)

        # 2. Simple K-Fold bounds on indices
        # We split the sorted timeline into N continuous chunks for TESTING
        # But this implementation assumes data is shuffled? No, standard KFold without shuffle does blocks.
        # Standard KFold (sklearn) does blocks: Test is [0..k], [k..2k]...

        fold_size = n_samples // self.n_splits

        # Use t0 (start time) and t1 (end time)
        # events_times values are t1 (exit)
        # test_times values are t0 (entry), if provided, or index if index is datetime

        t1 = events_times
        if test_times is None:
            if isinstance(events_times.index, pd.DatetimeIndex):
                t0 = pd.Series(events_times.index, index=events_times.index)
            else:
                raise ValueError("test_times (t0) must be provided if events_times index is not DatetimeIndex")
        else:
            t0 = test_times

        # Align series to ensure integer indexing works
        # We assume input is sorted by t0!

        for i in range(self.n_splits):
            # Define Test Range indices
            start = i * fold_size
            stop = (i + 1) * fold_size if i != self.n_splits - 1 else n_samples

            test_indices = indices[start:stop]

            if len(test_indices) == 0:
                continue

            # Define Raw Train Indices (All minus Test)
            train_indices = np.concatenate([indices[:start], indices[stop:]])

            # --- PURGING & EMBARGO ---

            # 1. Find Test Time Bounds
            test_start_t0 = t0.iloc[test_indices[0]]
            # We want the MAX exit time in the test set to define the end of the "event window"
            test_max_t1 = t1.iloc[test_indices].max()

            # 2. Purge Train Elements
            # Drop any train element that overlaps with [test_start_t0, test_max_t1]
            # Overlap condition:
            # (Train_t0 <= Test_Max_T1) AND (Train_t1 >= Test_Start_T0)

            train_t0 = t0.iloc[train_indices]
            train_t1 = t1.iloc[train_indices]

            # Vectorized Boolean Mask
            # Overlap: starts before test ends AND ends after test starts
            overlaps = (train_t0 <= test_max_t1) & (train_t1 >= test_start_t0)

            # Embargo: apply to training sets AFTER the test set
            # If train index > test index max, we apply embargo
            # Only relevant if train chunk is strictly after test chunk
            # In standard KFold, the "right side" training set is immediately after test.
            # We need to buffer the start of that right side set.

            # Identify "Right Side" training set (indices > max(test_indices))
            if i < self.n_splits - 1:
                # The right chunk starts at 'stop'.
                # We need to drop the first 'embargo' elements from it,
                # OR simpler: just drop based on time.
                # Usually embargo is fixed number of bars or pct.
                # We'll use index-based embargo for simplicity as per common impl.

                # Mask where index is in the embargo zone
                embargo_limit_index = stop + embargo
                is_embargoed = (train_indices >= stop) & (train_indices < embargo_limit_index)

                # Update drop mask
                overlaps = overlaps | is_embargoed

            # Apply purging
            clean_train_indices = train_indices[~overlaps]

            yield clean_train_indices, test_indices
