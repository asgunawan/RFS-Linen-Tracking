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
            const usageByWardDate = {};
            const complianceAlerts = [];
            const allDwells       = {
                'New Linen Department':     [],
                'Laundry Department':       [],
                'Cleaned Linen Department': [],
                'Ward': []          // any ward bucket
            };
            const wardsSet = new Set();
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
                    wardsSet.add(loc);
                    if (!usageByWardDate[loc]) usageByWardDate[loc] = {};
                    usageByWardDate[loc][dateStr] = (usageByWardDate[loc][dateStr] || 0) + 1;
                    item.lastInTime = time;
                }

                // Cycle count: IN at Laundry
                if (loc === 'Laundry Department' && proc === 'IN') {
                    item.cycles += 1;
                }

                // Trust explicit final cycle count at retirement
                if (proc === 'DECOMMISSION' && typeof ev['Final Cycles'] === 'number') {
                    item.cycles = Math.max(item.cycles, ev['Final Cycles']);
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

            return { inventory, usageByDate, usageByWardDate, wards: Array.from(wardsSet).sort(), complianceAlerts, allDwells };
        }

        const processed = processData(rawData);
        const items     = Object.values(processed.inventory);
        const SIM_END   = new Date('2025-05-01T08:00:00Z');
        const LOW_STOCK_THRESHOLD = 5;

        const latestEventMs = rawData.reduce((maxTs, ev) => {
            const ts = Date.parse(ev['Event Timestamp'] || '');
            return Number.isNaN(ts) ? maxTs : Math.max(maxTs, ts);
        }, 0);
        const recencyEnd = latestEventMs ? new Date(latestEventMs) : SIM_END;
        const recencyStart = new Date(recencyEnd);
        recencyStart.setDate(recencyStart.getDate() - 1);

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

        // ── Notifications (debug-trigger only; no startup auto-push) ─────
        const notificationBtn = document.getElementById('notification-btn');
        const notificationBadge = document.getElementById('notification-badge');
        const notificationModal = document.getElementById('notification-modal');
        const notificationBackdrop = document.getElementById('notification-backdrop');
        const notificationCloseBtn = document.getElementById('notification-close-btn');
        const notificationList = document.getElementById('notification-list');
        const toastContainer = document.getElementById('toast-container');
        const notificationDebugBtn = document.getElementById('notification-debug-btn');

        const notifications = [];
        let unreadNotifications = 0;
        let toastTimer = null;

        function formatNotificationTime(ts) {
            return ts.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) +
                ' ' + ts.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
        }

        function formatTowelLabel(epc) {
            const epcText = String(epc || '').trim();
            if (!epcText) return 'Towel Unknown';
            const lastDot = epcText.lastIndexOf('.');
            const tail = lastDot >= 0 ? epcText.slice(lastDot + 1) : epcText;
            const compact = tail.replace(/[^0-9A-Za-z]/g, '');
            return `Towel ${compact || epcText}`;
        }

        function updateNotificationBadge() {
            if (!notificationBadge) return;
            notificationBadge.textContent = String(Math.min(unreadNotifications, 99));
            notificationBadge.classList.toggle('show', unreadNotifications > 0);
        }

        function renderNotifications() {
            if (!notificationList) return;
            if (!notifications.length) {
                notificationList.innerHTML = '<li class="notification-empty" data-en="No notifications yet." data-th="ยังไม่มีการแจ้งเตือน">No notifications yet.</li>';
                return;
            }

            notificationList.innerHTML = notifications.map(n => (
                `<li class="notification-item">` +
                    `<div class="notification-item-title">${n.message}</div>` +
                    (n.detail ? `<div class="notification-item-detail">${n.detail}</div>` : '') +
                    `<div class="notification-item-time">${formatNotificationTime(n.timestamp)}</div>` +
                `</li>`
            )).join('');
        }

        function showToast(message) {
            if (!toastContainer) return;
            const toast = document.createElement('div');
            toast.className = 'notification-toast';
            toast.textContent = message;
            toastContainer.appendChild(toast);
            
            // Trigger animation in next frame
            requestAnimationFrame(() => toast.classList.add('show'));
            
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => toast.remove(), 250);
            }, 3500);
        }

        function openNotificationModal() {
            if (!notificationModal) return;
            notificationModal.classList.add('show');
            notificationModal.setAttribute('aria-hidden', 'false');
            unreadNotifications = 0;
            updateNotificationBadge();
            renderNotifications();
        }

        function closeNotificationModal() {
            if (!notificationModal) return;
            notificationModal.classList.remove('show');
            notificationModal.setAttribute('aria-hidden', 'true');
        }

        function pushNotification(message, detail) {
            notifications.unshift({ message, detail, timestamp: new Date() });
            if (notifications.length > 12) notifications.length = 12;
            unreadNotifications += 1;
            updateNotificationBadge();
            renderNotifications();
            showToast(message);
        }

        // ── Chart 1: Usage by Ward (last 30 days) ─────────────────────────
        const completeCutoffDate = SIM_END.toISOString().split('T')[0];
        const allCompleteDates = Object.keys(processed.usageByDate)
            .filter(d => d < completeCutoffDate)
            .sort();

        function getUsageSeries(ward) {
            const source = (ward === 'All Wards')
                ? processed.usageByDate
                : (processed.usageByWardDate[ward] || {});
            const labels = allCompleteDates.slice(-30);
            const values = labels.map(d => source[d] || 0);
            return { labels, values };
        }

        const wardFilter = document.getElementById('ward-filter');
        if (wardFilter) {
            wardFilter.innerHTML = '<option>All Wards</option>';
            processed.wards.forEach(ward => {
                const opt = document.createElement('option');
                opt.value = ward;
                opt.textContent = ward;
                wardFilter.appendChild(opt);
            });
        }

        function triggerDebugDispatchNotification() {
            const storageItems = items.filter(i => {
                const cur = getCurrentCheckIn(i);
                return cur && cur.Location === 'Cleaned Linen Department';
            });

            const randomItem = storageItems.length
                ? storageItems[Math.floor(Math.random() * storageItems.length)]
                : null;

            const selectedWard = (wardFilter && wardFilter.value && wardFilter.value !== 'All Wards')
                ? wardFilter.value
                : 'Wards';

            const availableWards = (processed.wards || []).filter(w => String(w).startsWith('Ward'));
            const randomArrivalWard = availableWards.length
                ? availableWards[Math.floor(Math.random() * availableWards.length)]
                : 'Ward 1';

            const towelId = randomItem ? randomItem.epc : 'Unknown Towel';
            const towelLabel = formatTowelLabel(towelId);
            pushNotification(
                `${towelLabel} has been checked out from Clean Storage.`,
                `Heading to ${selectedWard}`
            );

            setTimeout(() => {
                pushNotification(
                    `${towelLabel} has arrived at ${randomArrivalWard}.`,
                    `Ward IN registered`
                );
            }, 3000);
        }

        if (notificationBtn) {
            notificationBtn.addEventListener('click', () => {
                if (notificationModal && notificationModal.classList.contains('show')) {
                    closeNotificationModal();
                } else {
                    openNotificationModal();
                }
            });
        }
        if (notificationCloseBtn) notificationCloseBtn.addEventListener('click', closeNotificationModal);
        if (notificationBackdrop) notificationBackdrop.addEventListener('click', closeNotificationModal);
        if (notificationDebugBtn) notificationDebugBtn.addEventListener('click', triggerDebugDispatchNotification);
        document.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape') closeNotificationModal();
        });

        const initialUsage = getUsageSeries('All Wards');
        const usageChart = new Chart(document.getElementById('cycles-lifespan-chart'), {
            type: 'bar',
            data: {
                labels: initialUsage.labels,
                datasets: [{ label: 'Linen IN Events (All Wards)', data: initialUsage.values, backgroundColor: '#0056b3' }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });

        if (wardFilter) {
            wardFilter.addEventListener('change', () => {
                const selectedWard = wardFilter.value || 'All Wards';
                const usage = getUsageSeries(selectedWard);
                usageChart.data.labels = usage.labels;
                usageChart.data.datasets[0].data = usage.values;
                usageChart.data.datasets[0].label = `Linen IN Events (${selectedWard})`;
                usageChart.update();
            });
        }

        // ── Chart 2: Stock Levels ──────────────────────────────────────────
        // Items with an open IN (last event = IN, no matching OUT) are genuinely
        // "currently in" that stage at the snapshot date.
        const stockCounts = { 'New Linen': 0, 'In Laundry': 0, 'Clean Storage': 0, 'In Wards': 0 };
        const wardStockCounts = Object.fromEntries(processed.wards.map(ward => [ward, 0]));
        items.forEach(i => {
            const cur = getCurrentCheckIn(i);
            if (!cur) return;
            const loc = cur.Location;
            if (loc === 'New Linen Department')      stockCounts['New Linen']++;
            else if (loc === 'Laundry Department')   stockCounts['In Laundry']++;
            else if (loc === 'Cleaned Linen Department') stockCounts['Clean Storage']++;
            else if (loc.startsWith('Ward')) {
                stockCounts['In Wards']++;
                wardStockCounts[loc] = (wardStockCounts[loc] || 0) + 1;
            }
        });

        // ── Top KPI: Linen Flow (24h) ─────────────────────────────────────
        const received24h = rawData.filter(ev => {
            if (ev['Process'] !== 'IN' || !String(ev['Location'] || '').startsWith('Ward')) return false;
            const t = new Date(ev['Event Timestamp']);
            return t >= recencyStart && t <= recencyEnd;
        }).length;

        const dispatched24h = rawData.filter(ev => {
            if (ev['Process'] !== 'OUT' || ev['Location'] !== 'Cleaned Linen Department') return false;
            const t = new Date(ev['Event Timestamp']);
            return t >= recencyStart && t <= recencyEnd;
        }).length;

        const wardNames = Object.keys(wardStockCounts).sort();
        const lowStockWards = wardNames.filter(ward => (wardStockCounts[ward] || 0) < LOW_STOCK_THRESHOLD);
        const throughputValueEl = document.getElementById('throughput-24h-value');
        if (throughputValueEl) throughputValueEl.textContent = `${received24h} IN / ${dispatched24h} OUT`;


        const throughputCard = document.getElementById('throughput-24h');
        if (throughputCard) {
            throughputCard.setAttribute(
                'data-tooltip',
                `Linen movement in the latest 24-hour window.\nWindow: ${recencyStart.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})} ${recencyStart.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})} → ${recencyEnd.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})} ${recencyEnd.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}\nIN: ${received24h}  OUT: ${dispatched24h}`
            );
        }

        const circulatingNow = Object.values(stockCounts).reduce((a, b) => a + b, 0);
        document.querySelector('#total-linen .value').textContent = circulatingNow;

        const bedCoverage = Math.min(stockCounts['In Wards'], 20);
        const wardCoverageValueEl = document.querySelector('#ward-coverage .value');
        wardCoverageValueEl.textContent = `${bedCoverage} / 20`;
        wardCoverageValueEl.classList.remove('kpi-good', 'kpi-caution');
        if (bedCoverage >= 20) {
            wardCoverageValueEl.classList.add('kpi-good');
        }
        // Future: Implement pill display logic here when scaling up (e.g., only show if stock < threshold)

        const TARGET_BEDS = 20;
        const TARGET_PAR_RATIO = 10;
        const targetParInventory = TARGET_BEDS * TARGET_PAR_RATIO;
        const recent30dStart = new Date(recencyEnd);
        recent30dStart.setDate(recent30dStart.getDate() - 30);
        const recentDecomm30d = rawData.filter(ev => {
            if (ev['Process'] !== 'DECOMMISSION' || !ev['Event Timestamp']) return false;
            const t = new Date(ev['Event Timestamp']);
            return t >= recent30dStart && t <= recencyEnd;
        }).length;
        const suggestedOrderQty = Math.max(0, targetParInventory - circulatingNow) + recentDecomm30d;

        document.querySelector('#monthly-replenishment .value').textContent = `${suggestedOrderQty} items`;

        document.getElementById('total-linen').setAttribute(
            'data-tooltip',
            `Active towels currently in circulation across all stages.\nNow in circulation: ${circulatingNow}`
        );
        document.getElementById('monthly-replenishment').setAttribute(
            'data-tooltip',
            `Suggested order quantity for next month.\nTarget par = ${TARGET_BEDS} beds × ${TARGET_PAR_RATIO} = ${targetParInventory}\nCurrent inventory = ${circulatingNow}\n30-day decommissions = ${recentDecomm30d}\nSuggested order = max(0, ${targetParInventory} - ${circulatingNow}) + ${recentDecomm30d} = ${suggestedOrderQty}`
        );
        document.getElementById('ward-coverage').setAttribute(
            'data-tooltip',
            `Coverage in wards versus bed target.\nCurrent: ${bedCoverage} / ${TARGET_BEDS}`
        );

        new Chart(document.getElementById('bottlenecks-chart'), {
            type: 'doughnut',
            data: {
                labels: Object.keys(stockCounts),
                datasets: [{ data: Object.values(stockCounts), backgroundColor: ['#17a2b8','#ffc107','#0056b3','#dc3545'] }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
        });

        // ── Chart 5: Life Cycle Analysis ──────────────────────────────────
        // Red bar is a risk backlog indicator:
        // items at >=100 cycles that are NOT yet decommissioned.
        const lc = { 'New (0-20)': 0, 'Active (21-70)': 0, 'Old (71-99)': 0, 'Overdue (100+)': 0 };
        items.forEach(i => {
            const last = getLastEvent(i);
            const isAlreadyRetired = last && last.Process === 'DECOMMISSION';
            if (isAlreadyRetired) return;

            if      (i.cycles <= 20) lc['New (0-20)']++;
            else if (i.cycles <= 70) lc['Active (21-70)']++;
            else if (i.cycles <= 99) lc['Old (71-99)']++;
            else                     lc['Overdue (100+)']++;
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

        const initResult = buildHistogram('Storage');
        chart4a = new Chart(document.getElementById('rfid-barcode-chart'), {
            type: 'bar',
            data: {
                labels: Object.keys(initResult.buckets),
                datasets: [{ label: 'Items — Storage', data: Object.values(initResult.buckets), backgroundColor: '#0056b3', borderRadius: 3 }]
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

        // ── Chart 3: Ward Availability vs Minimum Threshold ─────────────
        const wardLabels = wardNames;
        const wardValues = wardLabels.map(ward => wardStockCounts[ward] || 0);
        const wardThreshold = wardLabels.map(() => LOW_STOCK_THRESHOLD);

        new Chart(document.getElementById('linen-status-chart'), {
            type: 'bar',
            data: {
                labels: wardLabels,
                datasets: [
                    {
                        label: 'Available Towels',
                        data: wardValues,
                        backgroundColor: wardValues.map(v => v < LOW_STOCK_THRESHOLD ? '#dc3545' : '#0056b3'),
                        borderRadius: 4
                    },
                    {
                        label: 'Minimum Threshold',
                        data: wardThreshold,
                        type: 'line',
                        borderColor: '#ffc107',
                        backgroundColor: '#ffc107',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Towels' },
                        ticks: { stepSize: 1 }
                    }
                },
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            afterBody: (context) => {
                                const i = context[0].dataIndex;
                                const wardValue = wardValues[i] || 0;
                                const diff = LOW_STOCK_THRESHOLD - wardValue;
                                return diff > 0
                                    ? `Alert: ${diff} below threshold`
                                    : `Status: ${Math.abs(diff)} above threshold`;
                            }
                        }
                    }
                }
            }
        });

        // ── Chart 6: Forecasting ──────────────────────────────────────────
        // Calculate daily usage from the full history, then project 60 days forward
        const allDates = Object.keys(processed.usageByDate)
            .filter(d => d < completeCutoffDate)
            .sort();
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
        document.getElementById('fc-replenish').textContent    = suggestedOrderQty + ' items/month';

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
