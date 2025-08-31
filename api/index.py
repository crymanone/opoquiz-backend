# api/index.py - LA VERSIÓN FINAL CON LAS RUTAS ABSOLUTAS CORRECTAS

from fastapi import FastAPI
app = FastAPI()

# --- ENDPOINT RAÍZ ---
# La ruta DEBE ser "/api/" porque es lo que Vercel nos envía.
@app.get("/api")
def read_root():
    return {"status": "¡Éxito! La ruta /api/ funciona."}

# --- ENDPOINT DE PRUEBA ---
# La ruta DEBE ser "/api/hello"
@app.get("/api/hello")
def hello_world():
    return {"message": "¡Éxito! La ruta /api/hello funciona."}

# --- ENDPOINT REAL (DE PRUEBA) ---
# La ruta DEBE ser "/api/get-question"
@app.get("/api/get-question")
def get_question():
    test_question = {
        "question": "¡Éxito! La ruta /api/get-question funciona.",
        "options": { "A": "Correcto", "B": "Falso", "C": "Falso", "D": "Falso" },
        "correct_answer": "A"
    }
    return test_question