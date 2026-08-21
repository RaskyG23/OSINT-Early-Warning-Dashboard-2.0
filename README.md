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

- **Collection:** Python collectors retrieve GDELT events and documents, GDACS alerts, USGS earthquakes, Google and Bing RSS results, official government/port/aviation notices, OpenSky aircraft states, ADSBDB route metadata and supported open maritime feeds. Provider outcomes and failures are stored separately so “no data returned” is not presented as “no risk”.
- **Story consolidation:** Country reporting is filtered for concrete supply-chain relevance, then matching headline variants are clustered into one operational event. The displayed headline and summary synthesise the shared facts instead of copying one publisher's headline. Local and international reporting sources remain separately listed, clickable and labelled with their country of origin where known.
- **Storage:** SQLite persists signals, country articles and provider collection history in `data/osint-dashboard.sqlite`.
- **Phase 1 pattern recognition:** Similar headlines are clustered into stories, category-level news volume is evaluated with a robust median/MAD z-score, and recurring stories are tracked across refresh windows with a persistence score.
- **Phase 2 context and change detection:** Headlines are checked for supply-chain entities, strategic routes and transport modes. A robust one-sided CUSUM tests whether story volume is moving into a sustained higher range.
- **Phase 3 explainable early warning:** Each pattern receives a warning score based on anomaly strength, persistence, sustained change, source diversity and operational relevance. The interface exposes the component rationale and confidence score.
- **Analytics and presentation:** Streamlit performs filtering, clustering, ranking, severity display, confidence presentation, country grouping and automated source-grounded synthesis.
- **Map:** Plotly renders a country-level choropleth. Hovering identifies a country; countries are coloured green, yellow or red for Low, Moderate or Critical operational risk. Clicking a country opens its intelligence brief, and a searchable country selector provides the same workflow without using the map.
- **Supply-chain risk layer:** Country colours are derived from stored operational news, GDELT, GDACS, USGS and configured company exposure. The country brief explains maritime, aviation or other transport exposure, impact, assessment confidence, evidence quality and corroborating sources.
- **Hybrid country risk model:** The operational score combines the strongest credible event (35%), company exposure (30%), likelihood × impact (20%) and the recent historical anomaly (15%). Company exposure is entered per profile and country using supplier concentration, goods value, route dependency, inventory vulnerability, customer exposure and substitution difficulty. Where no exposure is configured, the interface explicitly uses a generic hybrid with renormalised evidence weights (50%, 28.6% and 21.4%) rather than assuming that every organisation has the same footprint. Corroboration and critical-escalation guardrails still prevent weak single-source headlines from creating unsupported alerts.
- **Ten-event evidence window:** Country risk is calculated from up to ten latest distinct operational events, while the brief displays five for readability. Event influence decays exponentially by 10% per position (`1.00, 0.90, 0.81, ...`). Multiple distinct corroborated events can earn a bounded concurrency uplift of at most 12 points; repeated coverage of the same incident is consolidated first and weak or single-source reports cannot earn this uplift.
- **Operational-connection evidence gate:** Each report is additionally assessed for an identifiable supply-chain asset/location, a concrete operational consequence, company dependency, official or independent confirmation, and temporal status. The company-specific connection score weights these at 25%, 30%, 20%, 15% and 10%. Without saved company exposure, the remaining evidence weights are renormalised. A report cannot receive a strong operational connection unless an asset/route/location, a concrete consequence, and credible confirmation are all present. The brief displays the extracted assets, locations, consequences and lifecycle state (`Developing`, `Active`, `Recently reported`, `Recovering / resolved`, or `Historical / stale`).
- **Map-wide country refresh:** `Live refresh` now updates global GDELT, GDACS and USGS feeds and then runs lightweight country operational-news snapshots before calculating map colours. It prioritises countries identified by current signals, countries with saved company exposure, the selected country and countries already represented by stored operational reporting. This prevents a country remaining on an old risk colour until its brief is opened.
- **Responsive country selection:** Map and dropdown selections update the selected country immediately. The dashboard uses cached intelligence first and performs bounded refreshes where current evidence is missing or stale, avoiding a long network request before anything is displayed.
- **Personalised country news:** The right-side **General news interests** panel presents ten current general stories for a separately selected country. Each story shows its automatically assigned categories, publication date, sources and Interested/Not interested controls. A local profile-specific TF-IDF/cosine-similarity model combines those choices with country relevance, recency, operational risk and source confidence to rank the operational stories subsequently shown in country briefs. Preferences remain in SQLite and affect ranking only; they never change risk or confidence scores.
- **Live transport monitoring:** A separate map displays likely dedicated cargo aircraft and commercial-vessel positions for a selected monitoring area. OpenSky provides aircraft state vectors and ADSBDB supplies route metadata where available. The maritime pipeline combines Fintraffic's official open AIS feed, optional BarentsWatch Norwegian AIS and optional Global Fishing Watch identity/port-visit enrichment. Positions older than 30 minutes are excluded, and missing provider data is never presented as zero traffic.
- **Disaster monitoring:** A dedicated GDACS view polls earthquakes, tropical cyclones, floods, volcanoes, droughts and wildfires independently so quieter hazard types are not crowded out. It maps active feed events using official green/orange/red GDACS grades, displays the source's 0–3 alert gauge, timestamp, severity details, geographic footprint and a clickable official report. Disaster-related country stories are cross-checked against applicable GDACS and USGS detections, GDELT-indexed reporting and independent news families. The brief labels evidence as officially confirmed, news-corroborated or unconfirmed instead of treating every disaster headline as verified.

### Country supply-chain intelligence brief

Selecting a country on the map or with the searchable selector opens one brief with two tabs:

- **Current trend** displays the five latest distinct, country-specific stories that have a credible connection to supply-chain operations. Results are ordered from latest to least latest. Each card contains a consolidated event headline, automated source-grounded summary, date and UTC time, transport mode, impact score, confidence interval, operational-connection evidence, country-targeted sentiment, lifecycle status and a stakeholder-oriented recommended action. All matched local, international and official sources are clickable and their origin country is shown where it can be identified.
- **Historical trend** displays up to ten earlier stored operational stories for that country. Similar past events are used to provide context and to support the recommended action shown with a current event; historical similarity does not by itself increase the current event's credibility.

Country selection is stricter than simple name matching: aliases such as United Kingdom/UK, United States/USA/America and Netherlands/Holland are normalised, but a story must still concern an operation, asset, route or consequence affecting the selected country. Repeated variants of the same event are consolidated before the five current stories are chosen.

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

1. Enter a profile name in the sidebar (for example, `Supply Chain Manager`).
2. In the right-side **General news interests** panel, select any country and refresh its ten general stories when needed.
3. Mark stories **Interested** or **Not interested**. The panel remains open as feedback is recorded.
4. Select an operational country on the map or with the country selector.
5. The country brief ranks its five operational stories for that named profile while preserving the underlying evidence and risk calculations.

Different profile names maintain separate preferences. The model is content-based, so it
learns from that profile's own feedback without requiring behaviour from other users.
Preference weights decay exponentially with a 90-day half-life so recent interests carry
more influence while older choices remain recoverable from SQLite.

### Live flights and vessels

Open **Live transport monitor**, choose a country, then select **Refresh live transport
activity**. OpenSky anonymous access is attempted automatically. Fintraffic works without
credentials in Finnish waters. Optional free credentials enable additional enrichment:

```text
BARENTSWATCH_CLIENT_ID=your_client_id
BARENTSWATCH_CLIENT_SECRET=your_client_secret
GFW_API_ACCESS_TOKEN=your_non_commercial_token
```

BarentsWatch adds live Norwegian Coastal Administration AIS after a free API client is
created. Global Fishing Watch enriches supported live records with registry identity and
historical port-visit context under its non-commercial API terms. Public MarineTraffic and
VesselFinder links remain available when no embeddable machine-readable feed covers the
selected country. You may also set `OPENSKY_TOKEN` to an OpenSky bearer token.

Aircraft are blue triangles and vessels are green diamonds. Clicking a flight or vessel
marker resolves its stable identifier (`ICAO24` or `MMSI`), scrolls directly to the
corresponding detail section and opens the selected record. Vessel cards display the
available name, MMSI, IMO, call sign, position, ship class, speed, course, draught,
AIS-declared destination, ETA, last port and contributing source. AIS identifies a broad
ship/cargo class; it does not expose the actual goods or cargo manifest.

Because OpenSky state vectors do not guarantee operator class and AIS messages can omit
static voyage fields, the dashboard labels qualifying records as **likely** cargo or
commercial activity and displays other fresh positions as unclassified. Transport
positions remain separate from the country risk calculation.

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

- The first refresh can take several seconds because several independent public providers have different response times and rate limits.
- Hover over the risk map to identify countries; click a country or use the searchable selector to open its brief.
- The country brief shows five current operational events and up to ten historical events rather than separate generic news categories.
- Automated headlines and summaries are source-grounded synthesis produced from collected titles, descriptions and matched evidence; the local Docker version does not require a separate hosted LLM.
- Pattern detection starts immediately, but a volume-anomaly baseline requires five successful country/category refresh windows. A zero anomaly value is therefore labelled **Baseline building**, not “no risk”.
- Pattern scores support analyst prioritisation. They do not establish that an event is true, predict probability, or replace verification against the linked sources.
- Public providers can rate-limit or temporarily reject requests. Provider status remains visible and historical records remain in SQLite.
