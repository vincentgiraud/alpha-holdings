"""Configuration and settings for alpha-holdings.

Reads from environment variables and provides sensible defaults.
"""

import os
from pathlib import Path

# Data storage paths
DATA_STORAGE_PATH = Path(os.getenv("DATA_STORAGE_PATH", "./data"))
DATABASE_URL = os.getenv("DATABASE_URL", "duckdb:///./alpha.duckdb")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")

# Future cloud-storage configuration (used when STORAGE_BACKEND=azure_blob)
AZURE_STORAGE_ACCOUNT_URL = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
AZURE_STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER")
AZURE_STORAGE_PREFIX = os.getenv("AZURE_STORAGE_PREFIX", "alpha-holdings")

# Data source configuration
DATA_SOURCE = os.getenv("DATA_SOURCE", "yahoo")
FALLBACK_DATA_SOURCE = os.getenv("FALLBACK_DATA_SOURCE", "stooq")

# Provider feature flags
ENABLE_EDGAR = os.getenv("ENABLE_EDGAR", "true").lower() == "true"
ENABLE_FRED = os.getenv("ENABLE_FRED", "true").lower() == "true"

# Benchmark configuration
BENCHMARK_SYMBOL = os.getenv("BENCHMARK_SYMBOL", "SPY")
BENCHMARK_PROXY_EQUITY = os.getenv("BENCHMARK_PROXY_EQUITY", "SPY")
BENCHMARK_PROXY_BOND = os.getenv("BENCHMARK_PROXY_BOND", "BND")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")

# Analysis
ANALYSIS_LOOKBACK_DAYS = int(os.getenv("ANALYSIS_LOOKBACK_DAYS", "252"))
CONFIDENCE_LEVEL = float(os.getenv("CONFIDENCE_LEVEL", "0.95"))


def ensure_storage_paths():
    """Create storage directories if they don't exist."""
    DATA_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
