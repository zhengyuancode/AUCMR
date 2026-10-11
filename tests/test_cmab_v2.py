import numpy as np
import unittest

from models.LinUCB import (
    PCA_FORMAT,
    WarmStartLinUCB,
    build_state,
    validate_artifact_pair,
)


class _Projection:
    n_components_ = 2

    def transform(self, values):
        assert values.shape == (1, 12)
        return np.asarray([[values[0, 0], values[0, 1]]])


class _IdentityScaler:
    def transform(self, values):
        assert values.shape == (1, 5)
        return values


def _assets(split="same"):
    return {
        "format": PCA_FORMAT,
        "pca_model": _Projection(),
        "scaler": _IdentityScaler(),
        "embedding_dim": 2,
        "pca_components": 2,
        "raw_feature_dim": 5,
        "state_dim": 6,
        "split_sha256": split,
    }


class CmabV2Tests(unittest.TestCase):
    def test_v2_state_uses_pca_explicit_features_scaler_and_bias(self):
        embedding = np.asarray([[1.0, 0.0], [0.0, 1.0]])
        state = build_state("two words", embedding, _assets())
        self.assertEqual(state.shape, (6,))
        self.assertEqual(state[0:2].tolist(), [0.5, 0.5])
        self.assertEqual(state[2], 2.0)
        self.assertEqual(state[3], 2.0)
        self.assertEqual(state[4], 0.0)
        self.assertEqual(state[5], 1.0)

    def test_artifact_pair_rejects_state_or_training_split_mismatch(self):
        model = WarmStartLinUCB(n_features=6)
        model.metadata = {"split_sha256": "same"}
        validate_artifact_pair(model, _assets())

        wrong_dim = WarmStartLinUCB(n_features=7)
        wrong_dim.metadata = {"split_sha256": "same"}
        with self.assertRaisesRegex(ValueError, "state mismatch"):
            validate_artifact_pair(wrong_dim, _assets())

        model.metadata = {"split_sha256": "different"}
        with self.assertRaisesRegex(ValueError, "different splits"):
            validate_artifact_pair(model, _assets())
