# Proposal: a read-only "shadow mode" trial of HydroWitness

*From Static Head Labs, a student team in the Conrad Challenge (Cyber-Security and Technology). Draft: adjust the bracketed parts, and have an adult on the team read it before it is sent.*

## What we are asking for

A small water system lets us run HydroWitness **beside** its normal operation for **4 to 8 weeks**, in shadow mode. "Shadow" means it watches and reports to the team and to one person you name. **It never writes to your plant, never sends a command, and does not replace anything you use.** If it is wrong, nothing happens to your water.

## What HydroWitness is, in two sentences

When a SCADA screen freezes or lies, it estimates how many minutes the pressure at your most critical customer would take to fall below 20 psi, from the tank level and your own system numbers, and it compares what SCADA says with independent clues (an operator's own readings and a small battery-powered sensor box on the outside of a pump that listens to vibration and sound). It shows a green, amber or red light and the reasons.

## What we would need from you

| item | detail | your choice |
|---|---|---|
| A contact person | one operator or manager we can ask questions of, who can say stop at any time | |
| System numbers | tank diameter and height, the elevation of the tank floor and the most critical customer, peak demand, pump ratings, minimum pressure. We would also like a second person to check the elevations | you decide what to share |
| A data feed | tank level and pump on/off every few seconds to minutes. **Simplest first version: you send a spreadsheet or text export, or type a reading.** Direct SCADA connections are not built and we will not ask for one in a first trial | |
| Permission to attach a Witness Node | a box about the size of a deck of cards clipped to the **outside** of one pump or motor, on a USB power adapter, for the length of the trial. It is not wired into anything and does not join your network unless you ask it to | optional |
| 30 minutes at the start and 30 at the end | to check our numbers and hear what we got wrong | |

## What we would **not** need, and will not take

Passwords, remote access, network diagrams, screenshots of your SCADA, the brand of your control system, customer data, or any information about your security.

## What you get

* A weekly one-page note: what it showed, which readings it disagreed with and why, and what it got wrong.
* The final report, including everything that did not work.
* The Time-to-Trouble numbers for your system, with the assumptions and how sensitive they are to a wrong elevation (we have a tool for that).
* A free trial with no obligation to continue, and the open-source code (Apache 2.0).

## What we promise

* We will describe it as a student prototype for a competition, not a certified safety system. You should not rely on it as your only protection.
* We will not use your system's name, location or data in anything public without your written permission. You can withdraw at any time and we will delete what we have.
* We will tell you straight away if we find a way it could be unsafe or if it does something we did not expect.
* We will share what we learn from your system with you before we share it with anyone else.

## How we will judge the trial (fixed in advance)

| question | how we measure it | what would count as a bad result |
|---|---|---|
| Does it cry wolf? | number of amber or red lights on days nothing was wrong, per week | more than [2] per week would mean you would ignore it (we will ask your number) |
| Does it agree with reality? | each week, compare the clock's tank-level estimate with the operator's real reading | disagreement beyond [1] ft more than [10%] of the time |
| Does the Witness Node tell on from off? | against the pump's real run times (from the operator's log or SCADA, read after the fact) | worse than the bar in `docs/PUMP_TEST_PROTOCOL.md` |
| Is the setup right? | a second person checks every elevation we typed | any number off by more than 1 ft is fixed and counted |
| Would you use it? | a short conversation at the end | "no", and why |

If the result is bad we say so, and publish it.

## Safety and ethics

An adult (teacher, parent or mentor) is part of the team and will join the first call. We will not ask any team member to enter a plant or touch electrical equipment. The Witness Node is placed by your staff, or by us only with your operator present and in charge.

## Who we are

[Team name, school, country]. Contact: [adult mentor name and email], [team lead name and email]. Website: https://hydrowitness.vercel.app. Source: https://github.com/Younus0v/hydrowitness.
