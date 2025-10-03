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
    context: Optional[str] = None # Hacemos ambos opcionales
    query: str
    summary_context: Optional[str] = None # <-- AÑADIDO
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
def get_topics(user_id: str = Depends(get_current_user)):
    try:
        response = supabase.table('topics').select('id, title, pdf_url').execute()
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
        response = supabase.table('resumenes').select('id, titulo, content').eq('topic_id', topic_id).execute()
        
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
        

@app.post("/api/ask-topic")
def ask_topic(request: AskRequest, user_id: str = Depends(get_current_user)):
    try:
        is_summary_request = (request.query == "SYSTEM_COMMAND_GENERATE_SUMMARY")

        if is_summary_request and request.summary_context:
            print("Petición de resumen detectada. Usando prompt de plantilla detallada con fuente.")
            
            prompt = f"""
            **ROL:** Eres un sistema de IA experto en crear apuntes de estudio de alta calidad para opositores. Tu objetivo es la claridad, la exhaustividad y la precisión.

            **TAREA:** Analiza el texto proporcionado y genera un resumen muy estructurado
            siguiendo estrictamente el siguiente formato Markdown.

            **TEXTO A RESUMIR:**
            ---
            {request.summary_context}
            ---

            **FORMATO DE SALIDA OBLIGATORIO (RELLENA CADA SECCIÓN CON PROFUNDIDAD):**

            ### Puntos Clave Fundamentales
            - (Usa viñetas para listar y explicar brevemente los 3 a 5 conceptos más esenciales del texto.)

            ### Artículos y Legislación Relevante
            - (Crea una lista de todos los artículos de leyes mencionados. Para cada uno, escribe el número del artículo en negrita y explica su contenido principal.)

            ### Fechas y Plazos Cruciales
            - (Si existen, crea una lista de todas las fechas y plazos importantes, explicando qué ocurrió en cada una.)
            
            ### Resumen General Desarrollado
            (Escribe un resumen en prosa de varios párrafos que conecte todas las ideas anteriores.)
            
            ---
            
            ### Fuente Principal
            (Aquí, cita textualmente la frase o párrafo más importante del "TEXTO A RESUMIR" que, en tu opinión, encapsula la idea central de todo el documento.)
            """
            model = genai.GenerativeModel('gemini-2.5-flash')

        else:
            # --- INICIO DEL BLOQUE CON INDENTACIÓN CORREGIDA ---
            print("Petición de pregunta normal detectada.")
            context_to_use = request.context or request.summary_context
            if not context_to_use:
                return {"answer": "Lo siento, no se ha proporcionado temario para responder."}
            
            prompt = f"""
            Actúa como un tutor experto. Responde a la pregunta del usuario basándote
            estrictamente en el TEXTO DEL TEMARIO. Después de tu respuesta, añade una sección
            "**Fuente:**" y cita textualmente la frase en la que te has basado.
            --- TEXTO DEL TEMARIO ---
            {context_to_use}
            ---
            --- PREGUNTA DEL USUARIO ---
            {request.query}
            ---
            """
            # El modelo Pro es mejor para la precisión de las preguntas directas
            model = genai.GenerativeModel('gemini-2.5-pro')
            # --- FIN DEL BLOQUE CON INDENTACIÓN CORREGIDA ---

        # Esta parte se ejecuta para ambos casos
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
        model = genai.GenerativeModel('gemini-2.5-flash')
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
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Tema no encontrado")
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))        

# --- FUNCIÓN REUTILIZABLE PARA GENERAR PREGUNTAS ---

def generate_question_from_topic(topic_id: int, user_id: str):
    try:
        # --- 1. OBTENCIÓN DE DATOS ---
        response = supabase.table('topics').select("content").eq('id', topic_id).single().execute()
        full_text = response.data.get('content')
        if not full_text:
            raise HTTPException(status_code=404, detail=f"El tema {topic_id} no tiene contenido.")
        
        all_fragments = [p.strip() for p in full_text.split('\n\n') if len(p.strip()) > 150]
        if not all_fragments:
            raise HTTPException(status_code=400, detail="El tema es demasiado corto.")

        ### --- INICIO DE LA OPTIMIZACIÓN --- ###

        # 2. OBTENER HISTORIAL (REDUCIDO)
        # Reducimos el límite de 100 a 30. Es suficiente para evitar repeticiones recientes.
        recent_questions_response = supabase.table('preguntas_generadas') \
            .select('question_text') \
            .eq('topic_id', topic_id) \
            .eq('user_id', user_id) \
            .order('created_at', desc=True) \
            .limit(30) \
            .execute()
        recent_question_texts = [q['question_text'] for q in recent_questions_response.data]

        # 3. GENERAR UN LOTE DE CANDIDATAS (REDUCIDO)
        # Reducimos el lote de 5 a 3. Esto disminuye las llamadas a fuzz.
        num_candidates = 3
        num_to_select = min(num_candidates, len(all_fragments))
        
        if num_to_select == 0:
            raise HTTPException(status_code=400, detail="No hay fragmentos válidos.")
            
        selected_fragments = random.sample(all_fragments, num_to_select)
        prompt = create_gemini_prompt_multiple(full_context=full_text, fragments=selected_fragments)
        
        model = genai.GenerativeModel('gemini-2.5-flash')
        gemini_response = model.generate_content(prompt)
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        
        try:
            list_of_questions = json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="La IA devolvió un JSON inválido.")

        # --- 4. FILTRADO (AHORA MUCHO MÁS RÁPIDO) ---
        # Ahora solo haremos 3 * 30 = 90 comparaciones, en lugar de 500.
        SIMILARITY_THRESHOLD = 90
        
        for candidate in list_of_questions:
            candidate_text = candidate.get('question')
            if not candidate_text: continue

            is_too_similar = any(fuzz.token_set_ratio(candidate_text, r) > SIMILARITY_THRESHOLD for r in recent_question_texts)
            
            if not is_too_similar:
                print("¡Pregunta única encontrada en el lote!")
                supabase.table('preguntas_generadas').insert({'question_text': candidate_text, 'topic_id': topic_id, 'user_id': user_id}).execute()
                candidate['topic_id'] = topic_id
                return candidate
        
        # --- 5. FALLBACK (sin cambios) ---
        print("Todas las candidatas del lote eran repetidas. Devolviendo una aleatoria.")
        fallback_question = random.choice(list_of_questions)
        fallback_question['topic_id'] = topic_id
        return fallback_question

    except Exception as e:
        print(f"!!! ERROR GRAVE EN EL BACKEND: {e}")
        raise HTTPException(status_code=500, detail=f"El backend falló: {str(e)}")


        