"""
build_v4_rebuild.py
Builds the towel v4 dashboard by injecting fresh EPCIS JSON and script logic
into a clean towel-only HTML template.
"""
import json, re

with open("epcis_events.json", "r") as f:
    raw_json = f.read()

with open("towel_dashboard_v4_template.html", "r", encoding="utf-8") as f:
    html = f.read()

# ── 2. Build the new script block ─────────────────────────────────────────────
NEW_SCRIPT = r"""<script>
        const rawData = __RAWDATA__;

        // ── Data Processing Engine ─────────────────────────────────────────
        function processData(data) {
            const inventory       = {};
            const usageByDate     = {};
            const complianceAlerts = [];
            const allDwells       = {
                'New Linen Department':     [],
                'Laundry Department':       [],
                'Cleaned Linen Department': [],
                'Ward': []          // any ward bucket
            };
            const initMeta = {};    // EPC -> { initialCycles, homeWard }

            data.sort((a, b) => new Date(a['Event Timestamp']) - new Date(b['Event Timestamp']));

            data.forEach(ev => {
                const epc  = ev['EPC'];
                const loc  = ev['Location'];
                const proc = ev['Process'];
                const time = new Date(ev['Event Timestamp']);
                const dateStr = time.toISOString().split('T')[0];

                // INIT meta-events (carry starting cycle count)
                if (proc === 'INIT') {
                    initMeta[epc] = {
                        initialCycles: ev['Initial Cycles'] || 0,
                        homeWard: ev['Home Ward'] || ''
                    };
                    return;
                }

                if (!inventory[epc]) {
                    inventory[epc] = {
                        epc,
                        type: ev['Item Description'],
                        gtin: ev['GTIN'],
                        currentLocation: loc,
                        lastInTime: time,
                        cycles: 0,
                        history: []
                    };
                }

                const item = inventory[epc];

                // Dwell calculation on OUT
                if (proc === 'OUT' && item.history.length > 0) {
                    const last = item.history[item.history.length - 1];
                    if (last.Process === 'IN' && last.Location === loc) {
                        const dh = (time - new Date(last['Event Timestamp'])) / 3600000;
                        const bucket = loc.startsWith('Ward') ? 'Ward' : loc;
                        if (allDwells[bucket] !== undefined) allDwells[bucket].push(dh);
                    }
                }

                // Usage: IN at any ward
                if (loc.startsWith('Ward') && proc === 'IN') {
                    usageByDate[dateStr] = (usageByDate[dateStr] || 0) + 1;
                    item.lastInTime = time;
                }

                // Cycle count: IN at Laundry
                if (loc === 'Laundry Department' && proc === 'IN') {
                    item.cycles += 1;
                }

                // Compliance: illegal skips
                if (proc === 'IN' && item.history.length > 0) {
                    const prev = item.currentLocation;
                    if (prev.startsWith('Ward') && loc === 'Cleaned Linen Department') {
                        complianceAlerts.push({ epc, type: 'Skipped Laundry (Ward→Storage)', date: dateStr });
                    }
                    if (prev === 'New Linen Department' && loc.startsWith('Ward')) {
                        complianceAlerts.push({ epc, type: 'Skipped First Wash (New→Ward)', date: dateStr });
                    }
                }

                if (proc === 'IN') {
                    item.lastInTime = time;
                    item.currentLocation = loc;
                }
                item.history.push(ev);
            });

            // Merge initial cycles from INIT events
            Object.entries(inventory).forEach(([epc, item]) => {
                if (initMeta[epc]) {
                    item.cycles += initMeta[epc].initialCycles;
                    item.homeWard = initMeta[epc].homeWard;
                }
            });

            return { inventory, usageByDate, complianceAlerts, allDwells };
        }

        const processed = processData(rawData);
        const items     = Object.values(processed.inventory);
        const SIM_END   = new Date('2025-05-01T08:00:00Z');

        function getLastEvent(item) {
            return item.history[item.history.length - 1] || null;
        }

        function getCurrentCheckIn(item) {
            // An item is "currently in a stage" if its last event is an IN (no OUT yet)
            const lastEv = item.history[item.history.length - 1];
            return (lastEv && lastEv.Process === 'IN') ? lastEv : null;
        }

        function getCurrentStageKey(item) {
            const cur = getCurrentCheckIn(item);
            if (!cur) return null;
            if (cur.Location === 'New Linen Department') return 'New Linen';
            if (cur.Location === 'Laundry Department') return 'Laundry';
            if (cur.Location === 'Cleaned Linen Department') return 'Storage';
            if (cur.Location.startsWith('Ward')) return 'Ward';
            return null;
        }

        // ── KPI Cards ──────────────────────────────────────────────────────
        const activeItems = items.filter(i => {
            const last = getLastEvent(i);
            return last && last.Process !== 'DECOMMISSION';
        });

        const totalCycles = activeItems.reduce((s, i) => s + i.cycles, 0);
        document.querySelector('#avg-cycles    .value').textContent = (totalCycles / Math.max(activeItems.length, 1)).toFixed(1);

        // ── Language Toggle ────────────────────────────────────────────────
        const enBtn = document.getElementById('en-btn');
        const thBtn = document.getElementById('th-btn');
        document.getElementById('en-btn').addEventListener('click', () => {
            document.querySelectorAll('[data-en]').forEach(el => el.textContent = el.dataset.en);
            enBtn.classList.add('active'); thBtn.classList.remove('active');
        });
        document.getElementById('th-btn').addEventListener('click', () => {
            document.querySelectorAll('[data-th]').forEach(el => el.textContent = el.dataset.th);
            thBtn.classList.add('active'); enBtn.classList.remove('active');
        });

        // ── Chart 1: Usage by Ward (last 30 days) ─────────────────────────
        const usageDates  = Object.keys(processed.usageByDate).sort().slice(-30);
        const usageCounts = usageDates.map(d => processed.usageByDate[d]);

        new Chart(document.getElementById('cycles-lifespan-chart'), {
            type: 'bar',
            data: {
                labels: usageDates,
                datasets: [{ label: 'Linen IN Events (All Wards)', data: usageCounts, backgroundColor: '#0056b3' }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });

        // ── Chart 2: Stock Levels ──────────────────────────────────────────
        // Items with an open IN (last event = IN, no matching OUT) are genuinely
        // "currently in" that stage at the snapshot date.
        const stockCounts = { 'New Linen': 0, 'In Laundry': 0, 'Clean Storage': 0, 'In Wards': 0 };
        items.forEach(i => {
            const cur = getCurrentCheckIn(i);
            if (!cur) return;
            const loc = cur.Location;
            if (loc === 'New Linen Department')      stockCounts['New Linen']++;
            else if (loc === 'Laundry Department')   stockCounts['In Laundry']++;
            else if (loc === 'Cleaned Linen Department') stockCounts['Clean Storage']++;
            else if (loc.startsWith('Ward'))         stockCounts['In Wards']++;
        });

        const circulatingNow = Object.values(stockCounts).reduce((a, b) => a + b, 0);
        document.querySelector('#total-linen .value').textContent = circulatingNow;

        const bedCoverage = Math.min(stockCounts['In Wards'], 20);
        document.querySelector('#ward-coverage .value').textContent = `${bedCoverage} / 20 Beds`;

        const parRatio = (circulatingNow / 20).toFixed(2);
        document.querySelector('#par-level-ratio .value').textContent = `${parRatio} / 10.0`;

        new Chart(document.getElementById('bottlenecks-chart'), {
            type: 'doughnut',
            data: {
                labels: Object.keys(stockCounts),
                datasets: [{ data: Object.values(stockCounts), backgroundColor: ['#17a2b8','#ffc107','#0056b3','#dc3545'] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
        });

        // ── Chart 3: Life Cycle Analysis ──────────────────────────────────
        const lc = { 'New (0-20)': 0, 'Active (21-70)': 0, 'Old (71-99)': 0, 'Retired (100+)': 0 };
        items.forEach(i => {
            if      (i.cycles <= 20) lc['New (0-20)']++;
            else if (i.cycles <= 70) lc['Active (21-70)']++;
            else if (i.cycles <= 99) lc['Old (71-99)']++;
            else                     lc['Retired (100+)']++;
        });

        new Chart(document.getElementById('lost-by-step-chart'), {
            type: 'bar',
            data: {
                labels: Object.keys(lc),
                datasets: [{ label: 'Items', data: Object.values(lc), backgroundColor: ['#0056b3','#28a745','#ffc107','#dc3545'] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
        });

        // ── Chart 4a: Stage Duration Histogram ────────────────────────────
        // Only counts items CURRENTLY in the stage (open IN = last event is IN, no OUT yet).
        // Dwell = time since they checked IN.  Unit varies by stage: hours (fast) vs days (slow).
        const STAGE_CONFIG = {
            'New Linen': {
                locMatch: loc => loc === 'New Linen Department',
                unit: 'day',
                buckets: [1, 2, 3, 4, 5, 6, 7],
                bucketLabel: d => 'Day ' + d,
                xLabel: 'Days in New Linen'
            },
            'Laundry': {
                locMatch: loc => loc === 'Laundry Department',
                unit: 'hour',
                buckets: [2, 4, 6, 8, 10, 12, 18, 24],
                bucketLabel: h => h + 'h',
                xLabel: 'Hours in Laundry'
            },
            'Storage': {
                locMatch: loc => loc === 'Cleaned Linen Department',
                unit: 'day',
                buckets: [1, 2, 3, 4, 5, 6, 7],
                bucketLabel: d => 'Day ' + d,
                xLabel: 'Days in Clean Storage'
            },
            'Ward': {
                locMatch: loc => loc.startsWith('Ward'),
                unit: 'hour',
                buckets: [2, 4, 6, 8, 10, 12, 18, 24],
                bucketLabel: h => h + 'h',
                xLabel: 'Hours in Ward'
            }
        };

        function buildHistogram(stageKey) {
            if (stageKey === 'DEBUG_TOTAL') {
                return { buckets: { ...stockCounts, 'TOTAL': items.length }, xLabel: 'Stage' };
            }
            const cfg = STAGE_CONFIG[stageKey];
            if (!cfg) return { buckets: {}, xLabel: '' };

            const rawDwells = [];
            items.forEach(item => {
                const cur = getCurrentCheckIn(item);   // only items with OPEN IN
                if (!cur || !cfg.locMatch(cur.Location)) return;
                const dwell = cfg.unit === 'hour'
                    ? (SIM_END - new Date(cur['Event Timestamp'])) / 3600000
                    : (SIM_END - new Date(cur['Event Timestamp'])) / 86400000;
                if (dwell >= 0) rawDwells.push(dwell);
            });

            const bucketKeys = cfg.buckets.map(cfg.bucketLabel);
            const buckets = Object.fromEntries(bucketKeys.map(k => [k, 0]));
            rawDwells.forEach(d => {
                const fit = cfg.buckets.find(b => d <= b) || cfg.buckets[cfg.buckets.length - 1];
                buckets[cfg.bucketLabel(fit)]++;
            });

            return { buckets, xLabel: cfg.xLabel };
        }

        let chart4a = null;

        function render4aChart(stageKey) {
            document.querySelectorAll('.stage-btn').forEach(b => b.classList.toggle('active', b.dataset.stage === stageKey));
            const { buckets, xLabel } = buildHistogram(stageKey);
            if (chart4a) {
                chart4a.data.labels = Object.keys(buckets);
                chart4a.data.datasets[0].data  = Object.values(buckets);
                chart4a.data.datasets[0].label = 'Items — ' + stageKey;
                chart4a.options.scales.x.title.text = xLabel;
                chart4a.update();
            }
        }

        const initResult = buildHistogram('New Linen');
        chart4a = new Chart(document.getElementById('rfid-barcode-chart'), {
            type: 'bar',
            data: {
                labels: Object.keys(initResult.buckets),
                datasets: [{ label: 'Items — New Linen', data: Object.values(initResult.buckets), backgroundColor: '#0056b3', borderRadius: 3 }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    x: { title: { display: true, text: initResult.xLabel } },
                    y: { beginAtZero: true, title: { display: true, text: 'Number of Items' }, ticks: { stepSize: 1 } }
                },
                plugins: { legend: { display: false } }
            }
        });

        document.querySelectorAll('.stage-btn').forEach(btn => {
            btn.addEventListener('click', () => render4aChart(btn.dataset.stage));
        });

        // ── Chart 4b: Linen Status Snapshot (New/Washing/Clean/Dirty) ─────
        // Directly mirrors stockCounts — same real open-IN data, different labels
        const statusCounts = {
            'New (Unwashed)': stockCounts['New Linen'],
            'Washing':        stockCounts['In Laundry'],
            'Clean (Ready)':  stockCounts['Clean Storage'],
            'Dirty':          stockCounts['In Wards']
        };

        new Chart(document.getElementById('linen-status-chart'), {
            type: 'doughnut',
            data: {
                labels: Object.keys(statusCounts),
                datasets: [{ data: Object.values(statusCounts), backgroundColor: ['#17a2b8','#ffc107','#28a745','#dc3545'] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
        });

        // ── Forecasting ────────────────────────────────────────────────────
        // Calculate daily usage from the full history, then project 60 days forward
        const allDates = Object.keys(processed.usageByDate).sort();
        const FORECAST_DAYS = 60;

        // 7-day rolling average for smoothing
        function rollingAvg(arr, window) {
            return arr.map((_, i) => {
                const slice = arr.slice(Math.max(0, i - window + 1), i + 1);
                return slice.reduce((a, b) => a + b, 0) / slice.length;
            });
        }

        const histCounts  = allDates.map(d => processed.usageByDate[d]);
        const smoothed    = rollingAvg(histCounts, 7);
        const avgDaily    = smoothed.length ? smoothed[smoothed.length - 1] : 1;

        // Build forecasted dates & values
        const lastDate   = allDates.length ? new Date(allDates[allDates.length - 1]) : new Date();
        const fcDates    = [], fcValues = [];
        for (let i = 1; i <= FORECAST_DAYS; i++) {
            const d = new Date(lastDate); d.setDate(d.getDate() + i);
            fcDates.push(d.toISOString().split('T')[0]);
            // Add slight upward trend + noise
            fcValues.push(+(avgDaily * (1 + i * 0.001) + (Math.random() - 0.5) * 0.5).toFixed(1));
        }

        // Calculate projected retirement dates
        const nearRetire = items.filter(i => i.cycles >= 70).length;
        const approxCyclesPerItem = avgDaily / items.length;   // cycles added per item per day
        const daysToRetire = approxCyclesPerItem > 0 ? Math.round(30 / approxCyclesPerItem) : 90;

        document.getElementById('fc-avg-daily').textContent    = avgDaily.toFixed(1);
        document.getElementById('fc-near-retire').textContent  = nearRetire;
        document.getElementById('fc-days-retire').textContent  = daysToRetire + ' days';
        document.getElementById('fc-replenish').textContent    = Math.ceil(avgDaily * 30) + ' items/month';
        document.querySelector('#monthly-replenishment .value').textContent = Math.ceil(avgDaily * 30 * 0.15) + ' items';

        const fcCtx = document.getElementById('forecast-chart');
        new Chart(fcCtx, {
            type: 'line',
            data: {
                labels: [...allDates.slice(-60), ...fcDates],
                datasets: [
                    {
                        label: 'Historical Usage',
                        data: [...histCounts.slice(-60), ...Array(FORECAST_DAYS).fill(null)],
                        borderColor: '#0056b3', backgroundColor: 'rgba(0,86,179,0.1)',
                        fill: true, tension: 0.3, pointRadius: 1
                    },
                    {
                        label: 'Forecasted Usage (60d)',
                        data: [...Array(Math.min(histCounts.length, 60)).fill(null), ...fcValues],
                        borderColor: '#dc3545', borderDash: [6, 3],
                        backgroundColor: 'rgba(220,53,69,0.08)',
                        fill: true, tension: 0.3, pointRadius: 1
                    }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: { x: { ticks: { maxTicksLimit: 10 } } },
                plugins: { legend: { position: 'bottom' } }
            }
        });

        // ── Recent Towel Activity Table ────────────────────────────────────
        const activityBody = document.getElementById('recent-activity-body');
        if (activityBody) {
            activityBody.innerHTML = '';
            const itemByEpc = Object.fromEntries(items.map(item => [item.epc, item]));

            const recentEvents = [...rawData]
                .filter(ev => ev && ev['Event Timestamp'] && ev['EPC'] && ev['Process'] !== 'INIT')
                .sort((a, b) => new Date(b['Event Timestamp']) - new Date(a['Event Timestamp']))
                .slice(0, 15);

            recentEvents.forEach(ev => {
                const tr = document.createElement('tr');
                const shortEpc = String(ev['EPC']).split('.').pop();
                const processLabel = ev['Process'] || '-';
                const cycleCount = itemByEpc[ev['EPC']] ? itemByEpc[ev['EPC']].cycles : 0;
                const statusLabel =
                    processLabel === 'DECOMMISSION' ? 'Retired' :
                    processLabel === 'OUT' ? 'Checked Out' :
                    processLabel === 'IN' ? 'Checked In' :
                    'Active';
                const timestamp = String(ev['Event Timestamp']).replace('T', ' ').replace('Z', '');

                tr.innerHTML = `
                    <td>${timestamp}</td>
                    <td>${shortEpc}</td>
                    <td>${ev['Item Description'] || '-'}</td>
                    <td>${cycleCount}</td>
                    <td>${ev['Location'] || '-'}</td>
                    <td>${statusLabel}</td>
                    <td><button class="action-btn drilldown">Drill Down</button></td>
                `;
                activityBody.appendChild(tr);
            });
        }

</script>"""

# ── 3. Inject script into template placeholder ─────────────────────────────────
new_html = html.replace('<!--__DASHBOARD_SCRIPT__-->', NEW_SCRIPT)

# ── 4. Inject actual JSON data into the placeholder ───────────────────────────
new_html = new_html.replace('const rawData = __RAWDATA__;', f'const rawData = {raw_json};', 1)

with open("Towel Tracking Dashboard demo v4.html", "w", encoding="utf-8") as f:
    f.write(new_html)

print("Done. v4 rebuilt.")
