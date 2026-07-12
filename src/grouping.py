"""Commute-time grouping based on Google Directions transit times."""

import logging
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees_synthetic.csv"
SUMMARY_PATH = DATA_DIR / "commute_time_summary.csv"

GROUP_ORDER = [
    "≤30 min",
    "30–45 min",
    "45–60 min",
    ">60 min",
    "No transit data",
]

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading / saving
# ---------------------------------------------------------------------------

def load_employees(path=None):
    """Load enriched employee CSV."""
    path = path or EMPLOYEES_PATH
    return pd.read_csv(path)


def save_employees(df, path=None):
    """Write employee DataFrame back to CSV."""
    path = path or EMPLOYEES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def save_summary(summary_df, path=None):
    """Write commute-group summary to CSV."""
    path = path or SUMMARY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Grouping logic
# ---------------------------------------------------------------------------

def _assign_commute_group(transit_minutes):
    """Map transit_time_minutes to a commute_group label."""
    if pd.isna(transit_minutes):
        return "No transit data"
    if transit_minutes <= 30:
        return "≤30 min"
    if transit_minutes <= 45:
        return "30–45 min"
    if transit_minutes <= 60:
        return "45–60 min"
    return ">60 min"


def group_commute_time(employees_df):
    """Add commute_group column based on transit_time_minutes.

    Group rules
    -----------
    ≤30 min       : transit_time_minutes <= 30
    30–45 min     : 30 < transit_time_minutes <= 45
    45–60 min     : 45 < transit_time_minutes <= 60
    >60 min       : transit_time_minutes > 60
    No transit data : NaN
    """
    if "transit_time_minutes" not in employees_df.columns:
        logger.error("错误: 缺少 transit_time_minutes 字段。")
        logger.error("  请先运行: python src/commute.py")
        sys.exit(1)

    result = employees_df.copy()
    result["commute_group"] = result["transit_time_minutes"].apply(_assign_commute_group)
    return result


def summarize_commute_groups(employees_df):
    """Compute count and percentage for each commute_group."""
    if "commute_group" not in employees_df.columns:
        employees_df = group_commute_time(employees_df)

    total = len(employees_df)
    counts = employees_df["commute_group"].value_counts()

    summary = pd.DataFrame({
        "group": counts.index,
        "count": counts.values,
    })
    summary["percentage"] = (summary["count"] / total * 100).round(1)

    # Sort by predefined order; append any unexpected groups at the end
    order_map = {g: i for i, g in enumerate(GROUP_ORDER)}
    summary["_sort"] = summary["group"].map(lambda g: order_map.get(g, len(GROUP_ORDER)))
    summary = summary.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    return summary


def print_summary(summary_df):
    """Print group statistics to the console."""
    logger.info("")
    logger.info("=== Commute Time Group Summary ===")
    for _, row in summary_df.iterrows():
        logger.info("  %-18s  %4d  (%5.1f%%)", row["group"], row["count"], row["percentage"])
    logger.info("  %-18s  %4d", "Total", summary_df["count"].sum())


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_grouping():
    """Load employees, assign groups, summarize, and save results."""
    logger.info("正在加载员工数据 ...")
    employees = load_employees()
    logger.info("  共 %d 名员工", len(employees))

    logger.info("正在分组 (基于 transit_time_minutes) ...")
    grouped = group_commute_time(employees)

    logger.info("正在统计各组人数与占比 ...")
    summary = summarize_commute_groups(grouped)
    print_summary(summary)

    logger.info("正在保存文件 ...")
    emp_out = save_employees(grouped)
    sum_out = save_summary(summary)
    logger.info("员工数据已保存 -> %s", emp_out)
    logger.info("分组统计已保存 -> %s", sum_out)

    return grouped, summary


# ---------------------------------------------------------------------------
# Legacy wrappers (notebook / summary pipeline)
# ---------------------------------------------------------------------------

def group_by_commute_time(df):
    """Legacy alias — returns summary DataFrame from employees or results."""
    if "transit_time_minutes" in df.columns or "commute_group" in df.columns:
        if "commute_group" not in df.columns:
            df = group_commute_time(df)
        return summarize_commute_groups(df).rename(columns={"group": "commute_bucket"})

    # Old commute_results format
    if "commute_time_min" in df.columns:
        temp = df.rename(columns={"commute_time_min": "transit_time_minutes"})
        temp = group_commute_time(temp)
        return summarize_commute_groups(temp).rename(columns={"group": "commute_bucket"})

    raise KeyError("DataFrame must contain transit_time_minutes or commute_time_min")


def group_by_department(employees, results=None):
    """Legacy department grouping (requires department column)."""
    if results is not None:
        merged = employees.merge(results, on="employee_id")
    else:
        merged = employees

    if "department" not in merged.columns:
        return pd.DataFrame(columns=["department", "count", "avg_commute_min"])

    commute_col = "transit_time_minutes" if "transit_time_minutes" in merged.columns else "commute_time_min"
    return merged.groupby("department").agg(
        count=("employee_id", "count"),
        avg_commute_min=(commute_col, "mean"),
    ).reset_index()


if __name__ == "__main__":
    run_grouping()
