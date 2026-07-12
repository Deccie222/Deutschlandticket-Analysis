"""Deutschlandticket adoption scoring — fast / convenient / easy-to-access."""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees_synthetic.csv"
SUMMARY_PATH = DATA_DIR / "adoption_summary.csv"

RECOMMEND_THRESHOLD = 0.7
OPTIONAL_THRESHOLD = 0.4

RECOMMENDATION_ORDER = [
    "Recommend Deutschlandticket",
    "Optional",
    "Not recommended",
]

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading / saving
# ---------------------------------------------------------------------------

def load_employees(path=None):
    path = path or EMPLOYEES_PATH
    return pd.read_csv(path)


def save_employees(df, path=None):
    path = path or EMPLOYEES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def save_summary(summary_df, path=None):
    path = path or SUMMARY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(path, index=False)
    return path


def _require_columns(df, columns):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        logger.error("错误: 缺少必要字段: %s", ", ".join(missing))
        logger.error("  请先运行 pt_connection.py 和 commute.py")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_scores(df):
    """Compute FAST / CONVENIENT / ACCESS dimension scores and adoption_score.

    Dimensions
    ----------
    FAST        : time_score       = exp(-(transit/driving - 1))
    CONVENIENT  : convenience_score = exp(-transit_time_minutes / 45)
    EASY ACCESS : access_score      = pt_access_score

    adoption_score = w_time*time_score + w_conv*convenience_score + w_access*access_score
    (weights derived from each dimension's range: max - min)
    """
    _require_columns(df, ["driving_time_minutes", "transit_time_minutes", "pt_access_score"])

    result = df.copy()
    driving = result["driving_time_minutes"].astype(float)
    transit = result["transit_time_minutes"].astype(float)
    access = result["pt_access_score"].astype(float)

    # Dimension 1 — FAST: transit vs driving time competitiveness
    time_ratio = transit / driving
    result["time_score"] = np.exp(-(time_ratio - 1))

    # Dimension 2 — CONVENIENT: absolute transit duration
    result["convenience_score"] = np.exp(-transit / 45)

    # Dimension 3 — EASY TO ACCESS
    result["access_score"] = access

    range_time = result["time_score"].max() - result["time_score"].min()
    range_conv = result["convenience_score"].max() - result["convenience_score"].min()
    range_access = result["access_score"].max() - result["access_score"].min()
    ranges = np.array([range_time, range_conv, range_access])
    ranges = np.where(ranges == 0, 1e-6, ranges)
    weights = ranges / ranges.sum()
    w_time, w_conv, w_access = weights

    # Combined adoption score
    result["adoption_score"] = (
        w_time * result["time_score"]
        + w_conv * result["convenience_score"]
        + w_access * result["access_score"]
    )

    # Invalidate scores where required inputs are missing or driving is zero
    invalid = (
        transit.isna() | driving.isna() | access.isna() | (driving <= 0)
    )
    score_cols = ["time_score", "convenience_score", "access_score", "adoption_score"]
    result.loc[invalid, score_cols] = np.nan
    result["adoption_score"] = result["adoption_score"].round(4)
    result["time_score"] = result["time_score"].round(4)
    result["convenience_score"] = result["convenience_score"].round(4)

    return result


def classify_ticket(df):
    """Assign ticket_recommendation based on adoption_score thresholds."""
    if "adoption_score" not in df.columns:
        df = compute_scores(df)

    delta = (df["driving_time_minutes"] - df["transit_time_minutes"]).median()

    if delta > 10:
        RECOMMEND_THRESHOLD = 0.65
    elif delta > 0:
        RECOMMEND_THRESHOLD = 0.55
    else:
        RECOMMEND_THRESHOLD = 0.45

    OPTIONAL_THRESHOLD = RECOMMEND_THRESHOLD - 0.15

    def _classify(score):
        if pd.isna(score):
            return "Not recommended"
        if score >= RECOMMEND_THRESHOLD:
            return "Recommend Deutschlandticket"
        if score >= OPTIONAL_THRESHOLD:
            return "Optional"
        return "Not recommended"

    result = df.copy()
    result["ticket_recommendation"] = result["adoption_score"].apply(_classify)
    return result


def summarize(df):
    """Count and percentage per ticket_recommendation category."""
    if "ticket_recommendation" not in df.columns:
        df = classify_ticket(df)

    total = len(df)
    counts = df["ticket_recommendation"].value_counts()

    summary = pd.DataFrame({
        "recommendation": counts.index,
        "count": counts.values,
    })
    summary["percentage"] = (summary["count"] / total * 100).round(1)

    order_map = {r: i for i, r in enumerate(RECOMMENDATION_ORDER)}
    summary["_sort"] = summary["recommendation"].map(
        lambda r: order_map.get(r, len(RECOMMENDATION_ORDER))
    )
    summary = summary.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    return summary


def print_summary(summary_df):
    logger.info("")
    logger.info("=== Deutschlandticket Adoption Summary ===")
    for _, row in summary_df.iterrows():
        logger.info("  %-30s  %4d  (%5.1f%%)",
                     row["recommendation"], row["count"], row["percentage"])
    logger.info("  %-30s  %4d", "Total", summary_df["count"].sum())


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_scoring():
    """Load employees, score, classify, summarize, and save."""
    logger.info("正在加载员工数据 ...")
    employees = load_employees()
    logger.info("  共 %d 名员工", len(employees))

    logger.info("正在计算 adoption 评分 (fast / convenient / access) ...")
    scored = compute_scores(employees)

    logger.info("正在分类月票推荐 ...")
    classified = classify_ticket(scored)

    logger.info("正在统计推荐分布 ...")
    summary = summarize(classified)
    print_summary(summary)

    logger.info("正在保存文件 ...")
    emp_out = save_employees(classified)
    sum_out = save_summary(summary)
    logger.info("员工数据已保存 -> %s", emp_out)
    logger.info("推荐统计已保存 -> %s", sum_out)

    valid = classified["adoption_score"].notna().sum()
    logger.info("有效评分: %d/%d", valid, len(classified))
    if valid:
        logger.info("  adoption_score: min=%.4f  median=%.4f  max=%.4f",
                     classified["adoption_score"].min(),
                     classified["adoption_score"].median(),
                     classified["adoption_score"].max())

    return classified, summary


# ---------------------------------------------------------------------------
# Legacy wrappers (notebook / summary pipeline)
# ---------------------------------------------------------------------------

def score_all(df):
    """Legacy alias — score and classify employees."""
    if "adoption_score" not in df.columns:
        df = compute_scores(df)
    if "ticket_recommendation" not in df.columns:
        df = classify_ticket(df)
    return df


def organization_summary(df):
    """Legacy org-level summary from adoption recommendations."""
    df = score_all(df)
    n = len(df)
    recommended = (df["ticket_recommendation"] == "Recommend Deutschlandticket").sum()
    optional = (df["ticket_recommendation"] == "Optional").sum()

    return {
        "total_employees": n,
        "recommended_count": int(recommended),
        "optional_count": int(optional),
        "not_recommended_count": int(n - recommended - optional),
        "recommendation_rate_pct": round(recommended / n * 100, 1) if n else 0,
        "median_adoption_score": round(df["adoption_score"].median(), 4),
    }


if __name__ == "__main__":
    run_scoring()
