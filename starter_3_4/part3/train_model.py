"""PA1 Part 3: predict nine demographic attributes from a user's Boogle searches.

Model: TF-IDF over each user's search queries (word unigrams + bigrams) feeding one
logistic regression per attribute, with class_weight="balanced" because grading is
macro-F1, which counts rare classes (e.g. "unknown") as much as common ones.

    python train_model.py

1. Fits on train and prints macro-F1 per attribute on dev.
2. Refits on train + dev and writes predictions.csv for the test users.
3. Saves the fitted model to model.joblib (to be reused for ad targeting in Part 4).

References: scikit-learn docs for TfidfVectorizer and LogisticRegression.
"""

import csv
import gzip
import json
from pathlib import Path

import joblib
from joblib import Parallel, delayed
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

HERE = Path(__file__).resolve().parent

ATTRIBUTES = [
    "gender", "age", "education", "household_income", "industry",
    "employment_status", "employer_size", "home_ownership", "parental_status",
]


def load_users(split):
    with gzip.open(HERE / f"{split}.jsonl.gz", "rt") as f:
        return [json.loads(line) for line in f]


def load_labels(split, users):
    with open(HERE / f"{split}_labels.csv", newline="") as f:
        by_id = {row["client_id"]: row for row in csv.DictReader(f)}
    return {a: [by_id[u["client_id"]][a] for u in users] for a in ATTRIBUTES}


def user_text(user):
    # One document per user: all of their queries, one per line.
    return "\n".join(s["query"] for s in user["searches"])


def fit(users, labels):
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    X = vectorizer.fit_transform(user_text(u) for u in users)
    # The nine classifiers are independent, so train them in parallel.
    classifiers = Parallel(n_jobs=-1)(
        delayed(LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000).fit)(X, labels[a])
        for a in ATTRIBUTES)
    return {"vectorizer": vectorizer, "classifiers": dict(zip(ATTRIBUTES, classifiers))}


def predict(model, users):
    X = model["vectorizer"].transform(user_text(u) for u in users)
    return {a: model["classifiers"][a].predict(X) for a in ATTRIBUTES}


def main():
    train, dev, test = load_users("train"), load_users("dev"), load_users("test")
    train_labels, dev_labels = load_labels("train", train), load_labels("dev", dev)

    print("Macro-F1 on dev (model fit on train only):")
    dev_preds = predict(fit(train, train_labels), dev)
    for a in ATTRIBUTES:
        print(f"  {a:18} {f1_score(dev_labels[a], dev_preds[a], average='macro'):.3f}")

    # Final model: use every labeled user we have.
    model = fit(train + dev, {a: train_labels[a] + dev_labels[a] for a in ATTRIBUTES})
    test_preds = predict(model, test)
    with open(HERE / "predictions.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["client_id", *ATTRIBUTES])
        for i, user in enumerate(test):
            writer.writerow([user["client_id"], *(test_preds[a][i] for a in ATTRIBUTES)])
    joblib.dump(model, HERE / "model.joblib")
    print(f"Wrote predictions.csv ({len(test)} users) and model.joblib")


if __name__ == "__main__":
    main()
