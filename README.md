# Hospital Towel Tracking Dashboard (POC)

A portable, single-file **Pure HTML** dashboard for tracking hospital towels using RFID/EPCIS standard events. The entire dashboard — HTML, CSS, JavaScript, and synthetic data — is embedded in one self-contained file for easy sharing.

---

## 📋 Project Goals

| Goal | Detail |
|---|---|
| **Portability** | One `.html` file; open in any modern browser, no server needed |
| **EPCIS Standard** | Events follow the global RFID/GS1 EPCIS payload structure |
| **Multi-Ward** | Simulates a 20-bed hospital across 4 wards (5 beds per ward) |
| **10:1 Par Level** | ~193 towels for 20 beds ensures enough stock in rotation, laundry, and buffer |
| **Advanced Analytics** | Five reports covering usage, stock, lifecycle, cycle times, and forecasting |
| **Lifecycle Continuity** | Items decommission at end-of-life and are replenished with new stock to keep snapshot metrics realistic |

---

## 🏥 Hospital Configuration

- **Total beds**: 20 (5 per ward)
- **Wards**: `1`, `2`, `3`, `4`
- **Towels tracked**: ~193 individual items with heterogeneous age distribution
- **Par Level Target**: 10:1 (10 towels per bed in total circulation)
- **Simulation period**: 2025-01-01 → 2025-05-01 (**120 days**)
- **Total EPCIS events generated**: ~50,000+
- **Lifecycle policy**: retire at 100 wash cycles (`DECOMMISSION` event) + replacement towel arrival after 1–7 days

---

## 🔄 Towel Status Model

Items transition through four real-world states:

| Status | Trigger Condition |
|---|---|
| **New** | In `New Linen Department`; 0 wash cycles completed |
| **Washing** | Currently checked IN to `Laundry Department` |
| **Clean** | Currently checked IN to `Cleaned Linen Department` |
| **Dirty** | Checked IN to **any ward** — instantly marked dirty on arrival |

---

## 🔄 Towel Life Cycle Flow

```
New Linen Department
       ↓
 Laundry Department  ←────────────────────────────┐
       ↓                                            │
Cleaned Linen Dept                            Ward 1–4
       ↓                                            │
  Ward 1–4    ──────────────────────────────────────┘
                (towel → Dirty on IN; loops back to Laundry)
```

Towels rotate faster than bed linen (shorter ward dwell, faster laundry turnaround).

Operational note:
- **New Linen Department** is for brand-new stock staging.
- After a towel has entered ward usage, the operating loop is **Ward → Laundry → Clean Storage → Ward**.

Compliance anomalies simulated:
- 3% of items skip Laundry (Ward → Clean Storage directly)
- 2% of items skip first wash (New Department → Ward direct)

---

## 📊 Dashboard KPIs (Scorecards)

| Scorecard | Name | Logic |
|---|---|---|
| **1** | **Total Towel Inventory** | Count of all tracked EPCs |
| **2** | **Avg. Life Cycles** | Mean wash count across all active items |
| **3** | **Estimated Monthly Need** | Projected replenishment based on retirement and loss rate |
| **4** | **Towel Ward Coverage** | Towels currently in wards vs. 20-bed target |
| **5** | **Par Level Ratio** | Current active towels ÷ bed count (target: 10.0) |

---

## 📊 Reports

### 1. Towel Usage by Ward
Bar chart of daily towel `IN` events at any ward — last 30 days; filterable per ward via dropdown.

### 2. Current Stock Levels
Doughnut chart showing how many items are currently at each stage at the snapshot time.
`New Linen` | `In Laundry` | `Clean Storage` | `In Wards`

Counting rule: an item is counted in a stage only when its latest event is an open `IN` for that stage (no corresponding `OUT` yet).

### 3. Towel Life Cycle Analysis
Bar chart bucketing all items by wash-cycle age:
- **New (0–20 cycles)**
- **Active (21–70 cycles)**
- **Old (71–99 cycles)**
- **Retired (100+ cycles)**

Initial cycles are read from the `INIT` meta-event embedded at the start of each item's history, enabling accurate age tracking across the full simulation window.

### 4a. Linen Cycle Time Duration
Interactive histogram showing how long items are currently parked at a selected stage (snapshot dwell time).
Toggle buttons: **New Linen | Laundry | Storage | Ward | Debug Total**

Stage-specific dwell units:
- **Laundry**: hours bucketed as `2h, 4h, 6h, 8h, 10h, 12h, 18h, 24h`
- **Ward**: hours bucketed as `2h, 4h, 6h, 8h, 10h, 12h, 18h, 24h`
- **Clean Storage**: days bucketed as `Day 1` to `Day 7`
- **New Linen**: days bucketed as `Day 1` to `Day 7`

Consistency rule: the sum of 4a bars for a selected stage must equal that stage's count in Report 2.

`Debug Total` mode shows a breakdown bar chart matching the Stock Levels doughnut exactly — use this to cross-verify both charts.

### 4b. Towel Status Snapshot
Doughnut chart of current New (Unwashed) / Washing / Clean (Ready) / Dirty counts.
This chart mirrors Report 2 using the same open-`IN` snapshot logic.

### 5. Usage Forecast (60-Day Projection)
Line chart overlaying:
- **Historical** daily ward-IN events (last 60 days, 7-day rolling average)
- **Forecasted** trend for 60 days ahead (drift model based on recent average)

Forecast KPI cards:
- Average daily ward IN events
- Items near retirement (≥70 cycles)
- Estimated days until next retirement wave
- Estimated monthly replenishment need

---

## 🛠 Technical Stack

| Layer | Technology |
|---|---|
| Frontend | HTML5, CSS3 (custom, responsive 3×2 grid) |
| Visualization | Chart.js 4 (Bar, Doughnut, Line) |
| Data | Embedded JSON (`const rawData`) — no network requests |
| Data Generation | Python 3 (`generate_epcis_data.py`) |

---

## 📡 EPCIS Payload Fields

Each scan event (`IN` / `OUT`) carries:

```jsonc
{
  "Event GUID":      "uuid-v4",
  "Event Timestamp": "2025-04-12T08:23:00Z",
  "Job ID":          "JOB-XXXXX",
  "RFID Device ID":  "RFID-RDR-XX",
  "Android App ID":  "LinenTrack-v2.1",
  "Staff ID":        "ST-XXXX",
  "Location":        "Ward 3",         // or "Laundry Department" etc.
  "Process":         "IN",             // IN | OUT | INIT | DECOMMISSION
  "Item Description":"Bath Towel - Large",
  "GTIN":            "urn:epc:idpat:sgtin:...",
  "EPC":             "urn:epc:id:sgtin:..."
}
```

`INIT` events additionally carry:
```jsonc
{
  "Process":        "INIT",
  "Initial Cycles": 42,
  "Home Ward":      "Ward 3"
}
```

`DECOMMISSION` events additionally carry:
```jsonc
{
  "Process":      "DECOMMISSION",
  "Final Cycles": 100,
  "Reason":       "End of Life"
}
```

---

## 🗂 Project Files

| File | Purpose |
|---|---|
| `Bed Linen Tracking Dashboard demo v4.html` | **Main deliverable** — fully self-contained single-file dashboard |
| `generate_epcis_data.py` | Python generator producing 50k+ EPCIS events for ~193 towels |
| `epcis_events.json` | Raw output from the generator |
| `build_v4_rebuild.py` | Injects JSON into the HTML and rewrites the script block |
| `README.md` | This file |

---

## 🚀 Running

1. Open `Bed Linen Tracking Dashboard demo v4.html` in any browser.
2. No installation, no server, no dependencies.

**To regenerate data:**
```bash
python generate_epcis_data.py
python build_v4_rebuild.py
```

---

*Conceptual Design | Subject to Final Requirements Definition | TradeLink 2026*
*Disclaimer: POC is risk/cost-free; RFID hardware costs apply for full implementation.*
