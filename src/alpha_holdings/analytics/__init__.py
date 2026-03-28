"""Performance analytics, attribution, and reporting."""

from .attribution import AttributionResult, FactorExposure, compute_factor_attribution
from .html_report import render_html_report
from .performance import PerformanceReport, compute_report_from_nav, generate_report

__all__ = [
    "AttributionResult",
    "FactorExposure",
    "PerformanceReport",
    "compute_factor_attribution",
    "compute_report_from_nav",
    "generate_report",
    "render_html_report",
]
