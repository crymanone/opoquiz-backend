# api/index.py

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import Optional, List
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
import firebase_admin
from firebase_admin import credentials, auth
import re

load_dotenv()
app = FastAPI()

# --- MODELOS DE DATOS Pydantic ---
class AskRequest(BaseModel):
    context: str # El texto del temario O del resumen
    query: str
    # La propiedad schema_url ya no se usa, pero la dejamos por si la
    # implementamos en el futuro con imágenes.
    schema_url: Optional[str] = None
class TestResponse(BaseModel):
    test_id: int
    question_text: str
    was_correct: bool
    topic_id: int
class NewTestRequest(BaseModel):
    topic_id: Optional[int] = None
    is_random_test: bool = False
class HighlightRequest(BaseModel):
    context: str    

# --- CONFIGURACIÓN DE APIS ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# --- INICIALIZACIÓN DE FIREBASE ADMIN ---
try:
    firebase_sdk_json_str = os.getenv("FIREBASE_ADMIN_SDK_JSON")
    if not firebase_sdk_json_str:
        raise ValueError("Variable de entorno FIREBASE_ADMIN_SDK_JSON no encontrada.")
    cred_json = json.loads(firebase_sdk_json_str)
    cred = credentials.Certificate(cred_json)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    print("Firebase Admin SDK inicializado correctamente.")
except Exception as e:
    print(f"ERROR CRÍTICO inicializando Firebase: {e}")

# --- LÓGICA DE AUTENTICACIÓN ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        return uid
    except Exception as e:
        print(f"Error de autenticación: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

# --- PROMPT ENGINEERING ---
def create_gemini_prompt_multiple(full_context: str, fragments: list) -> str:
    variety_instructions = ["un detalle específico o un dato numérico.", "una definición clave.", "las funciones o competencias de un órgano descrito.", "una comparación entre dos conceptos.", "una excepción a una regla general.", "un plazo, fecha o período de tiempo."]
    variety_string = ", ".join(variety_instructions)
    fragment_section = ""
    for i, fragment in enumerate(fragments):
        fragment_section += f"\n--- FRAGMENTO DE TEXTO #{i+1} ---\n{fragment}\n"
    return f"""
    Actúa como un tribunal de oposición creando un examen variado y de alta dificultad.
    Te proporciono el CONTEXTO COMPLETO de un tema y una lista de 5 FRAGMENTOS ESPECÍFICOS.
    Tu tarea es generar una lista de 5 preguntas de test. Cada pregunta debe basarse única y exclusivamente en su fragmento correspondiente (Pregunta 1 -> Fragmento 1, etc.).
    Para asegurar la máxima variedad, para cada pregunta, intenta enfocarla en un tipo de información diferente. Considera los siguientes enfoques: {variety_string}
    No te repitas en el tipo de pregunta.
    La respuesta DEBE ser un array JSON que contenga 5 objetos JSON.
    El formato de salida debe ser estrictamente este, sin añadir coletillas como "Según el fragmento...":
    [
        {{"question": "¿Cuál es la capital de España?", "options": {{"A": "Lisboa", "B": "Madrid", "C": "París", "D": "Roma"}}, "correct_answer": "B"}},
        ...
    ]
    --- CONTEXTO COMPLETO ---
    {full_context}
    ---
    {fragment_section}
    """

# --- ENDPOINTS DE LA API (AHORA PROTEGIDOS) ---
@app.get("/api")
def read_root():
    return {"status": "OpoQuiz API está conectada y funcionando!"}

@app.get("/api/topics", response_model=List[dict])
def get_topics():
    try:
        response = supabase.table('topics').select('id, title, pdf_url, schema_url').execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/topics/{topic_id}/summaries")
def get_topic_summaries(topic_id: int, user_id: str = Depends(get_current_user)):
    """
    Consulta la tabla 'resumenes' y devuelve una lista de todos los resúmenes
    disponibles para un 'topic_id' específico.
    """
    try:
        # Seleccionamos todas las columnas de la tabla 'resumenes' que
        # coincidan con el topic_id proporcionado.
        response = supabase.table('resumenes').select('*').eq('topic_id', topic_id).execute()
        
        # Devolvemos los datos. Si no hay resúmenes, será una lista vacía.
        return {"summaries": response.data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))        

@app.get("/api/get-question")
def get_question(topic_id: int, user_id: str = Depends(get_current_user)):
    return generate_question_from_topic(topic_id, user_id)

@app.get("/api/get-random-question")
def get_random_question(user_id: str = Depends(get_current_user)):
    try:
        all_topics_response = supabase.table('topics').select('id').filter('content', 'not.is', 'null').execute()
        if not all_topics_response.data:
            raise HTTPException(status_code=404, detail="No hay temas con contenido.")
        random_topic_id = random.choice(all_topics_response.data)['id']
        return generate_question_from_topic(random_topic_id, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al seleccionar tema aleatorio: {str(e)}")

# Reemplaza tu función ask_topic por esta versión más simple

@app.post("/api/ask-topic")
def ask_topic(request: AskRequest, user_id: str = Depends(get_current_user)):
    try:
        # El prompt ahora es siempre el mismo, el de "Tutor experto"
        prompt = f"""
        Actúa como un tutor experto de oposiciones. Tu única fuente de conocimiento es el texto
        proporcionado. Responde a la pregunta del usuario de forma clara y concisa
        basándote estrictamente en la información proporcionada.
        
        Después de tu respuesta, añade una sección "**Fuente:**" y cita textualmente la
        frase del temario en la que te has basado.

        --- TEXTO FUENTE ---
        {request.context}
        ---
        --- PREGUNTA DEL USUARIO ---
        {request.query}
        ---
        """
        
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        response = model.generate_content(prompt)
        return {"answer": response.text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/get-highlighted-explanation")
def get_highlighted_explanation(request: HighlightRequest, user_id: str = Depends(get_current_user)):
    try:
        context = request.context
        
        # --- LÓGICA DE BÚSQUEDA CON EXPRESIONES REGULARES ---
        
        # Patrón para buscar una etiqueta y capturar el texto hasta el siguiente salto de línea
        exam_fragments = re.findall(r'\[PREGUNTA_EXAMEN\]\s*(.*?)\n', context)
        highlighted_fragments = re.findall(r'\[DESTACADO\]\s*(.*?)\n', context)
        date_fragments = re.findall(r'\[FECHA_CLAVE\]\s*(.*?)\n', context)
        
        # Unimos todos los fragmentos encontrados en una lista de prioridad
        priority_fragments = exam_fragments + highlighted_fragments + date_fragments
        
        if not priority_fragments:
            return {"answer": "No he encontrado conceptos con etiquetas especiales ([PREGUNTA_EXAMEN], [DESTACADO], etc.) en el temario."}

        print(f"Encontrados {len(priority_fragments)} fragmentos etiquetados para explicar.")
        chosen_fragment = random.choice(priority_fragments)

        # El fragmento ya viene limpio de la etiqueta gracias a la captura del grupo (.*?)
        
        prompt = f"""
        Actúa como un profesor experto. Un opositor te ha pedido que le expliques en profundidad
        el siguiente concepto clave de su temario:
        ---
        {chosen_fragment.strip()}
        ---
        Genera una explicación clara, detallada y fácil de entender.
        """
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        response = model.generate_content(prompt)
        return {"answer": response.text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))        

@app.post("/api/tests/start")
def start_new_test(request: NewTestRequest, user_id: str = Depends(get_current_user)):
    try:
        test_data = {"topic_id": request.topic_id, "is_random_test": request.is_random_test, "user_id": user_id}
        response = supabase.table('tests').insert(test_data).execute()
        return {"test_id": response.data[0]['id']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tests/answer")
def record_answer(response: TestResponse, user_id: str = Depends(get_current_user)):
    try:
        supabase.table('test_respuestas').insert({
            "test_id": response.test_id, "question_text": response.question_text,
            "was_correct": response.was_correct, "topic_id": response.topic_id, "user_id": user_id
        }).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
def get_stats(user_id: str = Depends(get_current_user)):
    try:
        resp_response = supabase.table('test_respuestas').select('*', count='exact').eq('user_id', user_id).execute()
        respuestas = resp_response.data
        total = len(respuestas)
        if total == 0:
            return {'total_answered': 0, 'correct': 0, 'incorrect': 0, 'by_topic': {}, 'accuracy': 0}
        correctas = sum(1 for r in respuestas if r['was_correct'])
        incorrectas = total - correctas
        accuracy = (correctas / total) * 100
        by_topic = {}
        for r in respuestas:
            topic_id = r['topic_id']
            if topic_id not in by_topic: by_topic[topic_id] = {'correct': 0, 'incorrect': 0}
            if r['was_correct']: by_topic[topic_id]['correct'] += 1
            else: by_topic[topic_id]['incorrect'] += 1
        return {'total_answered': total, 'correct': correctas, 'incorrect': incorrectas, 'by_topic': by_topic, 'accuracy': accuracy}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats/most-failed-questions")
def get_most_failed_questions(user_id: str = Depends(get_current_user)):
    try:
        response = supabase.rpc('get_most_failed_questions_for_user', {'p_user_id': user_id}).execute()
        return {"ok": True, "questions": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/get-topic-context/{topic_id}")
def get_topic_context(topic_id: int, user_id: str = Depends(get_current_user)):
    """
    Devuelve el texto completo y el texto del resumen de un tema específico.
    """
    try:
        response = supabase.table('topics').select("content, summary_text").eq('id', topic_id).single().execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Tema no encontrado")
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))        

# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS ---

def generate_question_from_topic(topic_id: int, user_id: str):
    try:
        # --- 1. OBTENCIÓN Y FRAGMENTACIÓN DEL TEXTO ---
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        full_text = response.data.get('content')
        if not full_text:
            raise HTTPException(status_code=404, detail=f"El tema {topic_id} no tiene contenido pre-procesado.")
        
        all_fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > 100]
        if not all_fragments:
            raise HTTPException(status_code=400, detail="El tema es demasiado corto para generar fragmentos.")

        # --- 2. LÓGICA DE SELECCIÓN DE FRAGMENTOS PRIORIZADA ---
        exam_fragments = [f for f in all_fragments if '[PREGUNTA_EXAMEN]' in f]
        highlighted_fragments = [f for f in all_fragments if '[DESTACADO]' in f]

        num_candidates = 5
        source_fragments = []

        if len(exam_fragments) >= num_candidates:
            print("Seleccionando fragmentos de [PREGUNTA_EXAMEN]")
            source_fragments = exam_fragments
        elif len(exam_fragments) + len(highlighted_fragments) >= num_candidates:
            print("Seleccionando fragmentos de [PREGUNTA_EXAMEN] y [DESTACADO]")
            source_fragments = exam_fragments + highlighted_fragments
        else:
            print("Pocos fragmentos priorizados, seleccionando de todo el texto.")
            source_fragments = all_fragments

        def clean_fragment(text):
            return text.replace('[PREGUNTA_EXAMEN]', '').replace('[DESTACADO]', '').replace('[FECHA_CLAVE]', '').strip()

        num_to_select = min(num_candidates, len(source_fragments))
        if num_to_select == 0:
            raise HTTPException(status_code=400, detail="No hay fragmentos válidos en el tema.")
        
        selected_fragments_raw = random.sample(source_fragments, num_to_select)
        selected_fragments = [clean_fragment(f) for f in selected_fragments_raw]

        # --- 3. OBTENCIÓN DEL HISTORIAL DE PREGUNTAS ---
        recent_questions_response = supabase.table('preguntas_generadas').select('question_text').eq('topic_id', topic_id).eq('user_id', user_id).order('created_at', desc=True).limit(50).execute()
        recent_question_texts = [q['question_text'] for q in recent_questions_response.data]

        # --- 4. GENERACIÓN Y FILTRADO EN LOTE ---
        prompt = create_gemini_prompt_multiple(full_context=clean_fragment(full_text), fragments=selected_fragments)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        gemini_response = model.generate_content(prompt)
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        
        try:
            list_of_questions = json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="La IA devolvió un formato JSON inválido en el lote.")

        SIMILARITY_THRESHOLD = 90
        for candidate in list_of_questions:
            candidate_text = candidate.get('question')
            if not candidate_text: continue

            is_too_similar = any(fuzz.token_set_ratio(candidate_text, r) > SIMILARITY_THRESHOLD for r in recent_question_texts)
            
            if not is_too_similar:
                print("¡Pregunta única y priorizada encontrada en el lote!")
                supabase.table('preguntas_generadas').insert({'question_text': candidate_text, 'topic_id': topic_id, 'user_id': user_id}).execute()
                candidate['topic_id'] = topic_id
                return candidate
        
        # --- 5. FALLBACK ---
        print("Todas las candidatas del lote eran repetidas. Devolviendo una aleatoria para no fallar.")
        fallback_question = random.choice(list_of_questions)
        fallback_question['topic_id'] = topic_id
        return fallback_question

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        raise HTTPException(status_code=500, detail=f"El backend falló al generar la pregunta: {str(e)}")


        