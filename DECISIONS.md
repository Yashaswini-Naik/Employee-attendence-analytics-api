# DECISIONS.md

## 1. Indexes
I use indexes on employee codes to find employees quickly and prevent duplicate codes. I also use attendance indexes on employee code and attendance date to support attendance lookups. I considered adding indexes to every field, but rejected this because extra indexes use storage and slow down writes.

## 2. Punch-in race
If two identical punch-in requests arrive together, both may initially check whether attendance exists. The database must prevent duplicate records using a unique constraint or index. One request succeeds, while the other receives a duplicate-key error and returns a conflict response. This depends on the unique index being created correctly.

## 3. Ties
The leaderboard sorts employees by late minutes and uses a consistent secondary sort, such as employee code. This makes the result deterministic. If the limit cuts through a tie, only the employees within the limit are returned.

## 4. Headcount
Department headcount should be calculated from employees who joined by the end of the reporting period, rather than from attendance logs. Therefore, employees with zero attendance records are still counted.

## 5. Scaling
For 100 times more data, I would test query performance with realistic data and MongoDB explain plans, optimize indexes, and ensure analytics use database aggregation instead of loading large collections into Python.