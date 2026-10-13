# V2: attack drills and false alarms (simulated plant, simulated node)

| scenario | operator walks to the pump | reached amber | reached red | amber (median / max) | red (median / max) | final states | false alarms before attack |
|---|---|---|---|---|---|---|---|
| Frozen screen (PLC lockout) | no | 5/5 | 5/5 | 65 s / 65 s | 95 s / 95 s | {'red': 5} | 0 |
| Frozen screen (PLC lockout) | yes | 5/5 | 5/5 | 65 s / 65 s | 95 s / 95 s | {'red': 5} | 0 |
| Pumps forced off behind a careful forgery | no | 5/5 | 0/5 | 20 s / 30 s | - / - | {'amber': 5} | 0 |
| Pumps forced off behind a careful forgery | yes | 5/5 | 5/5 | 20 s / 30 s | 120 s / 120 s | {'red': 5} | 0 |
| Alarms off, remote access cut, pump cycles changed | no | 5/5 | 5/5 | 50 s / 50 s | 80 s / 80 s | {'red': 5} | 0 |
| Alarms off, remote access cut, pump cycles changed | yes | 5/5 | 5/5 | 50 s / 50 s | 80 s / 80 s | {'red': 5} | 0 |
| Replayed data | no | 0/5 | 5/5 | - / - | 50 s / 50 s | {'red': 5} | 0 |
| Replayed data | yes | 0/5 | 5/5 | - / - | 50 s / 50 s | {'red': 5} | 0 |
| Valve closed out of sight | no | 5/5 | 0/5 | 180 s / 180 s | - / - | {'amber': 5} | 0 |
| Valve closed out of sight | yes | 5/5 | 0/5 | 180 s / 180 s | - / - | {'amber': 5} | 0 |
| Power outage that looks like an attack | no | 5/5 | 5/5 | 20 s / 30 s | 125 s / 125 s | {'red': 5} | 0 |
| Power outage that looks like an attack | yes | 5/5 | 5/5 | 20 s / 30 s | 125 s / 125 s | {'red': 5} | 0 |

Normal operation, full system with two nodes: 10 site-days, 0 false amber, 0 false red, while the pumps really started or stopped 33 times. The nodes sent 785 state-change messages, most of them brief 'unsure' blips from their modelled sensor noise.
Consistency rules alone: 20 site-days, 0 false rule episodes .
