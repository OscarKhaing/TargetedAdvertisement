"""Runs the Part 3 model inside the web server to turn searches into ad segments.

This is the same arithmetic TfidfVectorizer and LogisticRegression do, written with numpy so
the server doesn't need scikit-learn. export_model.py checks that the answers match.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model.npz"
SEGMENTS_PATH = HERE / "starter_3_4" / "part4" / "segments.json"

# TfidfVectorizer's default definition of a word: two or more word characters.
TOKEN = re.compile(r"(?u)\b\w\w+\b")

# Only claim a segment when the model is at least this sure. On dev, claims at this level were
# about 90% correct with just 5 searches and 98% correct with a user's full history.
CONFIDENCE = 0.7


class Model:
    def __init__(self, path=MODEL_PATH):
        with np.load(path) as f:
            self.vocabulary = {term: i for i, term in enumerate(f["terms"].tobytes().decode().split("\n"))}
            self.idf = f["idf"]
            self.classes = json.loads(f["classes"].tobytes())
            self.coef = {a: f[f"coef_{a}"] for a in self.classes}
            self.intercept = {a: f[f"intercept_{a}"] for a in self.classes}
        with open(SEGMENTS_PATH) as f:
            self.segment_ids = {(s["attribute"], s["value"]): s["id"] for s in json.load(f)["segments"]}

    def tfidf(self, text):
        """Returns one user's TF-IDF vector as (feature indices, values)."""
        tokens = TOKEN.findall(text.lower())
        counts = Counter(tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])])
        known = [(self.vocabulary[t], count) for t, count in counts.items() if t in self.vocabulary]
        if not known:
            return [], np.zeros(0)
        indices = [i for i, _ in known]
        values = (1 + np.log([count for _, count in known])) * self.idf[indices]  # sublinear TF
        return indices, values / np.linalg.norm(values)

    def probabilities(self, text):
        """Returns {attribute: probability of each class, in self.classes[attribute] order}."""
        indices, values = self.tfidf(text)
        result = {}
        for attribute in self.classes:
            scores = self.coef[attribute][:, indices] @ values + self.intercept[attribute]
            if len(scores) == 1:  # two classes: one score, for the second class
                p = 1 / (1 + math.exp(-scores[0]))
                result[attribute] = np.array([1 - p, p])
            else:
                exp = np.exp(scores - scores.max())
                result[attribute] = exp / exp.sum()
        return result

    def segments(self, text):
        """Returns the segment ids we're confident about, at most one per attribute."""
        ids = []
        for attribute, probs in self.probabilities(text).items():
            best = int(probs.argmax())
            # "unknown" has no segment id, so it's never claimed.
            segment = self.segment_ids.get((attribute, self.classes[attribute][best]))
            if segment and probs[best] >= CONFIDENCE:
                ids.append(segment)
        return ids
