# Employee Attendance & Analytics API

A REST API built using FastAPI and MongoDB to manage employee records, track attendance, and generate analytics.

## Technologies Used
- Python
- FastAPI
- MongoDB
- PyMongo

## Features
- Health check
- Create and list employees
- Attendance punch-in and punch-out
- View and correct attendance records
- Monthly employee attendance analytics
- Department attendance summary
- Late-arrival leaderboard
- Department attendance trends
- Database query explain endpoint

## Requirements
- Python 3.10+
- MongoDB running locally or remotely

## Installation

Install the required dependencies:

```bash
pip install -r requirements.txt
```

Ensure MongoDB is running and configure the required environment variables.

## Run the Application

```bash
python -m uvicorn app.main:app --port 8002
```

## API Documentation

After starting the server, open:

http://127.0.0.1:8002/docs

## Project Structure

- `app/main.py` — Main API implementation
- `requirements.txt` — Project dependencies
- `openapi.yaml` — API contract
- `DATA_MODEL.md` — Database model documentation
- `REVIEW.md` — Starter-code review
- `DECISIONS.md` — Implementation decisions
- `sample_data/` — Sample JSON data
- `sample_seed.py` — Sample data seeding script
