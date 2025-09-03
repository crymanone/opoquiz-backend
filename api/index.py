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
    schema_url: str = None

# --- 1. CONFIGURACIÓN DE APIs ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- 2. PROMPT ENGINEERING ---

def create_gemini_prompt_multiple(full_context: str, fragments: list) -> str:
    variety_instructions = [
        "un detalle específico o un dato numérico.",
        "una definición clave.",
        "las funciones o competencias de un órgano descrito.",
        "una comparación entre dos conceptos.",
        "una excepción a una regla general.",
        "un plazo, fecha o período de tiempo."
    ]
    variety_string = ", ".join(variety_instructions)

    fragment_section = ""
    for i, fragment in enumerate(fragments):
        fragment_section += f"\n--- FRAGMENTO {i+1} ---\n{fragment}\n"

    return f"""
    Actúa como un tribunal de oposición creando un examen variado y de alta dificultad.
    Te proporciono el CONTEXTO COMPLETO de un tema y una lista de 5 FRAGMENTOS ESPECÍFICOS.

    Tu tarea es generar una lista de 5 preguntas de test. Cada pregunta debe basarse
    única y exclusivamente en su fragmento correspondiente (Pregunta 1 -> Fragmento 1, etc.).

    Para asegurar la máxima variedad, para cada pregunta, intenta enfocarla en un tipo
    diferente de información. Considera los siguientes enfoques: {variety_string}
    No te repitas en el tipo de pregunta.

    La respuesta DEBE ser un array JSON que contenga 5 objetos JSON.
    El formato de salida debe ser estrictamente este:
    [
        {{
            "question": "Pregunta sobre el fragmento 1...",
            "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
            "correct_answer": "LETRA"
        }},
        {{
            "question": "Pregunta sobre el fragmento 2...",
            "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
            "correct_answer": "LETRA"
        }},
        ... (hasta 5 preguntas)
    ]

    --- CONTEXTO COMPLETO ---
    {full_context}
    ---

    {fragment_section}
    """

# --- 3. ENDPOINTS DE LA API ---
@app.get("/api")
def read_root():
    return {"status": "OpoQuiz API está conectada y funcionando!"}

@app.get("/api/topics")
def get_topics():
    try:
        response = supabase.table('topics').select('id, title, pdf_url,schema_url').execute()
        return {"topics": response.data}
    except Exception as e:
        return {"error": str(e)}, 500

@app.get("/api/get-question")
def get_question(topic_id: int):
    return generate_question_from_topic(topic_id)

@app.get("/api/get-random-question")
def get_random_question():
    try:
        all_topics_response = supabase.table('topics').select('id').filter('content', 'not.is', 'null').execute()
        if not all_topics_response.data:
            return {"error": "No hay temas con contenido en la base de datos."}, 404
        
        random_topic = random.choice(all_topics_response.data)
        random_topic_id = random_topic['id']
        return generate_question_from_topic(random_topic_id)
    except Exception as e:
        return {"error": f"Error al seleccionar un tema aleatorio: {str(e)}"}, 500

@app.post("/api/ask-topic")
def ask_topic(request: AskRequest):
    try:
        content_parts = []
        if request.schema_url:
            from PIL import Image
            print(f"Esquema encontrado, descargando imagen desde: {request.schema_url}")
            image_response = requests.get(request.schema_url)
            image_response.raise_for_status()
            img = Image.open(io.BytesIO(image_response.content))
            content_parts.append(img)
            content_parts.append("\nAnaliza tanto el texto como la imagen del esquema para responder.\n")

        prompt = f"""
        Actúa como un tutor experto de oposiciones. Tus fuentes de conocimiento son el texto del temario
        y, si se proporciona, la imagen del esquema adjunto.
        No puedes usar información externa. Responde a la pregunta del usuario de forma clara y concisa
        basándote estrictamente en la información proporcionada.

        --- TEXTO DEL TEMARIO ---
        {request.context}
        ---
        --- PREGUNTA DEL USUARIO ---
        {request.query}
        ---
        Respuesta:
        """
        content_parts.append(prompt)
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        response = model.generate_content(content_parts)
        return {"answer": response.text}
    except Exception as e:
        print(f"!!! ERROR en /api/ask-topic: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS ---
def generate_question_from_topic(topic_id: int):
    try:
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        if not response.data or not response.data.get('content'):
            return {"error": f"El tema {topic_id} no tiene contenido pre-procesado."}, 404
        
        full_text = response.data['content']
        
        min_length = 150
        all_fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > min_length]

        if len(all_fragments) < 5:
            # Fallback para temas cortos: usar la estrategia de fragmento único
            print(f"Tema {topic_id} demasiado corto, usando fallback de fragmento único.")
            selected_fragment = random.choice(all_fragments) if all_fragments else full_text
            
            single_prompt = f"""
            Actúa como un tribunal de oposición. Basa una pregunta de test **única y exclusivamente**
            en el siguiente FRAGMENTO ESPECÍFICO de un tema más grande.
            Formato JSON: {{"question": "...", "options": {{...}}, "correct_answer": "..."}}

            --- FRAGMENTO ESPECÍFICO ---
            {selected_fragment}
            ---
            """
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            gemini_response = model.generate_content(single_prompt)
            cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
            final_question = json.loads(cleaned_response)

        else:
            # Estrategia de múltiples fragmentos para temas largos
            selected_fragments = random.sample(all_fragments, 5)
            
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            prompt = create_gemini_prompt_multiple(full_context=full_text, fragments=selected_fragments)
            gemini_response = model.generate_content(prompt)
            
            cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
            list_of_questions = json.loads(cleaned_response)
            
            final_question = random.choice(list_of_questions)
        
        final_question['topic_id'] = topic_id
        return final_question

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        error_details = {"error": "El backend falló al generar la pregunta.", "details": str(e)}
        return error_details, 500