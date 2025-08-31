# api/index.py - VERSIÓN FINAL CON RUTAS CORREGIDAS

from fastapi import FastAPI

# Creamos la instancia de la app.
app = FastAPI()

# --- ENDPOINT RAÍZ ---
# Vercel lo expondrá en: /api/
@app.get("/")
def read_root():
    return {"status": "OpoQuiz API con FastAPI está online."}

# --- ENDPOINT DE PRUEBA ---
# Vercel lo expondrá en: /api/hello
@app.get("/hello")
def hello_world():
    return {"message": "¡Hola Mundo desde FastAPI!"}

# --- ENDPOINT REAL (DE PRUEBA) ---
# Vercel lo expondrá en: /api/get-question
@app.get("/get-question")
def get_question(topic_id: int):
    test_question = {
        "question": f"Pregunta para el topic_id={topic_id}. ¡La conexión y las rutas ahora funcionan!",
        "options": { "A": "Correcto", "B": "Falso", "C": "Falso", "D": "Falso" },
        "correct_answer": "A"
    }
    return test_question