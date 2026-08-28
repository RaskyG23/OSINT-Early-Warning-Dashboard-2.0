# Examiner access guide

## Recommended route: browser only

Open the public Streamlit dashboard link supplied with the submission. No
Docker, Python, GitHub account, ChatGPT account, plug-in or local project
download is required.

The hosted dashboard is an examination/demo deployment. Its SQLite history can
reset when the free hosting service restarts. This does not change the source
code or the documented analytical methods.

## View the associated files online

The public GitHub repository can be inspected in a browser:

- `README.md` explains the architecture, data sources and dashboard controls.
- `app/collectors.py` contains public-source ingestion and source consolidation.
- `app/supply_chain.py` contains relevance, sentiment and operational-risk logic.
- `app/patterns.py` contains clustering, robust anomaly, persistence and CUSUM.
- `app/embeddings.py` contains the corpus-trained PPMI model.
- `app/recommender.py` contains profile feedback and personalised ranking.
- `app/forecasting.py` contains short-horizon weighted regression.
- `app/database.py` contains the SQLite schema and retrieval functions.
- `app/dashboard.py` contains the Streamlit interface and interaction workflow.
- `evaluation/` and `tests/` contain reproducible evaluation evidence.

GitHub displays these text files directly in the browser. Downloading or
cloning the repository is optional.

## Suggested five-minute examination

1. Open the public dashboard URL.
2. Select **Operations overview** and inspect provider-health status.
3. Hover over the risk map and select a country, or use the country selector.
4. Review the five current events, risk level, confidence and source links.
5. Open **Historical trend** and **Forecast**.
6. Open **Disaster monitor** and select a current GDACS record if available.
7. Use the GitHub links above to trace one displayed method to its source code.

## Local fallback

The portable ZIP and Docker instructions are retained only as a reproducible
fallback. They are not required for normal examination of the hosted dashboard.
