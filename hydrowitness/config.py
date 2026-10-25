"""Default weights and thresholds.

These are design defaults chosen BEFORE testing (see the project document, section 5.5).
Testing tunes them, and the results must report what changed and why.
"""

# Witness weights
W_OPERATOR = 3.0            # operator reading of a physical gauge / sight glass / pump seen by eye
W_NODE_BOTH = 2.0           # Witness Node, accelerometer and microphone agree
W_NODE_ONE = 1.0            # Witness Node, only one channel has an opinion
W_CUSTOMER_EACH = 0.5       # per distinct address
W_CUSTOMER_CAP = 1.5        # customer reports can reach amber alone, never red alone
CUSTOMER_MIN_DISTINCT = 3
CUSTOMER_WINDOW_S = 1800.0

# Trust thresholds
AMBER_AT = 1.0
RED_AT = 3.0
FRESHNESS_S = 900.0         # a witness contribution decays linearly to zero over 15 minutes
GRACE_S = 8.0               # ignore claim/witness disagreements younger than this (command lag)
NODE_MIN_CONFIDENCE = 0.5

# Integrity events (rejected node messages) surface as amber but can never force red alone
INTEGRITY_EACH = 1.0
INTEGRITY_CAP = 1.5
INTEGRITY_WINDOW_S = 300.0

# Consistency rule severities
SEV_MASS_BALANCE = 1.5
SEV_PUMP_FLOW = 1.5
SEV_STATIC_HEAD = 1.0
SEV_PLAUSIBILITY = 1.0
SEV_COMMAND_RESPONSE = 1.0
SEV_FROZEN = 1.5
SEV_REPLAY = 2.0

# Time-to-Trouble
INFLOW_SAFETY_FACTOR = 0.8
LEVEL_BASE_UNCERTAINTY_FT = 0.5
LEVEL_DRIFT_FT_PER_MIN = 0.02
UNTRUSTED_EXTRA_UNCERTAINTY_FT = 1.5
EXPECTED_HORIZON_H = 72
HEADLOSS_EXPONENT = 1.852   # Hazen-Williams: friction loss grows with flow^1.852. Added after validation V1 (see results/).

# How the clock describes the age of its tank reading (plain labels only; the numbers in the estimate do not change)
CLOCK_FRESH_S = 900.0        # a reading this young is "fresh"
CLOCK_OLD_S = 3600.0         # older than this is "old": the screen says so and asks for a new reading

# Alerts
ESCALATE_AFTER_S = 300.0
# Alerts that could not be delivered (the private channel is down or unreachable) are kept and tried again
ALERT_RETRY_S = 30.0         # first retry; each later retry waits twice as long...
ALERT_RETRY_MAX_S = 240.0    # ...up to this long between tries
ALERT_KEEP_S = 86400.0       # stop trying after a day: a day-old alert helps nobody
