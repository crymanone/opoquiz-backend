# api/index.py - VERSIÓN COMPLETA CON PROMPT MEJORADO Y ENDPOINT ALEATORIO

from fastapi import FastAPI
import os
import json
import requests
import io
import random
from supabase import create_client, Client
import google.generativeai as genai
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

# --- 1. CONFIGURACIÓN DE APIs (sin cambios) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- 2. PROMPT ENGINEERING (MEJORADO) ---
def create_gemini_prompt(topic_content: str) -> str:
    variety_instructions = [
        "enfócate en un detalle específico o un dato numérico del texto.",
        "basa la pregunta en una definición clave mencionada en el documento.",
        "crea una pregunta sobre las funciones o competencias de un órgano descrito.",
        "formula una pregunta que compare dos conceptos mencionados en el texto.",
        "haz una pregunta sobre una excepción a una regla general descrita.",
        "céntrate en un plazo, fecha o período de tiempo mencionado."
    ]
    selected_instruction = random.choice(variety_instructions)

    return f"""
    Eres un preparador de oposiciones experto y muy creativo.
    Tu objetivo es generar una pregunta de test variada y que ponga a prueba la atención al detalle del opositor.
    Para asegurar la variedad, esta vez, {selected_instruction}

    Requisitos estrictos de formato:
    - La respuesta debe ser un objeto JSON válido, sin texto o explicaciones adicionales.
    - La estructura debe ser:
    {{
      "question": "Texto de la pregunta...",
      "options": {{ "A": "...", "B": "...", "C": "...", "D": "..." }},
      "correct_answer": "LETRA_CORRECTA"
    }}
    - Las opciones incorrectas deben ser verosímiles pero erróneas según el texto proporcionado.

    Texto para basar la pregunta:
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
    try:
        response = supabase.table('topics').select('id, title').execute()
        return {"topics": response.data}
    except Exception as e:
        return {"error": str(e)}, 500

@app.get("/api/get-question")
def get_question(topic_id: int):
    # Esta función la reutilizaremos para no repetir código
    return generate_question_from_topic(topic_id)

# --- NUEVO ENDPOINT PARA PREGUNTAS ALEATORIAS ---
@app.get("/api/get-random-question")
def get_random_question():
    try:
        # 1. Obtener TODOS los temas de Supabase que tengan una URL de PDF
        all_topics_response = supabase.table('topics').select('id').filter('pdf_url', 'not.is', 'null').execute()
        
        if not all_topics_response.data:
            return {"error": "No hay temas con PDFs en la base de datos."}, 404
        
        # 2. Elegir el ID de un tema al azar
        random_topic = random.choice(all_topics_response.data)
        random_topic_id = random_topic['id']
        
        # 3. Llamar a nuestra función reutilizable con ese ID aleatorio
        return generate_question_from_topic(random_topic_id)
        
    except Exception as e:
        return {"error": f"Error al seleccionar un tema aleatorio: {str(e)}"}, 500

# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS ---
def generate_question_from_topic(topic_id: int):
    try:
        response = supabase.table('topics').select("pdf_url").eq('id', topic_id).single().execute()
        if not response.data or not response.data.get('pdf_url'):
            return {"error": f"No se encontró una URL de PDF para el topic_id {topic_id}"}, 404
        
        pdf_url = response.data['pdf_url']
        pdf_response = requests.get(pdf_url, timeout=20)
        pdf_response.raise_for_status()

        pdf_file = io.BytesIO(pdf_response.content)
        reader = PdfReader(pdf_file)
        pdf_text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pdf_text += page_text + "\n"
        
        if not pdf_text:
            return {"error": "El PDF parece estar vacío o no contiene texto extraíble."}, 400

        model = genai.GenerModel('gemini-1.5-flash-latest') # Usamos Flash que es más rápido y barato
        prompt = create_gemini_prompt(pdf_text)
        gemini_response = model.generate_content(prompt)
        
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        quiz_data = json.loads(cleaned_response)
        
        # Añadimos el ID del tema a la respuesta para que la app sepa de qué era la pregunta
        quiz_data['topic_id'] = topic_id
        
        return quiz_data

    # ... final del bloque try ...
    except Exception as e:
        # ---- MODIFICACIÓN PARA MEJORAR EL DEBUGGING ----
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}") # Log para nosotros en Vercel
    
        # Devolvemos un error mucho más específico al frontend
        error_details = {
            "error": "El backend falló al generar la pregunta.",
            "details": str(e)
        }
        return error_details, 500