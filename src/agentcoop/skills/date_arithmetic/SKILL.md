---
name: date-arithmetic
type: agent-skill
description: Date reasoning tactics for numerical RC — MM/DD/YYYY format, leap years, days-between, month-length lookup.
domain_tags:
  - dates
  - numerical
  - reading_comprehension
  - drop
capability_tags:
  - date_arithmetic
  - format_discipline
  - calendar
shareable: true
---

## Format discipline

- **Prefer the format the grader expects.** DROP accepts `MM/DD/YYYY` for dates and plain `YYYY` for year-only questions. Use ISO only if the question is explicit.
- **Strip day/month when the question asks only for the year.** "In what year..." → `1986`, not `01/01/1986`.
- **Zero-pad** single-digit months and days in `MM/DD/YYYY` (`03/05/2021`, not `3/5/2021`).

## Core facts (memorize)

- **Days per month:** Jan 31, Feb 28 (29 in leap), Mar 31, Apr 30, May 31, Jun 30, Jul 31, Aug 31, Sep 30, Oct 31, Nov 30, Dec 31.
- **Leap year:** divisible by 4, except centuries, except centuries divisible by 400. (2000 is leap; 1900 is not.)
- **Days of week:** don't compute by hand — use Zeller's congruence or code.

## Operations

### Difference in years

If the question asks "how many years between A and B", compute `|yearB - yearA|`. If month/day comparison matters (e.g., "how old was X at time Y"), subtract one when the later date's month/day has not yet reached the earlier date's month/day.

### Difference in days

Use Python `datetime`:
```python
from datetime import date
days = (date(y2, m2, d2) - date(y1, m1, d1)).days
```
Never compute day differences by hand — leap-year arithmetic is error-prone.

### Month/day arithmetic

Use `datetime.timedelta` for adding/subtracting days. For month offsets, use `dateutil.relativedelta` if available, or decompose into year+month components and renormalize.

## When code is essential

Any date arithmetic spanning leap years, month boundaries, or more than a handful of days should be delegated to Python's `datetime` module. Do not trust free-form LLM arithmetic on dates.
