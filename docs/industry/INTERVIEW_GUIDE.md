# Operator interview guide (20 minutes)

**Who to ask:** operators, superintendents or managers of small community water systems (the target is systems with a storage tank and a few pumps), rural water association staff, and city or county utility staff. Five conversations is a start. Ten is better. Stop counting a conversation if the person only said polite things and nothing about how their system works.

**Who is on the call:** one person asks, one writes. An adult knows the call is happening. If the person is not comfortable with a student call, thank them and end it.

## Before you start: say this, then ask permission

> "Hi, I'm [name], a student on a team called Static Head Labs. We're in a school competition and we're trying to understand how small water systems cope when their control screen can't be trusted, for example when it freezes or goes blank. We're not selling anything and this isn't a sales call. Could I ask you a few questions about how your system works? It takes about 20 minutes. I'd like to take notes. I won't write down your name or your system's name unless you tell me it's fine. You can skip any question or stop at any time. Is that OK?"

Ask once more: "Is it OK if I note down what you say, without your name, and mention it in our competition report?" If they say no, take no notes you could share, and use it only to learn.

## Questions (ask about the past, not the future)

**Their system (5 min)**
1. How many people does your system serve, roughly? How many tanks and pumps?
2. How do you know, right now, how full the tank is? What are all the ways?
3. What happens when your SCADA, or whatever you look at, goes down or shows nonsense? Tell me about the last time.

**When it goes wrong (7 min)**
4. How long does it take you or someone else to physically get to the tank or pump if the screen can't be trusted? At night?
5. What do you do first? Is there a written procedure? Do you practise it?
6. Have you ever not known whether a reading was true? What did you do? How did you find out?
7. Do you know how many hours your system could run if the pumps stopped? How do you know that number?

**What would help, and what would not (6 min)**
8. If something told you "I think the screen is wrong, and here's why", what would you want it to show you? What would make you ignore it?
9. How many false alarms a month would you tolerate before you stopped looking? (Let them say a number. Do not suggest one.)
10. Would you let a small battery-powered sensor box sit on the outside of a pump? What would worry you about that?
11. Who would have to say yes before you tried something like this?

**Close (2 min)**
12. What did we not ask that we should have?
13. Is there one other person you think we should talk to?
14. May we send you a one-page summary of what we learn? (Collect an email only if they say yes.)

## Rules for the asker

* Do **not** describe our idea in the first ten minutes. If they ask, give one sentence: "It watches the tank and the pumps independently of SCADA and tells you when the screen disagrees with the physical clues."
* Do **not** ask "would you buy this?" or "would you use this?". People say yes to be polite. Ask what they did last time.
* Do **not** correct them. If they say something we think is wrong, write it down and check it later.
* If they mention a real incident, vulnerability or security detail of their system, say "Please don't tell me anything that could help someone attack it" and move on.
* Never ask for a password, a network diagram, a screenshot of SCADA, or the name of the product they use.

## Notes template (write the same day)

```
Date:            Role (operator / manager / association / other):
System size (rough): Tanks: Pumps:
Interviewer / note-taker:      Adult informed: yes / no
Permission to quote without name: yes / no       Follow-up allowed: yes / no

What they do now when the screen can't be trusted:
Time to reach the site:
Biggest surprise to us:
Words they used for the problem (exact quotes if allowed):
What would make them ignore an alert:
False alarms they would tolerate (their number):
Objections to a sensor on the pump:
Who else decides:
What we got wrong that this shows:
What we will change:
```
