"""Final Summary Output — consolidated Deutschlandticket analysis report."""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees_synthetic.csv"
OUTPUT_PATH = DATA_DIR / "final_summary_output.csv"

STRONG_PT_THRESHOLD = 0.6
WEAK_PT_THRESHOLD = 0.3

COMMUTE_GROUP_ORDER = [
    "≤30 min", "30–45 min", "45–60 min", ">60 min", "No transit data",
]
RECOMMENDATION_ORDER = [
    "Recommend Deutschlandticket", "Optional", "Not recommended",
]

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_employees(path=None):
    path = path or EMPLOYEES_PATH
    return pd.read_csv(path)


def _require_columns(df, columns):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        logger.error("错误: 缺少必要字段: %s", ", ".join(missing))
        logger.error("  请按顺序运行: pt_connection → commute → grouping → scoring")
        sys.exit(1)


def _geo_aggregate(subset):
    """Simple geographic summary for a subset of employees."""
    return {
        "mean_home_lat": round(subset["home_lat"].mean(), 4),
        "mean_home_lon": round(subset["home_lon"].mean(), 4),
        "min_home_lat": round(subset["home_lat"].min(), 4),
        "max_home_lat": round(subset["home_lat"].max(), 4),
        "min_home_lon": round(subset["home_lon"].min(), 4),
        "max_home_lon": round(subset["home_lon"].max(), 4),
    }


# ---------------------------------------------------------------------------
# Summary sections
# ---------------------------------------------------------------------------

def summarize_commute_times(df):
    """① Commute time distribution by commute_group."""
    _require_columns(df, ["commute_group"])
    total = len(df)
    counts = df["commute_group"].value_counts()

    summary = pd.DataFrame({
        "commute_group": counts.index,
        "count": counts.values,
    })
    summary["percentage"] = (summary["count"] / total * 100).round(1)

    order = {g: i for i, g in enumerate(COMMUTE_GROUP_ORDER)}
    summary["_sort"] = summary["commute_group"].map(lambda g: order.get(g, 99))
    summary = summary.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    return summary


def summarize_adoption(df):
    """② Deutschlandticket adoption potential."""
    _require_columns(df, ["adoption_score", "ticket_recommendation"])
    total = len(df)
    valid = df["adoption_score"].dropna()

    rows = [
        {"metric": "mean_adoption_score", "value": round(valid.mean(), 4),
         "count": np.nan, "percentage": np.nan},
        {"metric": "median_adoption_score", "value": round(valid.median(), 4),
         "count": np.nan, "percentage": np.nan},
    ]

    rec_counts = df["ticket_recommendation"].value_counts()
    for rec in RECOMMENDATION_ORDER:
        cnt = rec_counts.get(rec, 0)
        rows.append({
            "metric": rec,
            "value": np.nan,
            "count": int(cnt),
            "percentage": round(cnt / total * 100, 1),
        })

    return pd.DataFrame(rows)


def summarize_connectivity(df):
    """③④ Strong vs weak public transport connectivity areas."""
    _require_columns(df, ["pt_access_score", "home_lat", "home_lon"])
    total = len(df)

    strong = df[df["pt_access_score"] >= STRONG_PT_THRESHOLD]
    weak = df[df["pt_access_score"] < WEAK_PT_THRESHOLD]

    def _build(label, subset):
        geo = _geo_aggregate(subset) if len(subset) else {
            k: np.nan for k in [
                "mean_home_lat", "mean_home_lon",
                "min_home_lat", "max_home_lat",
                "min_home_lon", "max_home_lon",
            ]
        }
        return pd.DataFrame([{
            "connectivity_type": label,
            "count": len(subset),
            "percentage": round(len(subset) / total * 100, 1) if total else 0,
            **geo,
        }])

    strong_summary = _build(
        "strong (pt_access_score >= {})".format(STRONG_PT_THRESHOLD), strong,
    )
    weak_summary = _build(
        "weak (pt_access_score < {})".format(WEAK_PT_THRESHOLD), weak,
    )

    return strong_summary, weak_summary


def summarize_key_factors(df):
    """⑤ Key factors influencing adoption (scoring dimensions)."""
    _require_columns(df, ["time_score", "convenience_score", "access_score"])

    return pd.DataFrame([
        {"factor": "time_score (FAST)",
         "mean": round(df["time_score"].mean(), 4),
         "median": round(df["time_score"].median(), 4)},
        {"factor": "convenience_score (CONVENIENT)",
         "mean": round(df["convenience_score"].mean(), 4),
         "median": round(df["convenience_score"].median(), 4)},
        {"factor": "access_score (EASY TO ACCESS)",
         "mean": round(df["access_score"].mean(), 4),
         "median": round(df["access_score"].median(), 4)},
    ])


# ---------------------------------------------------------------------------
# Merge & export
# ---------------------------------------------------------------------------

def merge_summaries(commute_df, adoption_df, strong_df, weak_df, factors_df):
    """Combine all summary sections into one long-format DataFrame."""
    rows = []

    for _, r in commute_df.iterrows():
        rows.append({
            "section": "commute_time_distribution",
            "metric": r["commute_group"],
            "count": r["count"],
            "percentage": r["percentage"],
            "value": np.nan,
        })

    for _, r in adoption_df.iterrows():
        rows.append({
            "section": "adoption_potential",
            "metric": r["metric"],
            "count": r["count"],
            "percentage": r["percentage"],
            "value": r["value"],
        })

    for _, r in strong_df.iterrows():
        rows.append({
            "section": "strong_connectivity",
            "metric": r["connectivity_type"],
            "count": r["count"],
            "percentage": r["percentage"],
            "value": r["mean_home_lat"],
            "mean_home_lat": r["mean_home_lat"],
            "mean_home_lon": r["mean_home_lon"],
            "min_home_lat": r["min_home_lat"],
            "max_home_lat": r["max_home_lat"],
            "min_home_lon": r["min_home_lon"],
            "max_home_lon": r["max_home_lon"],
        })

    for _, r in weak_df.iterrows():
        rows.append({
            "section": "weak_connectivity",
            "metric": r["connectivity_type"],
            "count": r["count"],
            "percentage": r["percentage"],
            "value": r["mean_home_lat"],
            "mean_home_lat": r["mean_home_lat"],
            "mean_home_lon": r["mean_home_lon"],
            "min_home_lat": r["min_home_lat"],
            "max_home_lat": r["max_home_lat"],
            "min_home_lon": r["min_home_lon"],
            "max_home_lon": r["max_home_lon"],
        })

    for _, r in factors_df.iterrows():
        rows.append({
            "section": "key_factors",
            "metric": r["factor"],
            "count": np.nan,
            "percentage": np.nan,
            "value": r["mean"],
            "median": r["median"],
        })

    return pd.DataFrame(rows)


def print_report(commute_df, adoption_df, strong_df, weak_df, factors_df):
    """Print all summary sections to the console."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("  FINAL SUMMARY — Deutschlandticket Analysis")
    logger.info("=" * 60)

    logger.info("")
    logger.info("① Commute Time Distribution")
    logger.info("-" * 40)
    for _, r in commute_df.iterrows():
        logger.info("  %-18s  %4d  (%5.1f%%)", r["commute_group"], r["count"], r["percentage"])

    logger.info("")
    logger.info("② Deutschlandticket Adoption Potential")
    logger.info("-" * 40)
    for _, r in adoption_df.iterrows():
        if pd.notna(r["value"]):
            logger.info("  %-35s  %.4f", r["metric"], r["value"])
        else:
            logger.info("  %-35s  %4d  (%5.1f%%)", r["metric"], r["count"], r["percentage"])

    logger.info("")
    logger.info("③ Strong Public Transport Connectivity (pt_access >= %.1f)", STRONG_PT_THRESHOLD)
    logger.info("-" * 40)
    for _, r in strong_df.iterrows():
        logger.info("  Employees: %d (%.1f%%)", r["count"], r["percentage"])
        logger.info("  Centroid:  (%.4f, %.4f)", r["mean_home_lat"], r["mean_home_lon"])
        logger.info("  Lat range: [%.4f, %.4f]", r["min_home_lat"], r["max_home_lat"])
        logger.info("  Lon range: [%.4f, %.4f]", r["min_home_lon"], r["max_home_lon"])

    logger.info("")
    logger.info("④ Weak Public Transport Connectivity (pt_access < %.1f)", WEAK_PT_THRESHOLD)
    logger.info("-" * 40)
    for _, r in weak_df.iterrows():
        logger.info("  Employees: %d (%.1f%%)", r["count"], r["percentage"])
        logger.info("  Centroid:  (%.4f, %.4f)", r["mean_home_lat"], r["mean_home_lon"])
        logger.info("  Lat range: [%.4f, %.4f]", r["min_home_lat"], r["max_home_lat"])
        logger.info("  Lon range: [%.4f, %.4f]", r["min_home_lon"], r["max_home_lon"])

    logger.info("")
    logger.info("⑤ Key Factors Influencing Adoption")
    logger.info("-" * 40)
    for _, r in factors_df.iterrows():
        logger.info("  %-35s  mean=%.4f  median=%.4f", r["factor"], r["mean"], r["median"])

    logger.info("")
    logger.info("=" * 60)


def save_summary(combined_df, path=None):
    path = path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    combined_df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_summary():
    """Load data, build all summary sections, save and print."""
    logger.info("正在加载员工数据 ...")
    df = load_employees()
    logger.info("  共 %d 名员工", len(df))

    logger.info("正在生成通勤时间分布 ...")
    commute_df = summarize_commute_times(df)

    logger.info("正在生成 adoption potential 摘要 ...")
    adoption_df = summarize_adoption(df)

    logger.info("正在分析公共交通可达性 ...")
    strong_df, weak_df = summarize_connectivity(df)

    logger.info("正在汇总关键影响因素 ...")
    factors_df = summarize_key_factors(df)

    combined = merge_summaries(commute_df, adoption_df, strong_df, weak_df, factors_df)

    print_report(commute_df, adoption_df, strong_df, weak_df, factors_df)

    logger.info("正在保存 final_summary_output.csv ...")
    out = save_summary(combined)
    logger.info("已保存 -> %s", out)

    return {
        "commute_time_distribution": commute_df,
        "adoption_potential_summary": adoption_df,
        "strong_connectivity_summary": strong_df,
        "weak_connectivity_summary": weak_df,
        "key_factor_summary": factors_df,
        "combined": combined,
    }


# ---------------------------------------------------------------------------
# Legacy wrappers
# ---------------------------------------------------------------------------

def build_summary():
    """Legacy alias — returns dict of summary DataFrames."""
    return run_summary()


def print_summary():
    """Legacy alias for console output."""
    run_summary()


def main():
    run_summary()


if __name__ == "__main__":
    main()
