#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split


def timer():
    return time.perf_counter()


def main():
    parser = argparse.ArgumentParser(description="LightGBM benchmark for credit card fraud data.")
    parser.add_argument("--data", default="creditcard.csv", help="Path to creditcard.csv")
    parser.add_argument("--output", default="benchmark_result.json", help="JSON output path")
    parser.add_argument("--sample-frac", type=float, default=1.0, help="Optional sample fraction for small instances")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path.resolve()}")

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    total_start = timer()

    load_start = timer()
    df = pd.read_csv(data_path)
    if args.sample_frac < 1.0:
        df = df.groupby("Class", group_keys=False).sample(frac=args.sample_frac, random_state=args.seed)
    load_seconds = timer() - load_start

    X = df.drop(columns=["Class"])
    y = df["Class"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=args.seed,
        stratify=y,
    )

    positives = int(y_train.sum())
    negatives = int(len(y_train) - positives)
    scale_pos_weight = negatives / max(positives, 1)

    model = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=500,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.9,
        colsample_bytree=0.9,
        scale_pos_weight=scale_pos_weight,
        random_state=args.seed,
        n_jobs=max(1, min(os.cpu_count() or 1, 2)),
    )

    train_start = timer()
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_test, y_test)],
        eval_metric="auc",
        callbacks=[
            lgb.early_stopping(stopping_rounds=30, verbose=False),
            lgb.log_evaluation(period=50),
        ],
    )
    training_seconds = timer() - train_start

    predict_start = timer()
    y_prob = model.predict_proba(X_test)[:, 1]
    prediction_seconds = timer() - predict_start
    y_pred = (y_prob >= 0.5).astype(int)

    one_row = X_test.iloc[[0]]
    single_runs = 100
    single_start = timer()
    for _ in range(single_runs):
        model.predict_proba(one_row)
    single_latency_ms = ((timer() - single_start) / single_runs) * 1000

    batch_size = min(1000, len(X_test))
    batch = X_test.iloc[:batch_size]
    batch_start = timer()
    model.predict_proba(batch)
    batch_seconds = timer() - batch_start
    throughput_rows_per_second = batch_size / max(batch_seconds, 1e-9)

    result = {
        "started_at_utc": started_at,
        "instance_note": "AWS CPU fallback benchmark. Current lab instance may be t3.micro instead of r5.2xlarge.",
        "data_path": str(data_path.resolve()),
        "rows": int(len(df)),
        "features": int(X.shape[1]),
        "fraud_rows": int(y.sum()),
        "sample_frac": args.sample_frac,
        "load_data_seconds": round(load_seconds, 4),
        "training_seconds": round(training_seconds, 4),
        "prediction_seconds": round(prediction_seconds, 4),
        "best_iteration": int(getattr(model, "best_iteration_", 0) or model.n_estimators),
        "auc_roc": round(float(roc_auc_score(y_test, y_prob)), 6),
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 6),
        "f1_score": round(float(f1_score(y_test, y_pred, zero_division=0)), 6),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 6),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 6),
        "inference_latency_1_row_ms": round(single_latency_ms, 4),
        "inference_throughput_rows_per_second": round(throughput_rows_per_second, 2),
        "total_seconds": round(timer() - total_start, 4),
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n=== LightGBM Credit Card Fraud Benchmark ===")
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"\nSaved result to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
