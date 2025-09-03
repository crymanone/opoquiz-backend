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

# En api/index.py

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

    La respuesta DEBE ser un array JSON que contenga 5 objetos JSON.
    El formato de salida debe ser estrictamente este, sin añadir coletillas como "Según el fragmento...":
    [
        {{
            "question": "¿Cuál es la capital de España?",
            "options": {{"A": "Lisboa", "B": "Madrid", "C": "París", "D": "Roma"}},
            "correct_answer": "B"
        }},
        // ... 4 objetos más con la misma estructura ...
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


# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS (VERSIÓN CON MEMORIA CORREGIDA) ---
def generate_question_from_topic(topic_id: int):
    try:
        # --- OBTENCIÓN Y FRAGMENTACIÓN (sin cambios) ---
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        if not response.data or not response.data.get('content'):
            return {"error": f"El tema {topic_id} no tiene contenido pre-procesado."}, 404
        
        full_text = response.data['content']
        all_fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > 150]

        if not all_fragments:
            return {"error": "El tema es demasiado corto para generar preguntas."}, 400

        ### --- INICIO DE LA NUEVA LÓGICA DE MEMORIA --- ###

        # 1. Función para normalizar texto (ignorar mayúsculas, espacios, etc.)
        def normalize_text(text):
            return ''.join(text.lower().split())

        # 2. Obtener el historial de preguntas UNA SOLA VEZ
        recent_questions_response = supabase.table('preguntas_generadas') \
            .select('question_text') \
            .eq('topic_id', topic_id) \
            .order('created_at', desc=True) \
            .limit(100) \
            .execute()
        
        # Guardamos una versión normalizada del historial para comparar
        recent_questions_normalized = {normalize_text(q['question_text']) for q in recent_questions_response.data}
        
        # 3. Bucle de intentos para encontrar una pregunta única
        MAX_ATTEMPTS = 7 # Aumentamos los intentos
        for attempt in range(MAX_ATTEMPTS):
            print(f"Intento de generación #{attempt + 1}")

            # 3a. Seleccionar un fragmento aleatorio
            selected_fragment = random.choice(all_fragments)
            
            # 3b. Generar UNA SOLA pregunta candidata
            single_prompt = f"""
            Actúa como un tribunal de oposición. Basa una pregunta de test única y exclusivamente
            en el siguiente FRAGMENTO ESPECÍFICO. Evita empezar la pregunta con coletillas como "Según el fragmento...".
            Formato JSON: {{"question": "...", "options": {{...}}, "correct_answer": "..."}}

            --- FRAGMENTO ESPECÍFICO ---
            {selected_fragment}
            ---
            """
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            gemini_response = model.generate_content(single_prompt)
            cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
            candidate_question = json.loads(cleaned_response)
            
            # 3c. Comprobar si la pregunta es nueva (usando la versión normalizada)
            candidate_normalized = normalize_text(candidate_question['question'])
            
            if candidate_normalized not in recent_questions_normalized:
                print("¡Pregunta única encontrada!")
                
                # 3d. Guardar la pregunta en la base de datos y devolverla
                supabase.table('preguntas_generadas').insert({
                    'question_text': candidate_question['question'],
                    'topic_id': topic_id
                }).execute()
                
                candidate_question['topic_id'] = topic_id
                return candidate_question

        # 4. Si el bucle termina, no hemos encontrado una pregunta única.
        print(f"No se pudo generar una pregunta única en {MAX_ATTEMPTS} intentos.")
        return {"error": "No se pudo generar una pregunta única, prueba en un momento."}, 500

        ### --- FIN DE LA NUEVA LÓGICA DE MEMORIA --- ###

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        error_details = {"error": "El backend falló al generar la pregunta.", "details": str(e)}
        return error_details, 500