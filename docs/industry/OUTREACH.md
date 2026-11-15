# Outreach drafts

Fill the brackets. Send from a team email with an adult copied. Keep each one short. Log every send in `OUTREACH_LOG.csv`. Do not send anything that claims a result we do not have.

---

## 1. First email to a water operator or manager

**Subject:** Student question about small water systems (20 minutes?)

Hello [Name],

I'm [your name], a student in a team called Static Head Labs. We're working on a Conrad Challenge project about what small water systems do when their control screen can't be trusted, for example when it freezes or goes blank.

We're not selling anything. We'd really like to understand how it works in practice, and where our idea is wrong, from someone who runs a system. Could we ask you about 20 minutes of questions by phone or video, whenever suits you? We won't use your name or your system's name unless you say that's fine.

Thank you for considering it. [Adult mentor] is copied.

[Your name], Static Head Labs
[Contact]

---

## 2. Email to a state or regional rural water association

**Subject:** Student team seeking advice from small-system operators

Hello,

We are a student team (Static Head Labs) in the Conrad Challenge. We are building an open-source, read-only monitor for small water systems that estimates how long their tank would last and checks whether their SCADA data agrees with independent clues, for the day the screen can't be trusted.

We would value your help in two ways. First, could you point us to one or two operators who would give us 20 minutes to tell us what we have wrong? Second, if your members have a newsletter or a message board, would you allow us to share an anonymous, five-minute, eight-question survey? It collects no names, locations or security details.

We can send the questions and a one-page description first. [Adult mentor] is copied.

Thank you,
[Name], Static Head Labs

---

## 3. Email asking an engineer or academic to review our work

**Subject:** Request: 30 minutes to critique a student water-system monitor

Dear [Dr / Mr / Ms Name],

I'm a student on a team called Static Head Labs. Your work on [specific paper or role, one true sentence about what you actually read] is the reason I'm writing.

We have built an open-source prototype that estimates how many minutes a water system's tank can supply its critical customer, and flags when SCADA data disagrees with independent evidence. All of our checks so far are in simulation (EPANET randomized systems, simulated attacks, a long false-alarm study), with exact confidence intervals. We have **not** yet recorded a real pump. We have written down our limits in `docs/VALIDATION_MATRIX.md`, `docs/THREAT_MODEL.md` and `docs/FMEA.md`.

Would you be willing to spend 30 minutes telling us where our method or our claims are weakest? A reply of "this part is wrong" is exactly what we want. You can read the three documents first (links below), and we will send questions in advance.

Repository: https://github.com/Younus0v/hydrowitness
Website: https://hydrowitness.vercel.app

Thank you,
[Name], Static Head Labs. [Adult mentor] is copied.

What to ask them (pick three):
1. Is the Time-to-Trouble worst case defined in a way you would trust? What would you change?
2. Which of our assumptions in `results/sensitivity.md` is least defensible?
3. What failure of the Witness Node are we missing in `docs/FMEA.md`?
4. Is our pump-test protocol enough to say anything about a real pump?

---

## 4. Request for a letter of support

Ask only after a conversation. Never ask for a letter from someone who has not seen what we did.

**Subject:** Would you be willing to write a short letter of support?

Dear [Name],

Thank you for the time you gave us on [date]. [One sentence on what we learned from them.]

We are preparing our Conrad Challenge Innovation Stage submission. Would you be willing to write a short letter (a few sentences is plenty) saying what you actually think of the problem and of our work, whatever that is? You are welcome to be critical. We will include only what you write, unedited, and only if you tell us we may. We would not use your name or organisation otherwise.

If helpful, the points a letter might cover are:
* Is "SCADA can't be trusted and the tank is the only truth" a real situation for systems like yours?
* Does a read-only second opinion look useful or not?
* What would you need to see before using something like this?

A draft is **not** attached: it must be your own words.

Thank you,
[Name], Static Head Labs. [Adult mentor] is copied.

---

## 5. After the call: thank-you note (send within a day)

Thank you for your time today. I wrote down [one specific thing they said] and we will [one specific thing you will change because of it]. If it is OK, I will send you a one-page summary of what we learn from all the conversations. No reply needed.
