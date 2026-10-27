
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
JOBS_JSON = DATA_DIR / "jobs.json"

HIRINGCAFE_MCP_URL = "https://hiringcafe.com/api/mcp"

# Broad searches so we don't exclude too many potentially relevant roles.
SEARCH_QUERIES = [
    "software developer",
    "software engineer",
    "IT analyst",
    "automation developer",
    "Power Platform developer",
]

LOCATION = "Toronto, ON"

WORKPLACE_TYPES = ["Remote", "Hybrid", "Onsite"]
EMPLOYMENT_TYPES = ["Full Time", "Contract"]
SENIORITY_LEVELS = ["Entry Level", "Mid Level"]

MAX_YEARS_EXPERIENCE = 5
POSTED_WITHIN_DAYS = 14
RESULTS_PER_QUERY = 10

REQUEST_TIMEOUT_SECONDS = 45
