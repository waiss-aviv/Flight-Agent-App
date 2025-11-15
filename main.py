import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI()

FLIGHT_CIRCLE_BASE_URL = os.getenv("FLIGHT_CIRCLE_BASE_URL", "https://www.flightcircle.com/v1/api/pub")
FLIGHT_CIRCLE_CLIENT_ID = os.getenv("FLIGHT_CIRCLE_CLIENT_ID")
FLIGHT_CIRCLE_CLIENT_SECRET = os.getenv("FLIGHT_CIRCLE_CLIENT_SECRET")
FLIGHT_CIRCLE_REFRESH_TOKEN = os.getenv("FLIGHT_CIRCLE_REFRESH_TOKEN")

TOKEN_URL = f"{FLIGHT_CIRCLE_BASE_URL}/token"


class StudentFlightsRequest(BaseModel):
    fbo_id: str
    start_date: str   # YYYY-MM-DD
    end_date: str     # YYYY-MM-DD
    user_id: Optional[str] = None  # Flight Circle UserID לחניך (לא חובה)


class Flight(BaseModel):
    date: Optional[str] = None
    aircraft: Optional[str] = None
    lesson: Optional[str] = None
    duration: Optional[float] = None


class StudentFlightsResponse(BaseModel):
    flights: List[Flight]


@app.get("/health")
def health_check():
    return {"status": "ok"}


def _parse_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _calc_duration(f: dict) -> Optional[float]:
    hobbs_in = _parse_float(f.get("hobbs_in"))
    hobbs_out = _parse_float(f.get("hobbs_out"))
    if hobbs_in is not None and hobbs_out is not None:
        return hobbs_in - hobbs_out

    tach_in = _parse_float(f.get("tach_in"))
    tach_out = _parse_float(f.get("tach_out"))
    if tach_in is not None and tach_out is not None:
        return tach_in - tach_out

    return None


async def get_access_token() -> str:
    if not FLIGHT_CIRCLE_CLIENT_ID or not FLIGHT_CIRCLE_CLIENT_SECRET or not FLIGHT_CIRCLE_REFRESH_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Flight Circle OAuth credentials are not configured",
        )

    data = {
        "client_id": FLIGHT_CIRCLE_CLIENT_ID,
        "client_secret": FLIGHT_CIRCLE_CLIENT_SECRET,
        "refresh_token": FLIGHT_CIRCLE_REFRESH_TOKEN,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(TOKEN_URL, data=data)

    text = resp.text

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Failed to obtain access_token from Flight Circle, status {resp.status_code}, body={text}",
        )

    try:
        payload = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail=f"Token endpoint did not return JSON. Raw body: {text}",
        )

    access_token = payload.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=500,
            detail=f"No access_token in Flight Circle token response. Raw body: {payload}",
        )

    return access_token


@app.post("/student_flights", response_model=StudentFlightsResponse)
async def get_student_flights(req: StudentFlightsRequest):
    # ולידציה של תאריכים
    try:
        start = datetime.strptime(req.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")

    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    # access_token טרי
    access_token = await get_access_token()

    url = f"{FLIGHT_CIRCLE_BASE_URL}/flights/{req.fbo_id}"
    params = {
        "year": start.strftime("%Y"),
        "month": start.strftime("%m"),
        "day": start.strftime("%d"),
        "eyear": end.strftime("%Y"),
        "emonth": end.strftime("%m"),
        "eday": end.strftime("%d"),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

    text = resp.text

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Flight Circle /flights API error, status {resp.status_code}, body={text}",
        )

    try:
        payload = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail=f"/flights endpoint did not return JSON. Raw body: {text}",
        )

    # כאן התיקון: אם זו רשימה, משתמשים בה ישירות, אם זה dict מחפשים data
    if isinstance(payload, list):
        raw_flights = payload
    elif isinstance(payload, dict):
        raw_flights = payload.get("data", [])
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected /flights payload type {type(payload)}. Raw payload: {payload}",
        )

    if not isinstance(raw_flights, list):
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected /flights payload structure. data/list is not a list. Raw payload: {payload}",
        )

    flights: list[Flight] = []

    for f in raw_flights:
        if not isinstance(f, dict):
            continue

        if req.user_id is not None and str(f.get("UserID")) != str(req.user_id):
            continue

        flights.append(
            Flight(
                date=f.get("depart_date") or f.get("date"),
                aircraft=f.get("tail_number") or str(f.get("AircraftID")) if f.get("AircraftID") is not None else None,
                lesson=f.get("lesson_name"),
                duration=_calc_duration(f),
            )
        )

    return StudentFlightsResponse(flights=flights)
