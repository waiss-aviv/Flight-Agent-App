import os
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from datetime import datetime, timedelta
import json
import requests.exceptions

# --- 1. הגדרות וטעינת סודות ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# הגדרות קבועות ל-API של מגידו/Flight Circle
FLIGHT_CIRCLE_FBO_ID = "2698"
MEGIDDO_BASE_URL = "https://megiddo-agent-backend-1085562207224.europe-west1.run.app"
MEGIDDO_ENDPOINT = "/student_flights"

# --- 2. איתחול ה-Gemini Client ---
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    # במקום לצאת, נחזיר הודעת שגיאה ל-UI אם ה-Client לא מאותחל
    print("FATAL ERROR: Failed to initialize Gemini Client. Check GEMINI_API_KEY in .env.")
    # הפונקציה תטפל בשגיאה זו בהמשך, כעת נמשיך (אך הקריאה ל-API תכשל)
    pass


# --- 3. הגדרת הסכמה (Schema) באופן ידני ---
def get_flight_schema():
    """Defines the JSON schema for the fetch_student_flights function."""
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="fetch_student_flights",
                description="Fetches a list of historical flight records and logbook entries for a specific student (identified by user_id) within a given date range. Use this for all queries about student flight history, logbook, or training history.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "user_id": types.Schema(
                            type=types.Type.STRING,
                            description="Flight Circle UserID of the student."
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
                    required=["user_id", "start_date", "end_date"]
                )
            )
        ]
    )

# --- 4. פונקציית הביצוע בפועל (Middleware Code) ---
def fetch_student_flights(user_id: str, start_date: str, end_date: str):
    """מבצע את קריאת ה-API ל-Cloud Run."""
    
    request_body = {
        "fbo_id": FLIGHT_CIRCLE_FBO_ID,
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    
    headers = {"Content-Type": "application/json"}
    url = MEGIDDO_BASE_URL + MEGIDDO_ENDPOINT

    try:
        # --- תיקון ה-SSL: הוספת verify=False כדי לעקוף שגיאת אימות ---
        response = requests.post(url, headers=headers, json=request_body, timeout=15, verify=False)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        return {"error": f"API Request Failed: {e}"}


# --- 5. לולאת השיחה והתשאול (Agent Orchestration) ---

def run_agent_query(user_prompt: str):
    """
    מריץ שאילתה אחת ב-Gemini ומחזיר את התשובה הסופית כטקסט.
    """
    
    # ודא שה-Client אכן אותר (אם האתחול נכשל בשלב 2)
    if 'client' not in globals():
        return "שגיאה: Gemini Client לא אותחל בהצלחה. אנא בדוק את מפתח ה-API בקובץ .env."

    print(f"--- User Query ---\n{user_prompt}\n")
    
    messages = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
    ]
    
    # קריאה ראשונה למודל (מצרף את הסכמה הידנית)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=messages,
        config=types.GenerateContentConfig(
            tools=[get_flight_schema()] # שימוש בסכמה המוגדרת
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

    # הדפסת ה-Debug לטרמינל (עדיין חשוב)
    print("-" * 40)
    print("Gemini Response (in Hebrew):")
    print(response.text)
    print("-" * 40)
    
    # --- השינוי הקריטי: החזרת התשובה ל-Streamlit ---
    return response.text


# --- הדגמה/ניקוי סופי לקובץ ---
if __name__ == "__main__":
    # פונקציה זו כבר לא מריצה את הקוד לבד, אלא רק מציגה איך משתמשים בה
    # כדי להריץ את ה-Agent, השתמש ב-streamlit run app.py
    print("Agent script loaded successfully. Please run 'streamlit run app.py'.")
