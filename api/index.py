# api/index.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
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

# --- DEFINIR MODELO DE DATOS PARA EL CHAT ---
class AskRequest(BaseModel):
    context: str
    query: str

# --- 1. CONFIGURACIÓN DE APIs ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- 2. PROMPT ENGINEERING (MEJORADO) ---
def create_gemini_prompt(full_context: str, specific_fragment: str) -> str:
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
    Actúa como un tribunal de oposición extremadamente riguroso. Tu objetivo es crear
    una pregunta de test muy específica.

    Te proporciono dos piezas de información:
    1.  El CONTEXTO COMPLETO del tema para que entiendas el marco general.
    2.  Un FRAGMENTO ESPECÍFICO del tema.

    Tu tarea es generar una pregunta de tipo test que se base **única y exclusivamente**
    en la información contenida dentro del **FRAGMENTO ESPECÍFICO**. Las otras opciones
    pueden usar información del contexto general para ser distractores creíbles,
    pero la respuesta correcta DEBE estar en el fragmento.

    Requisitos estrictos de formato:
    - La respuesta debe ser un objeto JSON válido, sin texto adicional.
    - Estructura: {{"question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_answer": "LETRA"}}

    --- CONTEXTO COMPLETO ---
    {full_context}
    ---

    --- FRAGMENTO ESPECÍFICO PARA BASAR LA PREGUNTA ---
    {specific_fragment}
    ---
    """

# --- 3. ENDPOINTS DE LA API ---
@app.get("/api")
def read_root():
    return {"status": "OpoQuiz API está conectada y funcionando!"}

@app.get("/api/topics")
def get_topics():
    try:
        response = supabase.table('topics').select('id, title, pdf_url').execute()
        return {"topics": response.data}
    except Exception as e:
        return {"error": str(e)}, 500

@app.get("/api/get-question")
def get_question(topic_id: int):
    return generate_question_from_topic(topic_id)

@app.get("/api/get-random-question")
def get_random_question():
    try:
        all_topics_response = supabase.table('topics').select('id').filter('pdf_url', 'not.is', 'null').execute()
        if not all_topics_response.data:
            return {"error": "No hay temas con PDFs en la base de datos."}, 404
        
        random_topic = random.choice(all_topics_response.data)
        random_topic_id = random_topic['id']
        return generate_question_from_topic(random_topic_id)
    except Exception as e:
        return {"error": f"Error al seleccionar un tema aleatorio: {str(e)}"}, 500

# --- NUEVO ENDPOINT PARA EL CHAT (VERSIÓN DE PRUEBA) ---
@app.post("/api/ask-topic")
def ask_topic(request: AskRequest):
    """
    Recibe un texto de contexto (el temario) y una pregunta del usuario.
    Usa Gemini para generar una respuesta a la pregunta basada en el contexto.
    """
    try:
        # Construimos un prompt específico para la tarea de "Tutor de IA"
        prompt = f"""
        Actúa como un tutor experto de oposiciones. Tu única fuente de conocimiento es el siguiente texto.
        No puedes usar información externa. Responde a la pregunta del usuario de forma clara, concisa y
        basándote estrictamente en la información proporcionada en el texto.
        Si la respuesta no se encuentra en el texto, indica amablemente que no tienes
        información sobre ese punto en el material de estudio.

        --- TEXTO DEL TEMARIO ---
        {request.context}
        ---

        --- PREGUNTA DEL USUARIO ---
        {request.query}
        ---

        Respuesta concisa y directa:
        """

        # Usamos un modelo rápido y eficiente para el chat
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        
        # Devolvemos la respuesta real de Gemini
        return {"answer": response.text}

    except Exception as e:
        print(f"!!! ERROR GRAVE en /api/ask-topic: {e}")
        # Si algo falla, devolvemos un mensaje de error claro a la app
        raise HTTPException(status_code=500, detail=f"Error de la IA: {str(e)}")

# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS ---

# En api/index.py

def generate_question_from_topic(topic_id: int):
    try:
        # 1. Obtener el texto pre-procesado directamente de la columna 'content'
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        
        if not response.data or not response.data.get('content'):
            return {"error": f"El tema {topic_id} no tiene contenido pre-procesado."}, 404

        full_text = response.data['content']
        
        # 2. Dividir en fragmentos (la lógica de aleatoriedad que ya teníamos)
        min_length = 150
        fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > min_length]
        
        selected_fragment = random.choice(fragments) if fragments else full_text
        
        # 3. Llamar a Gemini (sin cambios)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        prompt = create_gemini_prompt(full_context=full_text, specific_fragment=selected_fragment)
        gemini_response = model.generate_content(prompt)
        
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        quiz_data = json.loads(cleaned_response)
        quiz_data['topic_id'] = topic_id
        
        return quiz_data

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        error_details = {"error": "El backend falló al generar la pregunta.", "details": str(e)}
        return error_details, 500
