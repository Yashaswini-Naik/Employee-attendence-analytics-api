# REVIEW.md

## Defects Found in the Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | Does not check whether MongoDB is reachable. | Stop MongoDB and call `/health`. | Added a database connectivity check. |
| 2 | `create_employee` | Duplicate employee codes may be inserted during concurrent requests. | Send simultaneous requests with the same employee code. | Added duplicate-key handling and duplicate prevention. |
| 3 | `create_employee` | Employee input validation may be incomplete. | Submit missing or invalid fields. | Added request validation. |
| 4 | `list_employees` | Pagination skip calculation may be incorrect. | Compare results from page 1 and page 2. | Used `(page - 1) * page_size`. |
| 5 | `list_employees` | Total count may not match the applied filters. | Filter by department and compare the count. | Applied identical filters to results and count queries. |
| 6 | Date and timestamp helpers | Incorrect timezone handling can affect attendance calculations. | Test timestamps containing an IST offset. | Standardized timezone-aware datetime handling. |
| 7 | Attendance calculations | Late-arrival calculation may mishandle the 10-minute grace period. | Test exactly 10 minutes late and just beyond 10 minutes. | Applied the strict greater-than-10-minute rule. |
| 8 | Attendance calculations | Overtime may be calculated before the required threshold. | Test 29-minute and 30-minute overtime cases. | Counted overtime only when it reaches 30 minutes. |
| 9 | Attendance calculations | Work-hour rounding may be incorrect. | Test durations requiring decimal rounding. | Applied half-up rounding to two decimal places. |
| 10 | Attendance calculations | Half-day status may use the wrong threshold. | Test work hours around 4.50 hours. | Applied the rule that rounded work hours below 4.50 indicate a half-day. |
| 11 | `punch_in` | Unknown employees may not be handled correctly. | Punch in using an unknown employee code. | Added employee existence validation. |
| 12 | `punch_in` | Concurrent requests may create duplicate attendance records. | Send multiple punch-in requests for the same employee and date. | Added safeguards against duplicate attendance creation. |
| 13 | `punch_out` | Required punch-out behavior may be missing or incomplete. | Punch out without punching in, or punch out twice. | Added punch-out validation and attendance updates. |
| 14 | `list_attendance` | Loading and sorting all records in Python is inefficient. | Test with a large attendance collection. | Moved filtering, sorting, and pagination into MongoDB queries. |
| 15 | `list_attendance` | Filtered results and total count may be inconsistent. | Filter attendance by employee and date. | Applied consistent filters to both queries. |
| 16 | Attendance responses | Internal MongoDB IDs may be exposed unnecessarily. | Inspect response JSON for `_id`. | Returned only the required response fields. |
| 17 | Attendance PATCH endpoint | Attendance correction may be missing or incomplete. | Call `PATCH /attendance/{emp_code}/{date}`. | Implemented attendance correction and validation. |
| 18 | Monthly employee analytics | Monthly analytics may be missing. | Call `/analytics/employees/{emp_code}/monthly`. | Implemented monthly attendance aggregation. |
| 19 | Department summary analytics | Department summaries may be missing. | Call `/analytics/departments/summary`. | Implemented department-level aggregation. |
| 20 | Late-arrival leaderboard | Late-arrival leaderboard may be missing. | Call `/analytics/leaderboard/late`. | Implemented leaderboard aggregation. |
| 21 | Department trend analytics | Department trend endpoint may be missing. | Call `/analytics/departments/{department}/trend`. | Implemented department trend aggregation. |
| 22 | Admin explain endpoint | Query explanation functionality may be missing. | Call `/admin/explain/{endpoint}`. | Implemented query-plan information for supported endpoints. |
| 23 | Database indexes | Missing indexes can make frequent queries slow. | Inspect indexes and query execution plans. | Added indexes for frequently queried fields. |

## Items Reviewed but Not Considered Defects

1. Returning an empty list when no records match is valid if the response format and total count are correct.
2. Returning a not-found response for an unknown employee is appropriate when required by the API contract.
3. Using MongoDB aggregation pipelines for analytics is appropriate for processing large datasets.
4. Returning an empty list for a page beyond the available records is valid if the total count remains correct.

## Verification Status

The listed issues and fixes must be checked against the original starter code and final implementation. Only confirmed defects should be reported as actual findings. Endpoints and edge cases that have not been tested should not be described as verified. Testing should cover valid inputs, invalid inputs, boundary conditions, and database failures.
