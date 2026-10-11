# AUCMR CMAB-TopN LinUCB v2

This directory contains the deployable artifacts matching the revised paper
experiment (PCA-10 + three explicit features + bias; 14-D state).

## Files

- `LinUCB.py`: feature construction, model loading, arm selection, and online
  update implementation.
- `pca_assets.pkl`: PCA and training-column standardization assets fitted on
  the 676 training queries only.
- `linucb_model1_efficiency.pkl`: `(alpha, Mq) = (5, 0)`.
- `linucb_model2_balanced.pkl`: `(alpha, Mq) = (3, 20)`.
- `linucb_model3_recall.pkl`: `(alpha, Mq) = (2, 20)`.
- `manifest.json`: dimensions, configuration metadata, split digest, and
  locked-test reproduction metrics.

## Minimal usage

```python
from LinUCB import WarmStartLinUCB, build_state_for_query, load_pca_assets

assets = load_pca_assets("pca_assets.pkl")
model = WarmStartLinUCB.load_model("linucb_model2_balanced.pkl")

state, query_embedding = build_state_for_query(query, embedder, assets)
topn, scores = model.select_arm(state)
```

After observing the selected arm's scalar reward in deployment:

```python
model.update(topn, state, reward)
model.save_model("linucb_model2_balanced_updated.pkl", metadata=model.metadata)
```

Use the same Jina embedding model/configuration as the training traces.  Do
not mix these v2 models with the old 11-D `pca_assets.pkl` or old model files.
