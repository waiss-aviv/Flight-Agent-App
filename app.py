import streamlit as st
# ייבוא הפונקציה המרכזית מקובץ הלוגיקה
from flight_agent import run_agent_query 

# --- הגדרות בסיסיות של הממשק ---
st.set_page_config(page_title="Megiddo Flight Agent", layout="wide")

# --- CSS מותאם אישית ליישור לימין (RTL) ---
st.markdown("""
    <style>
        /* קובע את כיווניות הטקסט מימין לשמאל */
        div[data-testid="stAppViewContainer"] {
            direction: rtl;
        }
        
        /* יישור כותרות (h1, h2, h3) ותוצאות (Markdown) לימין */
        h1, h2, h3, h4, div[data-testid="stMarkdownContainer"] {
            text-align: right;
        }
        
        /* יישור שדות קלט (Text Input) לימין */
        input[type="text"], textarea {
            direction: rtl;
            text-align: right;
        }
        
        /* יישור כפתור השליחה */
        div[data-testid="stButton"] {
            display: flex;
            justify-content: right;
        }
    </style>
    """, unsafe_allow_html=True)
# --- סוף CSS ---

st.title("תשאול טיסות✈️, ")
st.markdown("כלי תשאול בשפה טבעית עבור נתוני טיסות תלמידים (מבוסס Gemini)")

# --- 1. שדה קלט של המשתמש ---
user_prompt = st.text_input(
    "שאל את ה-Agent שאלה על היסטוריית טיסות (לדוגמה: 'מהם נתוני הטיסות של 8280 בשנת 2024?')"
)

# --- 2. לחצן הפעלה ---
if st.button("שגר שאילתה") and user_prompt:
    
    # הצגת הודעת טעינה בזמן שה-Agent עובד
    with st.spinner('בודק את הנתונים ומסנתז תשובה...'):
        
        try:
            # --- 3. קריאה לפונקציית ה-Agent ---
            final_answer = run_agent_query(user_prompt)
            
            # --- 4. הצגת התשובה ---
            st.success("✅ ניתוח הושלם")
            st.markdown("---")
            st.subheader("תשובת ה-Agent:")
            
            # הצגת התשובה הסופית (הטקסט שהוחזר מ-Gemini)
            st.markdown(final_answer)

        except Exception as e:
            # הצגת שגיאה אם ה-Agent נכשל (לדוגמה, בעיות API, בעיות במפתח)
            st.error(f"⚠️ אירעה שגיאה קריטית במהלך הרצת ה-Agent: {e}")
            st.warning("ודא שמפתח ה-API של Gemini וקובץ .env הוגדרו נכון.")
