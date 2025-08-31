# api/index.py - VERSIÓN FINAL Y COMPLETA

from fastapi import FastAPI
import os
import json
from supabase import create_client, Client
import google.generativeai as genai
from dotenv import load_dotenv

# Cargar variables de entorno para pruebas locales
load_dotenv()

app = FastAPI()

# --- 1. CONFIGURACIÓN DE APIs ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Inicializar clientes
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- 2. PROMPT ENGINEERING ---
def create_gemini_prompt(topic_content: str) -> str:
    return f"""
    Eres un preparador de oposiciones experto.
    Basándote estrictamente en el siguiente texto, genera una pregunta de tipo test.
    La respuesta debe ser un objeto JSON válido, sin texto adicional, con esta estructura:
    {{
      "question": "Texto de la pregunta...",
      "options": {{ "A": "...", "B": "...", "C": "...", "D": "..." }},
      "correct_answer": "LETRA_CORRECTA"
    }}

    Texto proporcionado:
    ---
    {topic_content}
    ---
    """

# --- 3. ENDPOINTS DE LA API ---
@app.get("/api")
def read_root():
    return {"status": "OpoQuiz API está conectada y funcionando!"}

@app.get("/api/topics")
def get_topics():
    """
    Consulta la base de datos de Supabase y devuelve una lista de todos los
    temas disponibles. Cada tema incluirá su 'id' y su 'title'.
    """
    try:
        # Seleccionamos solo las columnas 'id' y 'title' de la tabla 'topics'
        response = supabase.table('topics').select('id, title').execute()
        
        # Devolvemos los datos en un formato JSON claro
        return {"topics": response.data}
    
    except Exception as e:
        return {"error": str(e)}, 500
### ---- FIN DEL NUEVO ENDPOINT ---- ###
    

@app.get("/api/get-question")
def get_question(topic_id: int):
    try:
        # Paso 1: Obtener contenido de Supabase
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        topic_content = response.data['content']

        # Paso 2: Preparar y llamar a Gemini
        model = genai.GenerativeModel('gemini-1.5-pro-latest') # O el modelo que te funcione
        prompt = create_gemini_prompt(topic_content)
        gemini_response = model.generate_content(prompt)
        
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        quiz_data = json.loads(cleaned_response)
        
        return quiz_data

    except Exception as e:
        # Devolvemos un error claro si algo falla
        return {"error": str(e)}, 500