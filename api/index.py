# api/index.py - Código final para la estructura con vercel.json

from fastapi import FastAPI
app = FastAPI()

@app.get("/api")
def read_root():
    return {"status": "¡ÉXITO TOTAL! Vercel está usando la configuración explícita."}

@app.get("/api/get-question")
def get_question():
    test_question = {
        "question": "OpoQuiz está listo para conectar con Supabase y Gemini.",
        "options": { "A": "A", "B": "B", "C": "C", "D": "D" },
        "correct_answer": "A"
    }
    return test_question