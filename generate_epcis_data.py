import uuid
import random
from datetime import datetime, timedelta
import json

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NUM_ITEMS = 193          # par level for 20-bed hospital POC
DAYS      = 120          # 2025-01-01 through ~2025-05-01
START_DATE = datetime(2025, 1, 1, 8, 0, 0)

# ---------------------------------------------------------------------------
# Frontend test-only hardcoded injections (band-aid / non-production)
# These are intentionally hardcoded to make specific dashboard states visible:
# 1) keep some towels in New Linen at snapshot
# 2) force a small overdue-retirement red flag backlog
# ---------------------------------------------------------------------------
FRONTEND_TEST_HARDCODE = True
HARDCODE_NEW_LINEN_AT_END = 10
HARDCODE_OVERDUE_NOT_RETIRED = 3

# EPCIS Constants
GTIN_TOWEL = "08901030000005"
APP_ID     = "LinenTrack-v2.1"

# ---------------------------------------------------------------------------
# 4 Wards (5 beds per ward -> 20 beds total)
# ---------------------------------------------------------------------------
WARDS = [str(ward_no) for ward_no in range(1, 5)]
# e.g. 1, 2, 3, 4

def ward_location(ward_id):
    return f"Ward {ward_id}"

# RFID devices per static location + one reader per ward
STATIC_DEVICES = {
    "New Linen Department":     "RD-NEW-01",
    "Laundry Department":       "RD-WASH-01",
    "Cleaned Linen Department": "RD-STORE-01",
}
def device_for(location):
    if location in STATIC_DEVICES:
        return STATIC_DEVICES[location]
    # Ward reader: RD-WARD-1A, RD-WARD-2B, etc.
    return "RD-WARD-" + location.replace("Ward ", "")

# Staff pools
STAFF_NEW     = ["S-101", "S-102"]
STAFF_LAUNDRY = ["S-201", "S-202", "S-203", "S-204"]
STAFF_STORE   = ["S-301", "S-302"]
# Ward staff pool per ward (ward 1 = S-4xx, ward 2 = S-5xx, etc.)
def ward_staff(ward_id):
    ward_no = int(ward_id)          # 1-4
    base = 400 + (ward_no - 1) * 10
    return [f"S-{base+i}" for i in range(1, 5)]

def staff_for(location):
    if location == "New Linen Department":     return random.choice(STAFF_NEW)
    if location == "Laundry Department":       return random.choice(STAFF_LAUNDRY)
    if location == "Cleaned Linen Department": return random.choice(STAFF_STORE)
    ward_id = location.replace("Ward ", "")
    return random.choice(ward_staff(ward_id))

# ---------------------------------------------------------------------------
# Dwell Time Rules  (min_hours, max_hours) - TOWEL SPEED
# ---------------------------------------------------------------------------
DWELL_NEW     = (8,   24)
DWELL_LAUNDRY = (4,   8)     # fast cleaning
DWELL_STORE   = (12,  48)
DWELL_WARD    = (6,   18)    # towels swap fast
LAUNDRY_QUEUE_DELAY = (0.5, 6.0)  # capacity bottleneck wait before wash starts

def dwell_for(location, anomaly_roll):
    if location == "New Linen Department":
        h = random.uniform(*DWELL_NEW)
        if anomaly_roll < 0.03: h = random.uniform(48, 96)
    elif location == "Laundry Department":
        h = random.uniform(*DWELL_LAUNDRY)
        h += random.uniform(*LAUNDRY_QUEUE_DELAY)
        if anomaly_roll < 0.04: h = random.uniform(24, 48)
    elif location == "Cleaned Linen Department":
        h = random.uniform(*DWELL_STORE)
        if anomaly_roll < 0.05: h = random.uniform(5*24, 7*24)
    else:  # Ward
        h = random.uniform(*DWELL_WARD)
        if anomaly_roll < 0.04: h = random.uniform(48, 72)
    return h

# ---------------------------------------------------------------------------
# Standard flow  (ward slot is filled dynamically)
# ---------------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------------
def make_event(timestamp, epc, location, process, item_desc, gtin, job_id,
               extra=None):
    ev = {
        "Event GUID":      str(uuid.uuid4()),
        "Event Timestamp": timestamp.isoformat() + "Z",
        "Job ID":          job_id,
        "RFID Device ID":  device_for(location),
        "Android App ID":  APP_ID,
        "Staff ID":        staff_for(location),
        "Location":        location,
        "Process":         process,
        "Item Description": item_desc,
        "GTIN":            gtin,
        "EPC":             epc,
    }
    if extra:
        ev.update(extra)
    return ev

# ---------------------------------------------------------------------------
# Simulate one item.
# Returns (list_of_events, decommission_time_or_None)
# Items whose dwell extends past SIM_END are left with an OPEN IN (no OUT),
# making them "currently in that stage" at the snapshot date.
# ---------------------------------------------------------------------------
SIM_END = START_DATE + timedelta(days=DAYS)

def simulate_item(epc, initial_cycles, home_ward, start_time, start_loc_idx, retire_at,
                  is_ghost=False, ghost_day=9999):
    item_desc = "Bath Towel - Large"
    gtin      = GTIN_TOWEL
    evs       = []
    current_time = start_time
    cycles    = initial_cycles
    loc_idx   = start_loc_idx

    # INIT meta-event
    evs.append(make_event(
        current_time - timedelta(minutes=1),
        epc, "New Linen Department", "INIT", item_desc, gtin,
        str(uuid.uuid4()),
        extra={"Initial Cycles": initial_cycles, "Home Ward": home_ward}
    ))

    decommission_time = None

    while current_time < SIM_END:
        # Determine location from loc_idx
        r = loc_idx % 4
        if r == 0:
            location = "New Linen Department"
        elif r == 1:
            location = "Laundry Department"
        elif r == 2:
            location = "Cleaned Linen Department"
        else:
            location = ward_location(random.choice(WARDS)) if random.random() < 0.10 else home_ward

        # Ghost disappears silently
        if is_ghost and (current_time - START_DATE).days > ghost_day:
            break

        # Retirement check — emit DECOMMISSION event
        if cycles >= retire_at:
            evs.append(make_event(
                current_time, epc, location, "DECOMMISSION", item_desc, gtin,
                str(uuid.uuid4()),
                extra={"Final Cycles": cycles, "Reason": "End of Life"}
            ))
            decommission_time = current_time
            break

        job_id = str(uuid.uuid4())

        # IN event — item enters stage
        evs.append(make_event(current_time, epc, location, "IN", item_desc, gtin, job_id))

        anom = random.random()
        dwell_h = dwell_for(location, anom)
        current_time += timedelta(hours=dwell_h)

        # If dwell extends past SIM_END, leave as open IN (currently in stage)
        if current_time >= SIM_END:
            break

        # OUT event — item leaves stage
        evs.append(make_event(current_time, epc, location, "OUT", item_desc, gtin, job_id))

        # Transit gap
        current_time += timedelta(minutes=random.randint(15, 120))

        # Next location
        next_idx = (loc_idx + 1) % 4

        # Used towels: Ward → Laundry (skip New Linen — that's for brand-new stock only)
        if location.startswith('Ward') and cycles > 0 and next_idx % 4 == 0:
            next_idx = 1

        # Compliance anomalies
        skip = random.random()
        if location == home_ward and skip < 0.03:
            next_idx = 2   # skip laundry: Ward → Storage
        elif location == "New Linen Department" and skip < 0.02:
            next_idx = 3   # skip first wash: New → Ward

        if next_idx % 4 == 1:    # entering laundry = one wash cycle
            cycles += 1

        loc_idx = next_idx

    return evs, decommission_time

# ---------------------------------------------------------------------------
# Generate events — fleet management with replenishment
# ---------------------------------------------------------------------------
events        = []
item_counter  = NUM_ITEMS + 1   # EPCs for replacement items start here
total_items   = NUM_ITEMS       # track total unique items ever

# Work queue: list of (epc, initial_cycles, home_ward, start_time, loc_idx, retire_at, is_ghost, ghost_day)
WorkItem = dict

queue = []
for i in range(1, NUM_ITEMS + 1):
    rv = random.random()
    if   rv < 0.10: cycles = 0
    elif rv < 0.65: cycles = random.randint(20, 60)
    elif rv < 0.88: cycles = random.randint(61, 75)
    else:           cycles = random.randint(76, 85)

    queue.append({
        "epc":            f"urn:epc:id:sgtin:0890103.00000.{i:05d}",
        "initial_cycles": cycles,
        "home_ward":      ward_location(random.choice(WARDS)),
        "start_time":     START_DATE + timedelta(hours=random.randint(0, 72)),
        "loc_idx":        0 if cycles == 0 else random.choice([1, 2, 3]),
        "retire_at":      100,
        "is_ghost":       random.random() < 0.02,
        "ghost_day":      random.randint(30, 200),
    })

while queue:
    wi = queue.pop(0)

    item_evs, decomm_time = simulate_item(
        wi["epc"], wi["initial_cycles"], wi["home_ward"],
        wi["start_time"], wi["loc_idx"], wi["retire_at"],
        wi["is_ghost"], wi["ghost_day"]
    )
    events.extend(item_evs)

    # Replenish: schedule a new towel to arrive 1–7 days after decommission.
    # Only original-fleet items trigger replenishment — no cascading replacements.
    if decomm_time is not None and not wi.get("is_replacement", False):
        arrival = decomm_time + timedelta(days=random.randint(1, 7))
        if arrival < SIM_END - timedelta(days=14):   # only worth adding if >2 weeks remain
            new_epc = f"urn:epc:id:sgtin:0890103.00000.{item_counter:05d}"
            item_counter += 1
            total_items  += 1
            queue.append({
                "epc":            new_epc,
                "initial_cycles": 0,
                "home_ward":      ward_location(random.choice(WARDS)),
                "start_time":     arrival,
                "loc_idx":        0,    # always starts at New Linen
                "retire_at":      100,  # fresh stock retires at 100
                "is_ghost":       False,
                "ghost_day":      9999,
                "is_replacement": True, # prevents further cascading
            })

# ---------------------------------------------------------------------------
# Hardcoded frontend test injections (non-production behavior)
# ---------------------------------------------------------------------------
if FRONTEND_TEST_HARDCODE:
    item_desc = "Bath Towel - Large"
    gtin = GTIN_TOWEL

    # (1) Force ~10 open New Linen items near SIM_END
    for _ in range(HARDCODE_NEW_LINEN_AT_END):
        epc = f"urn:epc:id:sgtin:0890103.00000.{item_counter:05d}"
        item_counter += 1
        total_items += 1

        home_ward = ward_location(random.choice(WARDS))
        t_in = SIM_END - timedelta(hours=random.uniform(2, 10))
        job_id = str(uuid.uuid4())

        events.append(make_event(
            t_in - timedelta(minutes=1),
            epc, "New Linen Department", "INIT", item_desc, gtin,
            str(uuid.uuid4()),
            extra={"Initial Cycles": 0, "Home Ward": home_ward}
        ))
        events.append(make_event(
            t_in,
            epc, "New Linen Department", "IN", item_desc, gtin, job_id
        ))

    # (2) Force 2-4 overdue items (using 3) with 105-110 cycles and no DECOMMISSION
    for _ in range(HARDCODE_OVERDUE_NOT_RETIRED):
        epc = f"urn:epc:id:sgtin:0890103.00000.{item_counter:05d}"
        item_counter += 1
        total_items += 1

        home_ward = ward_location(random.choice(WARDS))
        forced_cycles = random.randint(105, 110)
        t_in = SIM_END - timedelta(hours=random.uniform(1, 6))
        job_id = str(uuid.uuid4())

        events.append(make_event(
            t_in - timedelta(minutes=1),
            epc, "New Linen Department", "INIT", item_desc, gtin,
            str(uuid.uuid4()),
            extra={"Initial Cycles": forced_cycles, "Home Ward": home_ward}
        ))
        events.append(make_event(
            t_in,
            epc, "Cleaned Linen Department", "IN", item_desc, gtin, job_id
        ))

# Sort chronologically
events.sort(key=lambda x: x["Event Timestamp"])

with open("epcis_events.json", "w") as f:
    json.dump(events, f, separators=(',', ':'))

# Summary
decomms   = sum(1 for e in events if e["Process"] == "DECOMMISSION")
replenishments = total_items - NUM_ITEMS
print(f"Generated {len(events):,} EPCIS events for {total_items} items "
      f"over {DAYS} days ({START_DATE.date()} -> "
      f"{SIM_END.date()}).")
print(f"  Decommissions: {decomms}  |  Replenishments (new stock): {replenishments}")
if FRONTEND_TEST_HARDCODE:
    print(f"  [HARDCODED FRONTEND TEST] New Linen open-at-end items: {HARDCODE_NEW_LINEN_AT_END}")
    print(f"  [HARDCODED FRONTEND TEST] Overdue-not-retired items: {HARDCODE_OVERDUE_NOT_RETIRED}")
print("Saved to epcis_events.json")
