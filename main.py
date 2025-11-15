import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# -----------------------------
# Environment variables
# -----------------------------

FLIGHTCIRCLE_BASE_URL = os.getenv(
    "FLIGHTCIRCLE_BASE_URL",
    "https://www.flightcircle.com/v1/api",
)
FLIGHTCIRCLE_API_KEY = os.getenv("FLIGHTCIRCLE_API_KEY")
FLIGHTCIRCLE_FBO_ID = os.getenv("FLIGHTCIRCLE_FBO_ID")

if not FLIGHTCIRCLE_API_KEY or not FLIGHTCIRCLE_FBO_ID:
    raise RuntimeError("FLIGHTCIRCLE_API_KEY and FLIGHTCIRCLE_FBO_ID must be set")


def flightcircle_headers() -> Dict[str, str]:
    """
    Headers לקריאות ל Flight Circle.
    עדכן כאן אם ב Insomnia אתה משתמש בפורמט אחר ל Authorization.
    """
    return {
        "Authorization": f"Bearer {FLIGHTCIRCLE_API_KEY}",
        "Accept": "application/json",
    }


# -----------------------------
# Pydantic models
# -----------------------------

class User(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None


class UserIdResponse(BaseModel):
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None


class Reservation(BaseModel):
    id: str
    user_id: str
    resource_name: Optional[str] = None
    start: str
    end: str
    status: Optional[str] = None


class Flight(BaseModel):
    id: str
    resource_name: str
    start: str
    end: str
    instructor_name: Optional[str] = None
    user_name: Optional[str] = None


# -----------------------------
# FastAPI app
# -----------------------------

app = FastAPI(
    title="Flight Circle Agent API",
    description="Proxy API בין Google Gemini / Agents לבין Flight Circle",
    version="1.4.0",
)


# -----------------------------
# Helper functions
# -----------------------------

async def fetch_flightcircle_users(keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    עטיפה ל Flight Circle Users API.

    לפי הדוגמה שקיבלת, תשובת ה API נראית כך:
    {
      "data": [ {...}, {...} ],
      "meta": { ... }
    }

    בפועל Flight Circle מחזיר את כל המשתמשים גם כששולחים keyword,
    לכן נעשה סינון בצד שלנו.
    """
    url = f"{FLIGHTCIRCLE_BASE_URL}/pub/users/{FLIGHTCIRCLE_FBO_ID}"

    params: Dict[str, Any] = {}
    if keyword:
        params["keyword"] = keyword  # אם בעתיד Flight Circle יתמכו בסינון, נשאיר זאת כאן

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, headers=flightcircle_headers(), params=params)
        except httpx.RequestError as ex:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_request_failed", "message": str(ex)},
            ) from ex

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    raw = resp.json()

    # לפי הפורמט שלך: הלקוחות יושבים תחת "data"
    if isinstance(raw, dict) and "data" in raw:
        data = raw["data"]
    else:
        data = raw

    if not isinstance(data, list):
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invalid_response_format",
                "message": "Expected 'data' to be a list of users from Flight Circle",
            },
        )

    return data


def extract_user_id(user: Dict[str, Any]) -> Optional[str]:
    """
    לפי הדוגמה שלך, ה ID הוא בשדה CustomerID.
    נשאיר עוד ווריאציות ליתר ביטחון.
    """
    candidate_keys = ["CustomerID", "customer_id", "customerId", "id"]
    for key in candidate_keys:
        value = user.get(key)
        if value not in (None, "", 0):
            return str(value)
    return None


def build_user_name(user: Dict[str, Any]) -> str:
    """
    בניית שם מלא מ first_name + last_name.
    """
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    return full or first or last or ""


# -----------------------------
# Endpoints
# -----------------------------

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}


@app.get(
    "/users/by-name",
    response_model=List[User],
    tags=["users"],
    summary="חיפוש משתמשים לפי שם",
    description="מחפש משתמשים ב Flight Circle לפי שם מלא או חלקי ומחזיר רשימת משתמשים תמציתית.",
)
async def get_user_by_name(
    name: str = Query(..., description="שם מלא או חלקי של המשתמש לחיפוש, כולל עברית"),
) -> List[User]:
    # מביאים את כל המשתמשים
    raw_users = await fetch_flightcircle_users(keyword=None)

    normalized_query = name.strip().lower()

    users: List[User] = []
    for u in raw_users:
        full_name = build_user_name(u)
        full_name_normalized = full_name.lower()

        # סינון בצד שלנו לפי שם
        if normalized_query in full_name_normalized:
            user_id = extract_user_id(u) or ""
            users.append(
                User(
                    id=user_id,
                    name=full_name,
                    email=u.get("email"),
                    phone=u.get("phone"),
                )
            )

    return users


@app.get(
    "/users/by-username",
    response_model=UserIdResponse,
    tags=["users"],
    summary="איתור User ID לפי username",
    description=(
        "מקבל username בדרך כלל כתובת אימייל כפי שהמשתמש מתחבר ל Flight Circle "
        "ומחזיר את ה User ID המתאים אם נמצא משתמש יחיד."
    ),
)
async def get_user_id_by_username(
    username: str = Query(..., description="Username כפי שמופיע ב Flight Circle, בדרך כלל אימייל התחברות"),
) -> UserIdResponse:
    # מביאים את כל המשתמשים ומסננים לוקלית לפי email
    raw_users = await fetch_flightcircle_users(keyword=None)

    normalized_username = username.strip().lower()

    matching_users: List[Dict[str, Any]] = []
    for u in raw_users:
        email = (u.get("email") or "").strip().lower()

        if normalized_username == email:
            matching_users.append(u)

    if not matching_users:
        raise HTTPException(
            status_code=404,
            detail={"error": "user_not_found", "message": f"No user found for username '{username}'"},
        )

    if len(matching_users) > 1:
        raise HTTPException(
            status_code=409,
            detail={"error": "multiple_users", "message": f"Multiple users found for username '{username}'"},
        )

    user = matching_users[0]

    user_id = extract_user_id(user)
    if not user_id:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "missing_user_id",
                "message": "User found by username but no ID field was detected",
                "raw_user_keys": list(user.keys()),
            },
        )

    return UserIdResponse(
        user_id=user_id,
        name=build_user_name(user),
        email=user.get("email"),
    )


@app.get(
    "/reservations/by-user",
    response_model=List[Reservation],
    tags=["reservations"],
    summary="רשימת הזמנות למשתמש",
    description="מחזיר רשימת הזמנות או טיסות מתוזמנות למשתמש בטווח תאריכים.",
)
async def get_reservations_by_user(
    user_id: str = Query(..., description="מזהה המשתמש ב Flight Circle"),
    start_date: Optional[str] = Query(None, description="תאריך התחלה בפורמט YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="תאריך סיום בפורמט YYYY-MM-DD"),
) -> List[Reservation]:
    # עדכן ל endpoint האמיתי של Flight Circle עבור reservations אם שונה
    url = f"{FLIGHTCIRCLE_BASE_URL}/pub/reservations/{FLIGHTCIRCLE_FBO_ID}"

    params: Dict[str, Any] = {"user_id": user_id}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, headers=flightcircle_headers(), params=params)
        except httpx.RequestError as ex:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_request_failed", "message": str(ex)},
            ) from ex

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data_list = data["data"]
    else:
        data_list = data

    if not isinstance(data_list, list):
        raise HTTPException(
            status_code=500,
            detail={"error": "invalid_response_format", "message": "Expected a list of reservations"},
        )

    reservations: List[Reservation] = []
    for r in data_list:
        reservations.append(
            Reservation(
                id=str(r.get("id")),
                user_id=str(r.get("user_id")),
                resource_name=r.get("resource_name"),
                start=r.get("start"),
                end=r.get("end"),
                status=r.get("status"),
            )
        )

    return reservations


@app.get(
    "/flights/by-date",
    response_model=List[Flight],
    tags=["flights"],
    summary="לוח טיסות לפי תאריך",
    description="מחזיר רשימת טיסות מתוזמנות לפי יום, ניתן לסנן לפי מטוס.",
)
async def get_flights_by_date(
    date: str = Query(..., description="תאריך בפורמט YYYY-MM-DD"),
    resource_id: Optional[str] = Query(None, description="מזהה מטוס לסינון"),
) -> List[Flight]:
    # עדכן ל endpoint האמיתי מול Flight Circle עבור schedule אם שונה
    url = f"{FLIGHTCIRCLE_BASE_URL}/pub/flights/{FLIGHTCIRCLE_FBO_ID}"

    params: Dict[str, Any] = {"date": date}
    if resource_id:
        params["resource_id"] = resource_id

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, headers=flightcircle_headers(), params=params)
        except httpx.RequestError as ex:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_request_failed", "message": str(ex)},
            ) from ex

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        data_list = data["data"]
    else:
        data_list = data

    if not isinstance(data_list, list):
        raise HTTPException(
            status_code=500,
            detail={"error": "invalid_response_format", "message": "Expected a list of flights"},
        )

    flights: List[Flight] = []
    for f in data_list:
        flights.append(
            Flight(
                id=str(f.get("id")),
                resource_name=f.get("resource_name") or "",
                start=f.get("start"),
                end=f.get("end"),
                instructor_name=f.get("instructor_name"),
                user_name=f.get("user_name"),
            )
        )

    return flights

