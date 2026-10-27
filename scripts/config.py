from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
JOBS_JSON = DATA_DIR / "jobs.json"

HIRINGCAFE_MCP_URL = "https://hiringcafe.com/api/mcp"

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

# Ask HiringCafe for a much narrower set of recent postings.
# The discovery script will still verify the exact posting date.
POSTED_WITHIN_DAYS = 1

# Request more results for each search, instead of only 10.
RESULTS_PER_QUERY = 25

REQUEST_TIMEOUT_SECONDS = 45
