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
from thefuzz import fuzz

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
    
    # Las instrucciones de variedad se mantienen igual
    variety_instructions = [
        "un detalle específico o un dato numérico.", "una definición clave.",
        "las funciones o competencias de un órgano descrito.", "una comparación entre dos conceptos.",
        "una excepción a una regla general.", "un plazo, fecha o período de tiempo."
    ]
    variety_string = ", ".join(variety_instructions)

    # La construcción de la sección de fragmentos se mantiene igual
    fragment_section = ""
    for i, fragment in enumerate(fragments):
        fragment_section += f"\n--- FRAGMENTO DE TEXTO #{i+1} ---\n{fragment}\n"

    # --- INICIO DEL NUEVO PROMPT MEJORADO ---
    return f"""
    **ROL Y OBJETIVO:**
    Eres un miembro experto de un tribunal de oposiciones. Tu tarea es crear un conjunto de 5 preguntas de examen originales y variadas. No debes mencionar nunca la fuente de la información (como "según el texto" o "según el fragmento"). La pregunta debe ser directa.

    **FUENTES DE INFORMACIÓN PROPORCIONADAS:**
    1.  CONTEXTO GENERAL: Un documento completo con todo el temario. Úsalo para entender el tema globalmente y crear opciones de respuesta incorrectas que sean creíbles.
    2.  LISTA DE FRAGMENTOS: Una lista de 5 fragmentos de texto específicos.

    **INSTRUCCIONES ESTRICTAS:**
    1.  Debes generar exactamente 5 preguntas de test.
    2.  Cada pregunta debe basarse **única y exclusivamente en su fragmento correspondiente** (Pregunta 1 -> Fragmento 1, Pregunta 2 -> Fragmento 2, etc.).
    3.  Para asegurar la variedad, intenta que cada pregunta se enfoque en un tipo de información diferente. Considera: {variety_string}.
    4.  **IMPORTANTE:** Nunca, bajo ninguna circunstancia, empieces una pregunta con frases como "Según el fragmento...", "De acuerdo con el texto...", etc. La pregunta debe ser directa y autónoma.
    
    **FORMATO DE SALIDA OBLIGATORIO:**
    Tu respuesta debe ser únicamente un array JSON válido, sin ningún otro texto. La estructura debe ser:
    [
        {{"question": "...", "options": {{...}}, "correct_answer": "..."}},
        {{"question": "...", "options": {{...}}, "correct_answer": "..."}},
        ... (5 objetos en total)
    ]

    **--- INICIO DE LAS FUENTES DE INFORMACIÓN ---**

    **CONTEXTO GENERAL:**
    {full_context}

    **LISTA DE FRAGMENTOS:**
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


def generate_question_from_topic(topic_id: int):
    try:
        # --- 1. OBTENCIÓN DE DATOS (sin cambios) ---
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        if not response.data or not response.data.get('content'):
            return {"error": f"El tema {topic_id} no tiene contenido pre-procesado."}, 404
        
        full_text = response.data['content']
        all_fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > 150]

        if not all_fragments:
            return {"error": "El tema es demasiado corto para generar preguntas."}, 400

        recent_questions_response = supabase.table('preguntas_generadas') \
            .select('question_text') \
            .eq('topic_id', topic_id) \
            .order('created_at', desc=True) \
            .limit(100) \
            .execute()
        recent_question_texts = [q['question_text'] for q in recent_questions_response.data]

        ### --- INICIO DE LA LÓGICA DE GENERACIÓN Y FILTRADO EN LOTE --- ###
        
        # --- 2. GENERAR UN LOTE DE CANDIDATAS CON UNA SOLA LLAMADA ---
        num_candidates = 5
        # Asegurarnos de no pedir más fragmentos de los que hay
        num_to_select = min(num_candidates, len(all_fragments))
        
        if num_to_select == 0:
            return {"error": "No hay fragmentos válidos en el tema."}, 400
            
        selected_fragments = random.sample(all_fragments, num_to_select)

        # Usamos el prompt múltiple que ya teníamos
        prompt = create_gemini_prompt_multiple(full_context=full_text, fragments=selected_fragments)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        gemini_response = model.generate_content(prompt)
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        
        try:
            list_of_questions = json.loads(cleaned_response)
        except json.JSONDecodeError:
            return {"error": "La IA devolvió un formato JSON inválido en el lote."}, 500

        # --- 3. FILTRAR EL LOTE PARA ENCONTRAR UNA PREGUNTA ÚNICA ---
        SIMILARITY_THRESHOLD = 90
        
        for candidate in list_of_questions:
            candidate_text = candidate.get('question')
            if not candidate_text: continue # Ignorar si la IA genera un objeto malformado

            is_too_similar = False
            for recent_question in recent_question_texts:
                if fuzz.token_set_ratio(candidate_text, recent_question) > SIMILARITY_THRESHOLD:
                    is_too_similar = True
                    break 
            
            if not is_too_similar:
                # ¡HEMOS ENCONTRADO UNA! La guardamos y la devolvemos.
                print("¡Pregunta única encontrada en el lote!")
                supabase.table('preguntas_generadas').insert({
                    'question_text': candidate_text,
                    'topic_id': topic_id
                }).execute()
                
                candidate['topic_id'] = topic_id
                return candidate
        
        # --- 4. SI NO ENCONTRAMOS NINGUNA EN EL LOTE ---
        print("Todas las candidatas del lote eran repetidas. Devolviendo una pregunta aleatoria del lote para no fallar.")
        # Como fallback, para que la app no se cuelgue, devolvemos una aleatoria del lote.
        # Es mejor una repetida que un error.
        final_question = random.choice(list_of_questions)
        final_question['topic_id'] = topic_id
        return final_question

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        error_details = {"error": "El backend falló al generar la pregunta.", "details": str(e)}
        return error_details, 500