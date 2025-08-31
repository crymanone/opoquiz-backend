# api/index.py - LA VERSIÓN DEFINITIVA Y FUNCIONAL

from fastapi import FastAPI
import os
import json
# Necesitaremos estas librerías más tarde
# import requests 
# from supabase import create_client, Client
# import google.generativeai as genai

# Creamos la instancia de la app. Vercel la buscará.
app = FastAPI()

# --- ENDPOINT DE PRUEBA ---
# Para verificar que las rutas funcionan
@app.get("/api/hello")
def hello_world():
    return {"message": "¡Hola Mundo desde FastAPI!"}

# --- ENDPOINT REAL ---
# Re-introducimos el código que se conecta a las APIs
@app.get("/api/get-question")
def get_question(topic_id: int):
    # Por ahora, devolvemos una respuesta fija
    # En el siguiente paso, reemplazaremos esto con la lógica real
    test_question = {
        "question": f"Pregunta para el topic_id={topic_id}. La conexión funciona.",
        "options": {
            "A": "Supabase y Gemini se conectarán aquí.",
            "B": "Opción Falsa 1",
            "C": "Opción Falsa 2",
            "D": "Opción Falsa 3"
        },
        "correct_answer": "A"
    }
    return test_question

# Opcional: una ruta raíz que simplemente confirme que la app está viva
@app.get("/api")
def read_root():
    return {"status": "OpoQuiz API con FastAPI está online."}