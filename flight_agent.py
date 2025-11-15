import os
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
import requests.exceptions
import json
import re

# --- 1. הגדרות וטעינת סודות ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# הגדרות קבועות ל-API של מגידו/Cloud Run
FLIGHT_CIRCLE_FBO_ID = os.getenv("FLIGHT_CIRCLE_FBO_ID") or "2698"
MEGIDDO_BASE_URL = os.getenv("MEGIDDO_BASE_URL") or "https://megiddo-agent-backend-1085562207224.europe-west1.run.app"
MEGIDDO_ENDPOINT = "/student_flights"

# --- חדש: Endpoint לחיפוש שמות (Cloud Run) ---
MEGIDDO_USER_LOOKUP_URL = os.getenv("MEGIDDO_USER_LOOKUP_URL") or "https://flight-agent-api-1085562207224.europe-west1.run.app/users/by-name" 
MEGIDDO_API_KEY = os.getenv("MEGIDDO_API_KEY") # מפתח ה-Bearer Token לאימות

# --- 2. איתחול ה-Gemini Client (כפי שהיה) ---
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    print("FATAL ERROR: Failed to initialize Gemini Client. Check GEMINI_API_KEY in .env.")
    pass


# --- 3. פונקציית תרגום שם ל-ID באמצעות API (דינמי) ---
def resolve_user_name_to_id(name_or_id: str) -> str | dict:
    """
    מנסה לתרגם שם משתמש ל-ID באמצעות קריאת GET ל-Cloud Run Lookup Endpoint.
    """
    # 1. אם הקלט הוא ID מספרי, מחזירים אותו מיידית
    if re.fullmatch(r'\d+', name_or_id):
        return name_or_id

    # 2. בניית Headers עם Bearer Token (לאימות מול ה-Backend שלך)
    headers = {
        "Authorization": f"Bearer {MEGIDDO_API_KEY}" 
    }
    
    # 3. בניית פרמטרי השאילתה (Name Lookup)
    params = {"name": name_or_id}

    try:
        # קריאת GET ל-Endpoint החדש שלך ב-Cloud Run (users/by-name)
        response = requests.get(
            MEGIDDO_USER_LOOKUP_URL, 
            headers=headers, 
            params=params, 
            timeout=15, 
            verify=False
        ) 
        response.raise_for_status()
        
        search_results = response.json()
        
        # 4. פרסור התגובה: אם יש תוצאות, קח את ה-ID הראשון
        if search_results and isinstance(search_results, list) and len(search_results) > 0:
            # ה-ID שה-Backend החזיר הוא בתוך השדה 'id'
            customer_id = str(search_results[0].get("id"))
            
            if customer_id and customer_id != "None":
                return customer_id
        
        # 5. אם לולאת החיפוש הסתיימה ולא נמצאה התאמה
        return {"error": f"UserID not found for name: '{name_or_id}'. Please provide a valid full name or a numeric User ID."}
        
    except requests.exceptions.RequestException as e:
        return {"error": f"Cloud Run User Lookup connection failed. Error: {e}"}


# --- 4. הגדרת הסכמה (Schema) (כפי שהיה) ---
def get_flight_schema():
    """Defines the JSON schema for the fetch_student_flights function."""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="fetch_student_flights",
                description="Fetches flight records for a student within a date range. The 'user_identifier' can be a UserID number OR the student's full name (e.g., 'אביב וייס').",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "user_identifier": types.Schema(
                            type=types.Type.STRING,
                            description="Flight Circle UserID (e.g., '8280') or the student's name (e.g., 'אביב וייס')."
                        ),
                        "start_date": types.Schema(
                            type=types.Type.STRING,
                            description="Start date for the search range (YYYY-MM-DD)."
                        ),
                        "end_date": types.Schema(
                            type=types.Type.STRING,
                            description="End date for the search range (YYYY-MM-DD)."
                        )
                    },
                    required=["user_identifier", "start_date", "end_date"]
                )
            )
        ]
    )


# --- 5. פונקציית הביצוע בפועל (Middleware Code) ---
def fetch_student_flights(user_identifier: str, start_date: str, end_date: str):
    
    # --- שלב קריטי: תרגום שם ל-ID דרך API ---
    user_id_result = resolve_user_name_to_id(user_identifier)
    
    # בדיקה האם פונקציית התרגום החזירה מילון שגיאה
    if isinstance(user_id_result, dict) and "error" in user_id_result:
        # אם התרגום נכשל, מחזירים את השגיאה כפלט לכלי, ו-Gemini יסביר למשתמש
        return user_id_result
        
    user_id = user_id_result # זהו ה-ID התקף (מחרוזת)

    # הפנייה ל-API של מגידו/Cloud Run (ה-API המקורי שלך)
    request_body = {
        "fbo_id": FLIGHT_CIRCLE_FBO_ID,
        "user_id": user_id,  # נשלח את ה-ID שתורגם
        "start_date": start_date,
        "end_date": end_date,
    }
    
    # --- הוספת Bearer Token לאימות ---
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MEGIDDO_API_KEY}" # השתמש במפתח לאימות Cloud Run
    }
    url = MEGIDDO_BASE_URL + MEGIDDO_ENDPOINT

    try:
        # קריאת API לשרת הטיסות (Cloud Run)
        response = requests.post(url, headers=headers, json=request_body, timeout=15, verify=False)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        return {"error": f"API Request to Flight Service Failed: {e}"}


# --- 6. לולאת השיחה והתשאול (Agent Orchestration) ---

def run_agent_query(user_prompt: str):
    
    # ודא שה-Client אותר
    if 'client' not in globals():
        return "שגיאה: Gemini Client לא אותחל בהצלחה. אנא בדוק את מפתח ה-API בקובץ .env."

    print(f"--- User Query ---\n{user_prompt}\n")
    
    messages = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
    ]
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=messages,
        config=types.GenerateContentConfig(
            tools=[get_flight_schema()] 
        )
    )

    # --- לולאת Function Calling (הליבה האוטומטית) ---
    while response.function_calls:
        function_call = response.function_calls[0]
        func_name = function_call.name
        func_args = dict(function_call.args)
        
        print(f"-> Agent Calling Tool: {func_name} with args: {func_args}")
        
        # 1. הפעלה אוטומטית של פונקציית הביצוע
        if func_name == "fetch_student_flights":
            tool_output = fetch_student_flights(**func_args) 
        else:
            tool_output = {"error": f"Unknown function: {func_name}"}

        # 2. שליחת התוצאה (ה-JSON) חזרה ל-Gemini
        messages.append(response.candidates[0].content)
        messages.append(
            types.Content(
                role="tool", 
                parts=[types.Part.from_function_response(name=func_name, response=tool_output)]
            )
        )
        
        # 3. קריאה שנייה (וסופית) למודל כדי לסנתז את התשובה
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=messages
        )

    # הדפסת התשובה הסופית
    print("-" * 40)
    print("Gemini Response (in Hebrew):")
    print(response.text)
    print("-" * 40)
    
    # החזרת התשובה ל-Streamlit
    return response.text


# --- 7. דוגמה לתשאול ---
if __name__ == "__main__":
    print("Agent script loaded successfully.")
    print("1. Update .env with MEGIDDO_API_KEY (Bearer Token).")
    print("2. Run Streamlit with: 'python3 -m streamlit run app.py'")
