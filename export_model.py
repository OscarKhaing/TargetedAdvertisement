"""Exports the Part 3 model to model.npz, the plain numpy file the web server loads.

    python export_model.py

model.joblib is a pickle tied to the exact Python, numpy and scikit-learn versions that wrote
it, and those can't be installed on the VM. This keeps the same fitted numbers (vocabulary,
IDF, weights) in a plain format, then checks inference.py still agrees with scikit-learn.
Needs scikit-learn and joblib, which the server itself doesn't.
"""

import gzip
import json
from pathlib import Path

import joblib
import numpy as np

from inference import MODEL_PATH, Model

PART3 = Path(__file__).resolve().parent / "starter_3_4" / "part3"


def export():
    model = joblib.load(PART3 / "model.joblib")
    vectorizer, classifiers = model["vectorizer"], model["classifiers"]
    terms = sorted(vectorizer.vocabulary_, key=vectorizer.vocabulary_.get)
    arrays = {
        "terms": np.frombuffer("\n".join(terms).encode(), dtype=np.uint8),
        "idf": vectorizer.idf_,
        "classes": np.frombuffer(json.dumps(
            {a: list(c.classes_) for a, c in classifiers.items()}).encode(), dtype=np.uint8),
    }
    for attribute, classifier in classifiers.items():
        arrays[f"coef_{attribute}"] = classifier.coef_.astype(np.float32)
        arrays[f"intercept_{attribute}"] = classifier.intercept_
    np.savez_compressed(MODEL_PATH, **arrays)
    return model


def check(sklearn_model):
    exported = Model(MODEL_PATH)
    for split in ("dev", "test"):
        with gzip.open(PART3 / f"{split}.jsonl.gz", "rt") as f:
            users = [json.loads(line) for line in f]
        texts = ["\n".join(s["query"] for s in u["searches"]) for u in users]
        X = sklearn_model["vectorizer"].transform(texts)
        expected = {a: c.predict_proba(X) for a, c in sklearn_model["classifiers"].items()}
        worst = 0.0
        for i, text in enumerate(texts):
            ours = exported.probabilities(text)
            for attribute, probs in ours.items():
                worst = max(worst, float(np.abs(probs - expected[attribute][i]).max()))
                if probs.argmax() != expected[attribute][i].argmax():
                    raise SystemExit(f"{split} user {i}: {attribute} prediction differs")
        print(f"{split}: {len(users)} users, every prediction matches, "
              f"largest probability difference {worst:.2e}")


if __name__ == "__main__":
    check(export())
    print(f"Wrote {MODEL_PATH.name} ({MODEL_PATH.stat().st_size / 1e6:.0f} MB)")
