"""Application configuration loaded from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data" / "HHGOA_IEEE")))
CASES_DIR = Path(os.getenv("CASES_DIR", str(BASE_DIR / "cases")))

# TigerGraph
TG_HOST = os.getenv("TIGERGRAPH_HOST", "")
TG_TOKEN = os.getenv("TIGERGRAPH_TOKEN", "")
TG_GRAPH = os.getenv("TIGERGRAPH_GRAPH", "FraudInvestigation")
TG_USE_SAVANNA = os.getenv("TIGERGRAPH_USE_SAVANNA", "true").lower() == "true"

# LLM — provider priority: openai > anthropic > grok > openrouter > mock
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

def _detect_provider():
    """Auto-detect best available LLM provider."""
    explicit = os.getenv("LLM_PROVIDER", "")
    if explicit:
        return explicit
    if OPENAI_API_KEY:
        return "openai"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if GROK_API_KEY:
        return "grok"
    if OPENROUTER_API_KEY:
        return "openrouter"
    return "mock"

LLM_PROVIDER = _detect_provider()
LLM_MODEL = os.getenv("LLM_MODEL", "")

# Application
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PORT = int(os.getenv("PORT", "8000"))

# Policy thresholds
FRAUD_PROBABILITY_THRESHOLD = float(os.getenv("FRAUD_PROBABILITY_THRESHOLD", "0.70"))
HIGH_RISK_THRESHOLD = float(os.getenv("HIGH_RISK_THRESHOLD", "0.85"))
LOW_RISK_THRESHOLD = float(os.getenv("LOW_RISK_THRESHOLD", "0.15"))
EXPOSURE_THRESHOLD_SAR = float(os.getenv("EXPOSURE_THRESHOLD_SAR", "1000"))
EXPOSURE_THRESHOLD_ESCALATE = float(os.getenv("EXPOSURE_THRESHOLD_ESCALATE", "500"))
EXPOSURE_THRESHOLD_L2 = float(os.getenv("EXPOSURE_THRESHOLD_L2", "2500"))

def ensure_dirs():
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
