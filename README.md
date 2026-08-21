# OSINT Early Warning Dashboard 2.0

This project provides the OSINT Early Warning Dashboard as a local Docker application.

## Public deployment and sharing

The repository includes a Render Blueprint in `render.yaml`. It deploys the same
Docker application at a public HTTPS address and mounts a persistent 1 GB disk for
SQLite, so collected intelligence survives restarts and redeployments.

1. Push this repository to GitHub.
2. Sign in at <https://dashboard.render.com> with GitHub.
3. Select **New + → Blueprint** and choose this repository.
4. Approve the `render.yaml` plan and select **Deploy Blueprint**.
5. When its status is **Live**, share the generated `onrender.com` URL. Viewers do
   not need GitHub, ChatGPT, Docker or a local copy of the project.

The Blueprint uses Render's Starter web service because persistent disks are not
available on its free web-service tier. If persistent history is not required, the
disk block and `OSINT_DB_PATH` can be removed and a free-compatible plan used;
SQLite data would then be temporary.

## Architecture

- **Collection:** Python collectors retrieve GDELT event exports, GDACS alerts, USGS earthquakes and category-specific web-news RSS results.
- **Headline enrichment:** The five selected updates in each category trigger follow-up Google News, Bing News and GDELT DOC searches; matching headline variants are clustered and all distinct reporting publishers are shown.
- **Storage:** SQLite persists signals, country articles and provider collection history in `data/osint-dashboard.sqlite`.
- **Phase 1 pattern recognition:** Similar headlines are clustered into stories, category-level news volume is evaluated with a robust median/MAD z-score, and recurring stories are tracked across refresh windows with a persistence score.
- **Phase 2 context and change detection:** Headlines are checked for supply-chain entities, strategic routes and transport modes. A robust one-sided CUSUM tests whether story volume is moving into a sustained higher range.
- **Phase 3 explainable early warning:** Each pattern receives a warning score based on anomaly strength, persistence, sustained change, source diversity and operational relevance. The interface exposes the component rationale and confidence score.
- **Analytics and presentation:** Streamlit performs filtering, severity display, confidence presentation, country grouping and automated source-grounded summaries.
- **Map:** Plotly displays clickable global sensors for GDELT, GDACS and USGS.
- **Supply-chain risk layer:** Every country has a traffic-light marker derived from stored country news and current GDELT, GDACS and USGS signals. Clicking a marker refreshes the country evidence and shows maritime, aviation or indirect operational exposure, impact score, confidence range and corroborating sources.
- **Hybrid country risk model:** The operational score combines the strongest credible event (35%), company exposure (30%), likelihood × impact (20%) and the recent historical anomaly (15%). Company exposure is entered per profile and country using supplier concentration, goods value, route dependency, inventory vulnerability, customer exposure and substitution difficulty. Where no exposure is configured, the interface explicitly uses a generic hybrid with renormalised evidence weights (50%, 28.6% and 21.4%) rather than assuming that every organisation has the same footprint. Corroboration and critical-escalation guardrails still prevent weak single-source headlines from creating unsupported alerts.
- **Ten-event evidence window:** Country risk is calculated from up to ten latest distinct operational events, while the brief displays five for readability. Event influence decays exponentially by 10% per position (`1.00, 0.90, 0.81, ...`). Multiple distinct corroborated events can earn a bounded concurrency uplift of at most 12 points; repeated coverage of the same incident is consolidated first and weak or single-source reports cannot earn this uplift.
- **Operational-connection evidence gate:** Each report is additionally assessed for an identifiable supply-chain asset/location, a concrete operational consequence, company dependency, official or independent confirmation, and temporal status. The company-specific connection score weights these at 25%, 30%, 20%, 15% and 10%. Without saved company exposure, the remaining evidence weights are renormalised. A report cannot receive a strong operational connection unless an asset/route/location, a concrete consequence, and credible confirmation are all present. The brief displays the extracted assets, locations, consequences and lifecycle state (`Developing`, `Active`, `Recently reported`, `Recovering / resolved`, or `Historical / stale`).
- **Map-wide country refresh:** `Live refresh` now updates global GDELT, GDACS and USGS feeds and then runs lightweight country operational-news snapshots before calculating map colours. It prioritises countries identified by current signals, countries with saved company exposure, the selected country and countries already represented by stored operational reporting. This prevents a country remaining on an old risk colour until its brief is opened.
- **Responsive country selection:** Map and dropdown selections switch immediately without blocking on network collection. The single **Refresh country intelligence** action performs the complete local/international collection, corroborating-source search and Phase 1–3 analysis.
- **Personalised country news:** Named profiles can rate varied stories as Interested or Not interested. A local TF-IDF/cosine-similarity model combines those choices with country relevance, recency, operational risk and source confidence to rank the five stories shown for each selected country. Feedback remains in SQLite and never changes the underlying risk or confidence assessment.
- **Live transport monitoring:** A separate map displays OpenSky aircraft state vectors and AISStream vessel positions for a selected country. Positions older than 30 minutes are excluded, and missing provider data is never presented as zero traffic.
- **Disaster monitoring:** A dedicated GDACS view polls earthquakes, tropical cyclones, floods, volcanoes, droughts and wildfires independently so quieter hazard types are not crowded out. It maps currently present feed events using official green/orange/red alert grades, provides a clearly labelled 0–3 alert gauge, and links each event to the official GDACS report and geographic footprint when available.

### Corpus-trained semantic risk model

The dashboard trains a Positive Pointwise Mutual Information (PPMI) word embedding from the headlines and summaries stored in SQLite. Words receive similar vectors when they repeatedly occur in similar four-word context windows. Article vectors are compared with learned disruption, severity and transport prototypes using cosine similarity.

The model activates after at least 20 stored documents are available. Its semantic contribution is capped at 12 risk points and cannot independently create a Critical assessment. Confirmed operational consequences, independent-source corroboration and primary-source evidence remain separate controls. With insufficient training data, the dashboard transparently uses the existing lexicon fallback.

This provides a local and reproducible trained semantic layer without downloading a large model or sending stored reporting to an external AI service.

### Country-targeted sentiment

Every displayed story and each matching source receives a sentiment score from -100 to +100. The analysis prioritises clauses explicitly naming the selected country, applies weighted positive and negative operational terms, and reverses polarity for common negations such as “no closure” or “avoided closure”. Scores of +15 or above are Positive, -15 or below are Negative, and values in between are Neutral. Sentiment describes how the report frames conditions affecting the country; it is displayed separately and does not alter the operational risk score.

#### Preliminary sentiment evaluation

The reproducible internal benchmark in `evaluation/sentiment_benchmark.csv` contains 30 balanced examples: 10 Positive, 10 Negative and 10 Neutral. Running `python evaluation/evaluate_sentiment.py` produced:

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| Positive | 1.000 | 0.900 | 0.947 | 10 |
| Negative | 1.000 | 0.900 | 0.947 | 10 |
| Neutral | 0.833 | 1.000 | 0.909 | 10 |
| **Macro average** | — | — | **0.935** | **30** |

Accuracy was 0.933. This is a small, internally authored functional benchmark rather than independent external validation. It verifies repeatable classifier behaviour but must not be interpreted as proof of generalisation to multilingual or real-world reporting. A defensible final evaluation should use a larger sample of genuine collected headlines labelled independently by at least two reviewers and report inter-annotator agreement.

### Personalising the news

1. Enter a profile name in the sidebar (for example, `Maritime Manager`).
2. Open **My news interests** and rate at least six varied stories.
3. Select a country on the map or with the country selector.
4. The five country-specific stories are marked **Recommended for you** and ordered for that profile.

Different profile names maintain separate preferences. Ratings can also be changed on
the stories inside a country brief or removed with **Reset this profile**. The model is
content-based, so it can learn from one user's feedback without collecting behaviour
from other users.

### Live flights and vessels

Open **Live transport monitor**, choose a country, then select **Refresh live transport
activity**. OpenSky anonymous access is attempted automatically. For vessel positions,
create a free AISStream API key and place it in `.env`:

```text
AISSTREAM_API_KEY=your_key_here
```

You may also set `OPENSKY_TOKEN` to an OpenSky bearer token. Because OpenSky state
vectors do not guarantee operator class and basic AIS messages can omit vessel type,
the dashboard labels qualifying positions as **likely commercial** and displays other
fresh positions as unclassified. Transport positions remain separate from the country
risk calculation.

Likely-commercial flights are enriched through ADSBDB callsign route records where a
match exists. The monitor then shows departure and destination airports, great-circle
route distance, approximate full-flight duration, remaining distance and estimated
arrival time. Duration and arrival are model estimates based on route geometry and live
ground speed—not airline schedules, filed flight plans or guaranteed arrival times.

## Start

On macOS, double-click `start-dashboard.command`. It builds the current version,
starts it in detached mode, waits for the health check, and opens the dashboard.

Alternatively, from this folder:

```bash
./start-dashboard.command
```

Open <http://localhost:8502>.

The application deliberately uses port **8502**, allowing the existing dashboard on port 8501 to remain running.

## Stop

```bash
docker compose down
```

The database remains in `data/osint-dashboard.sqlite` after the container stops.

## Reduce Docker resource usage

On macOS, double-click `optimise-docker.command`. It stops only the older
port-8501 dashboard and collector, clears unused build/dangling-image cache,
and recreates this dashboard with a 2 GB memory limit. SQLite data is preserved.

## Reset stored data

Stop the application, move or remove `data/osint-dashboard.sqlite`, then start it again. SQLite will recreate the schema automatically.

## Notes

- The first refresh can take several seconds because the application collects three live providers.
- Click a sensor to update the event-intelligence panel.
- Select a country and choose **Generate / refresh country brief** to store up to five latest articles in each category.
- Automated summaries are feed-description and headline based; they are not generated by a separate LLM.
- Pattern detection starts immediately, but a volume-anomaly baseline requires five successful country/category refresh windows. A zero anomaly value is therefore labelled **Baseline building**, not “no risk”.
- Pattern scores support analyst prioritisation. They do not establish that an event is true, predict probability, or replace verification against the linked sources.
- Public providers can rate-limit or temporarily reject requests. Provider status remains visible and historical records remain in SQLite.
