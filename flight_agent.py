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

# --- חדש: הגדרות Flight Circle Lookup API ---
# ה-URL חייב להיות בצורת F-String כדי שנוכל להכניס את ה-FboID
FLIGHT_CIRCLE_USER_ENDPOINT_TEMPLATE = "https://www.flightcircle.com/v1/api/pub/users/{fbo_id}"
FLIGHT_CIRCLE_API_KEY = os.getenv("FLIGHT_CIRCLE_API_KEY")


# --- 2. איתחול ה-Gemini Client (כפי שהיה) ---
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    print("FATAL ERROR: Failed to initialize Gemini Client. Check GEMINI_API_KEY in .env.")
    pass


# --- 3. פונקציית תרגום שם ל-ID באמצעות API חיצוני (התיקון) ---
def resolve_user_name_to_id(name_or_id: str) -> str | dict:
    """
    מנסה לתרגם שם משתמש ל-CustomerID (שווה ערך ל-UserID) באמצעות קריאת API חיצונית.
    """
    # 1. אם הקלט הוא ID מספרי, מחזירים אותו מיידית
    if re.fullmatch(r'\d+', name_or_id):
        return name_or_id

    # 2. בדיקת תצורה חיונית
    if not FLIGHT_CIRCLE_API_KEY:
        return {"error": "Configuration Missing: FLIGHT_CIRCLE_API_KEY must be set in .env for name lookup."}

    # 3. בניית ה-URL וה Headers
    url = FLIGHT_CIRCLE_USER_ENDPOINT_TEMPLATE.format(fbo_id=FLIGHT_CIRCLE_FBO_ID)
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {FLIGHT_CIRCLE_API_KEY}" 
    }

    try:
        # --- תיקון קריטי: שימוש ב-GET כפי שנדרש על ידי ה-API ---
        # העברת שם משתמש (השם יטופל כפרמטר חיפוש עקיף)
        response = requests.get(url, headers=headers, timeout=15, verify=False) 
        response.raise_for_status()
        
        search_results = response.json()
        
        # 4. פרסור התגובה (מחפשים התאמה בתוך מערך 'data')
        
        normalized_input = name_or_id.lower().strip()
        
        if search_results and isinstance(search_results.get("data"), list):
            for user_data in search_results["data"]:
                full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".lower()
                
                # בדיקה גמישה: האם השם שהמשתמש הזין נמצא בשם המלא של המשתמש?
                if normalized_input in full_name:
                    # שימוש ב-CustomerID כ-UserID (בהתבסס על המבנה שלך)
                    return str(user_data["CustomerID"]) 
            
            # אם לולאת החיפוש הסתיימה ולא נמצאה התאמה
            return {"error": f"UserID not found via API search for name: '{name_or_id}'. No matching user ID found in the API response."}
        
        # אם ה-API החזיר מבנה לא תקין
        return {"error": "API response structure is invalid or empty."}
        
    except requests.exceptions.RequestException as e:
        return {"error": f"Flight Circle User Lookup API failed. Check API URL/Key. Error: {e}"}


# --- 4. הגדרת הסכמה (Schema) (נשאר זהה) ---
def get_flight_schema():
    """Defines the JSON schema for the fetch_student_flights function."""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="fetch_student_flights",
                description="Fetches flight records for a student within a date range. The 'user_identifier' can be a UserID number OR the student's full name (e.g., 'אביב כהן').",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "user_identifier": types.Schema(
                            type=types.Type.STRING,
                            description="Flight Circle UserID (e.g., '8280') or the student's name (e.g., 'אביב כהן')."
                        ),
                        "start_date": types.Schema(
                            type=types.Type.STRING,
                            description="Start date for the search range (YYYY-MM-DD). Default to 6 months ago if 'recent flights' is mentioned."
                        ),
                        "end_date": types.Schema(
                            type=types.Type.STRING,
                            description="End date for the search range (YYYY-MM-DD). Default to today's date."
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
    
    headers = {"Content-Type": "application/json"}
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
    print("1. Update .env with FLIGHT_CIRCLE_USER_ENDPOINT_TEMPLATE and FLIGHT_CIRCLE_API_KEY.")
    print("2. Run Streamlit with: 'python3 -m streamlit run app.py'")
