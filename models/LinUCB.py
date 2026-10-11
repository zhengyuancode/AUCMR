"""Warm-start LinUCB and its canonical v2 query-state construction."""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


MODEL_FORMAT = "aucmr-linucb-v2"
PCA_FORMAT = "aucmr-pca-assets-v2"


def load_pca_assets(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as stream:
        assets = pickle.load(stream)
    if assets.get("format") != PCA_FORMAT:
        raise ValueError(f"Unsupported PCA asset format: {assets.get('format')!r}")
    return assets


def _statistical_embedding_features(query_embedding: np.ndarray) -> np.ndarray:
    embedding = np.asarray(query_embedding, dtype=np.float64)
    if embedding.ndim != 2:
        raise ValueError(f"Expected a 2-D query embedding, got {embedding.shape}")
    return np.concatenate([
        embedding.mean(axis=0),
        embedding.std(axis=0),
        embedding.max(axis=0),
        embedding.min(axis=0),
        np.median(embedding, axis=0),
        np.abs(embedding).mean(axis=0),
    ])


def _mean_pairwise_cosine(query_embedding: np.ndarray) -> float:
    embedding = np.asarray(query_embedding, dtype=np.float64)
    if len(embedding) <= 1:
        return 1.0
    norms = np.linalg.norm(embedding, axis=1)
    denominator = np.outer(norms, norms)
    cosine = np.divide(
        embedding @ embedding.T,
        denominator,
        out=np.zeros_like(denominator),
        where=denominator > 0,
    )
    upper = np.triu_indices(len(embedding), 1)
    return float(cosine[upper].mean())


def build_state(
    query: str,
    query_embedding: np.ndarray,
    pca_assets: Mapping[str, Any],
) -> np.ndarray:
    """Build the exact 14-D state used to train the v2 artifacts."""
    if pca_assets.get("format") != PCA_FORMAT:
        raise ValueError(f"Unsupported PCA asset format: {pca_assets.get('format')!r}")
    embedding = np.asarray(query_embedding, dtype=np.float64)
    expected_dim = int(pca_assets["embedding_dim"])
    if embedding.ndim != 2 or embedding.shape[1] != expected_dim:
        raise ValueError(
            f"Expected query embedding [tokens, {expected_dim}], got {embedding.shape}"
        )

    statistical = _statistical_embedding_features(embedding).reshape(1, -1)
    pca_scores = pca_assets["pca_model"].transform(statistical)[0]
    explicit = np.asarray([
        len(query.split()),
        float(embedding.sum()),
        _mean_pairwise_cosine(embedding),
    ], dtype=np.float64)
    raw = np.concatenate([pca_scores, explicit]).reshape(1, -1)
    standardized = pca_assets["scaler"].transform(raw)[0]
    state = np.concatenate([standardized, [1.0]])
    expected_state_dim = int(pca_assets["state_dim"])
    if state.shape != (expected_state_dim,):
        raise RuntimeError(f"State shape mismatch: {state.shape}")
    if not np.isfinite(state).all():
        raise ValueError("CMAB state contains non-finite values")
    return state


def build_state_for_query(query: str, embedder: Any, pca_assets: Mapping[str, Any]):
    """Return ``(v2 state, multivector query embedding)``."""
    encoded = embedder.encode_text(
        texts=[query],
        task="retrieval",
        prompt_name="query",
        return_multivector=True,
    )[0]
    if hasattr(encoded, "float"):
        encoded = encoded.float()
    if hasattr(encoded, "cpu"):
        encoded = encoded.cpu()
    if hasattr(encoded, "numpy"):
        encoded = encoded.numpy()
    query_embedding = np.asarray(encoded, dtype=np.float64)
    return build_state(query, query_embedding, pca_assets), query_embedding


def validate_artifact_pair(model: "WarmStartLinUCB", pca_assets: Mapping[str, Any]) -> None:
    """Fail fast when a model and its feature assets cannot be used together."""
    if pca_assets.get("format") != PCA_FORMAT:
        raise ValueError(f"Unsupported PCA asset format: {pca_assets.get('format')!r}")
    expected_state_dim = int(pca_assets["state_dim"])
    if model.n_features != expected_state_dim:
        raise ValueError(
            f"CMAB/PCA state mismatch: model={model.n_features}, pca={expected_state_dim}"
        )
    pca_components = int(pca_assets["pca_components"])
    raw_feature_dim = int(pca_assets["raw_feature_dim"])
    if raw_feature_dim != pca_components + 3 or expected_state_dim != raw_feature_dim + 1:
        raise ValueError(
            "Invalid PCA feature layout: expected PCA scores + 3 explicit features + bias"
        )
    model_split = model.metadata.get("split_sha256")
    pca_split = pca_assets.get("split_sha256")
    if model_split and pca_split and model_split != pca_split:
        raise ValueError("CMAB model and PCA assets were trained from different splits")


class WarmStartLinUCB:
    def __init__(
        self,
        n_arms: int = 10,
        n_features: int = 14,
        beta: float = 0.5,
        ridge_lambda: float = 100.0,
        regularize_bias: bool = False,
    ) -> None:
        self.n_arms = int(n_arms)
        self.n_features = int(n_features)
        self.beta = float(beta)
        self.ridge_lambda = float(ridge_lambda)
        self.regularize_bias = bool(regularize_bias)
        diagonal = np.full(self.n_features, self.ridge_lambda, dtype=np.float64)
        if not self.regularize_bias:
            diagonal[-1] = 0.0
        base = np.diag(diagonal)
        self.A = np.repeat(base[None, :, :], self.n_arms, axis=0)
        self.b = np.zeros((self.n_arms, self.n_features), dtype=np.float64)
        self.metadata: dict[str, Any] = {}

    @property
    def arm_indices(self) -> list[int]:
        return list(range(1, self.n_arms + 1))

    def _validate_state(self, state: np.ndarray) -> np.ndarray:
        state = np.asarray(state, dtype=np.float64).reshape(-1)
        if state.shape != (self.n_features,):
            raise ValueError(f"Expected state ({self.n_features},), got {state.shape}")
        if not np.isfinite(state).all():
            raise ValueError("CMAB state contains non-finite values")
        return state

    def predict_scores(self, state: np.ndarray, use_ucb: bool = True) -> np.ndarray:
        state = self._validate_state(state)
        scores = np.empty(self.n_arms, dtype=np.float64)
        for index in range(self.n_arms):
            theta = np.linalg.solve(self.A[index], self.b[index])
            score = float(theta @ state)
            if use_ucb:
                variance = float(state @ np.linalg.solve(self.A[index], state))
                score += self.beta * np.sqrt(max(variance, 0.0))
            scores[index] = score
        return scores

    def select_arm(self, state: np.ndarray, use_ucb: bool = True):
        scores = self.predict_scores(state, use_ucb=use_ucb)
        arm = int(np.argmax(scores)) + 1
        return arm, {index + 1: float(value) for index, value in enumerate(scores)}

    def update(self, arm: int, state: np.ndarray, reward: float) -> None:
        state = self._validate_state(state)
        index = int(arm) - 1
        if not 0 <= index < self.n_arms:
            raise ValueError(f"Invalid arm: {arm}")
        self.A[index] += np.outer(state, state)
        self.b[index] += float(reward) * state

    def update_full_feedback(self, state: np.ndarray, rewards: Sequence[float]) -> None:
        state = self._validate_state(state)
        rewards = np.asarray(rewards, dtype=np.float64).reshape(-1)
        if rewards.shape != (self.n_arms,):
            raise ValueError(f"Expected {self.n_arms} rewards, got {rewards.shape}")
        outer = np.outer(state, state)
        self.A += outer[None, :, :]
        self.b += rewards[:, None] * state[None, :]

    def save_model(self, path: str | Path, metadata: Mapping[str, Any] | None = None):
        payload = {
            "format": MODEL_FORMAT,
            "A": self.A,
            "b": self.b,
            "n_arms": self.n_arms,
            "n_features": self.n_features,
            "beta": self.beta,
            "ridge_lambda": self.ridge_lambda,
            "regularize_bias": self.regularize_bias,
            "metadata": dict(metadata or {}),
        }
        with Path(path).open("wb") as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load_model(cls, path: str | Path) -> "WarmStartLinUCB":
        with Path(path).open("rb") as stream:
            payload = pickle.load(stream)
        if payload.get("format") != MODEL_FORMAT:
            raise ValueError(f"Unsupported model format: {payload.get('format')!r}")
        model = cls(
            n_arms=payload["n_arms"],
            n_features=payload["n_features"],
            beta=payload["beta"],
            ridge_lambda=payload["ridge_lambda"],
            regularize_bias=payload["regularize_bias"],
        )
        model.A = np.asarray(payload["A"], dtype=np.float64)
        model.b = np.asarray(payload["b"], dtype=np.float64)
        if model.A.shape != (model.n_arms, model.n_features, model.n_features):
            raise ValueError(f"Invalid LinUCB A shape: {model.A.shape}")
        if model.b.shape != (model.n_arms, model.n_features):
            raise ValueError(f"Invalid LinUCB b shape: {model.b.shape}")
        model.metadata = dict(payload.get("metadata", {}))
        return model


OnlineLinUCB = WarmStartLinUCB
