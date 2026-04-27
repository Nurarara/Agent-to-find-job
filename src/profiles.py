"""
profiles.py - Candidate-specific search and scoring configuration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateProfile:
    key: str
    label: str
    first_name: str
    search_prompt: str
    role_keywords: dict[str, list[str]]
    skill_weights: list[tuple[str, float]]
    exclude_keywords: list[str]
    junior_bonus_keywords: list[str]
    senior_penalty_keywords: list[str]
    outreach_context: str


RON_ROLE_KEYWORDS = {
    "Data Engineer": ["data engineer", "etl", "pipeline", "dbt", "airflow", "spark", "bigquery"],
    "Analytics Engineer": ["analytics engineer", "dbt", "semantic layer", "metrics layer"],
    "AI/ML Engineer": ["ml engineer", "machine learning", "ai engineer", "llm", "nlp", "rag", "pytorch"],
    "Data Scientist": ["data scientist", "forecasting", "experiment", "statistical", "model"],
    "BI Analyst/Engineer": ["bi ", "business intelligence", "power bi", "tableau", "looker", "reporting"],
    "Data Analyst": ["data analyst", "sql analyst", "product analyst", "insight analyst"],
    "Python/Backend": ["python developer", "backend engineer", "software engineer python"],
}

HEBA_ROLE_KEYWORDS = {
    "Junior AI/ML": ["junior ai", "junior ml", "machine learning", "ai engineer", "nlp", "tensorflow"],
    "Junior Data Scientist": ["junior data scientist", "data scientist", "machine learning", "nlp", "model"],
    "Data Analyst": ["data analyst", "insight analyst", "python", "pandas", "sql", "power bi"],
    "Technical Consultant": ["technical consultant", "technology consultant", "tech assurance", "implementation consultant"],
    "Junior Pricing Analyst": ["pricing analyst", "pricing", "commercial analyst", "revenue analyst"],
    "Exposure Management Analyst": ["exposure management", "catastrophe", "cat modelling", "risk analyst", "insurance"],
    "QA/Data Automation": ["sdet", "qa automation", "test automation", "selenium", "data validation"],
}

RON_WEIGHTS = [
    ("python", 3.0), ("sql", 2.5), ("data engineer", 3.0), ("data engineering", 3.0),
    ("etl", 2.5), ("pipeline", 2.0), ("machine learning", 3.0), ("ml", 2.0),
    ("deep learning", 2.5), ("pytorch", 2.5), ("tensorflow", 2.0), ("scikit", 2.0),
    ("hugging face", 2.5), ("transformers", 2.5), ("nlp", 2.5), ("llm", 2.5),
    ("ai engineer", 3.0), ("generative ai", 2.5), ("gcp", 2.5), ("google cloud", 2.0),
    ("bigquery", 2.5), ("spark", 2.0), ("airflow", 2.0), ("dbt", 2.0), ("kafka", 1.5),
    ("aws", 1.5), ("azure", 1.5), ("data scientist", 2.5), ("analytics engineer", 2.5),
    ("power bi", 2.5), ("business intelligence", 2.5), ("bi developer", 2.5),
    ("data analyst", 2.0), ("tableau", 1.5), ("looker", 1.5), ("dashboard", 1.0),
    ("typescript", 1.5), ("api", 1.0), ("docker", 1.5), ("kubernetes", 1.5),
    ("graduate", 0.5), ("junior", 1.0), ("london", 1.5), ("hybrid", 1.0), ("remote", 1.0),
]

HEBA_WEIGHTS = [
    ("junior", 3.0), ("entry level", 3.0), ("graduate", 3.0), ("associate", 2.0),
    ("python", 3.0), ("sql", 2.5), ("pandas", 2.5), ("excel", 1.5), ("power bi", 2.5),
    ("data analyst", 3.0), ("insight analyst", 2.5), ("data scientist", 3.0),
    ("machine learning", 3.0), ("ml", 2.0), ("ai", 2.0), ("nlp", 2.5), ("tensorflow", 2.5),
    ("scikit", 2.0), ("langchain", 2.0), ("elasticsearch", 2.0), ("bm25", 1.5),
    ("technical consultant", 3.0), ("technology consultant", 2.5), ("tech assurance", 2.0),
    ("pricing analyst", 3.0), ("commercial analyst", 2.0), ("revenue analyst", 2.0),
    ("exposure management", 3.0), ("risk analyst", 2.5), ("insurance", 1.5),
    ("qa automation", 2.0), ("sdet", 2.0), ("selenium", 2.0), ("data validation", 2.0),
    ("kpmg", 1.0), ("audit", 1.0), ("sox", 1.0), ("london", 1.5), ("hybrid", 1.0), ("remote", 1.0),
]

COMMON_EXCLUDES = [
    "senior staff", "principal", "vp of", "head of", "director", "staff engineer",
    "c++", "c#", ".net", "ios developer", "android developer", "embedded", "sap consultant",
]

PROFILES = {
    "ron": CandidateProfile(
        key="ron",
        label="Ron",
        first_name="Rounak",
        search_prompt=(
            "Find jobs posted in the last 5 days for Data Analyst, Data Engineer, AI/ML Engineer, "
            "BI Analyst, BI Engineer, Analytics Engineer, Data Scientist, Power BI Developer, and "
            "other relevant Python/data roles in London, hybrid, or remote UK. Use one page per role. "
            "Exclude Java, .NET, C#, director, VP, staff, and principal roles."
        ),
        role_keywords=RON_ROLE_KEYWORDS,
        skill_weights=RON_WEIGHTS,
        exclude_keywords=COMMON_EXCLUDES + ["java "],
        junior_bonus_keywords=["graduate", "junior", "entry level", "associate"],
        senior_penalty_keywords=["senior", "principal", "staff", "lead", "manager", "head of", "director"],
        outreach_context="Data/ML engineer with Python, SQL, GCP, BigQuery, Power BI, ML and BI delivery experience.",
    ),
    "heba": CandidateProfile(
        key="heba",
        label="Heba",
        first_name="Heba",
        search_prompt=(
            "Find jobs posted in the last 5 days for junior or entry-level AI/ML Engineer, Data Scientist, "
            "Data Analyst, Technical Consultant, Junior Pricing Analyst, Junior Exposure Management Analyst, "
            "Risk Analyst, QA/Data Automation, and Power BI roles in London, hybrid, or remote UK. Use two pages "
            "per role. Exclude senior, lead, principal, manager, director, VP, and roles requiring 4+ years."
        ),
        role_keywords=HEBA_ROLE_KEYWORDS,
        skill_weights=HEBA_WEIGHTS,
        exclude_keywords=COMMON_EXCLUDES + ["senior", "lead ", "manager", "4+ years", "5+ years", "7+ years"],
        junior_bonus_keywords=["junior", "entry level", "graduate", "associate", "trainee", "internship", "0-2 years"],
        senior_penalty_keywords=["senior", "lead", "principal", "manager", "head of", "director", "4+ years", "5+ years"],
        outreach_context=(
            "MSc Artificial Intelligence distinction graduate with KPMG tech assurance, SDET automation, "
            "Python/Pandas/SQL, TensorFlow/NLP, Power BI, Selenium, and LegalBERT/Elasticsearch project work."
        ),
    ),
}


def get_profile(profile_key: str | None) -> CandidateProfile:
    return PROFILES.get((profile_key or "ron").lower(), PROFILES["ron"])
