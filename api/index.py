# api/index.py - VERSIÓN FINAL Y COMPLETA

from fastapi import FastAPI
import os
import json
import requests  
import io        
from supabase import create_client, Client
import google.generativeai as genai
from pypdf import PdfReader # <-- Necesario para leer el PDF
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
# En tu archivo api/index.py del proyecto opoquiz-backend

def create_gemini_prompt(topic_content: str) -> str:
    # NUEVA INSTRUCCIÓN DE VARIEDAD
    variety_instructions = [
        "enfócate en un detalle específico o un dato numérico del texto.",
        "basa la pregunta en una definición clave mencionada en el documento.",
        "crea una pregunta sobre las funciones o competencias de un órgano descrito.",
        "formula una pregunta que compare dos conceptos mencionados en el texto.",
        "haz una pregunta sobre una excepción a una regla general descrita.",
        "céntrate en un plazo, fecha o período de tiempo mencionado."
    ]
    # Elegimos una de las instrucciones al azar para cada pregunta
    selected_instruction = random.choice(variety_instructions)

    return f"""
    Eres un preparador de oposiciones experto y muy creativo.
    Tu objetivo es generar una pregunta de test variada y que ponga a prueba la atención al detalle del opositor.

    Basándote estrictamente en el siguiente texto, genera una pregunta de tipo test.
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
    """
    Obtiene el ID de un tema, busca su URL de PDF en Supabase,
    descarga el PDF, extrae su texto y usa Gemini para generar una pregunta.
    """
    try:
        # --- PASO 1: OBTENER LA URL DEL PDF DESDE SUPABASE ---
        print(f"Buscando URL para topic_id: {topic_id}")
        response = supabase.table('topics').select("pdf_url").eq('id', topic_id).single().execute()
        
        # Verificar que se encontró una URL
        if not response.data or not response.data.get('pdf_url'):
            return {"error": f"No se encontró una URL de PDF para el topic_id {topic_id}"}, 404
            
        pdf_url = response.data['pdf_url']
        print(f"URL del PDF encontrada: {pdf_url}")

        # --- PASO 2: DESCARGAR EL PDF EN MEMORIA ---
        print("Descargando el archivo PDF...")
        pdf_response = requests.get(pdf_url, timeout=20) # Timeout de 20 segundos
        pdf_response.raise_for_status()  # Esto lanzará un error si la descarga falla (ej. 404)
        print("PDF descargado con éxito.")

        # --- PASO 3: EXTRAER EL TEXTO DEL PDF ---
        print("Extrayendo texto del PDF...")
        # Usamos io.BytesIO para tratar el contenido descargado como un archivo en memoria
        pdf_file_in_memory = io.BytesIO(pdf_response.content)
        
        # Usamos la librería pypdf para leer el "archivo"
        pdf_reader = PdfReader(pdf_file_in_memory)
        
        pdf_text = ""
        # Iteramos a través de todas las páginas y unimos su texto
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                pdf_text += page_text + "\n"
        
        if not pdf_text:
            return {"error": "El PDF parece estar vacío o no contiene texto extraíble."}, 400
        
        print(f"Texto extraído. Total de caracteres: {len(pdf_text)}")

        # --- PASO 4: LLAMAR A GEMINI CON EL TEXTO EXTRAÍDO ---
        print("Enviando texto a Gemini para generar la pregunta...")
        # Usamos el modelo 'flash' para velocidad y costes, pero 'pro' es más potente
        model = genai.GenerativeModel('gemini-1.5-flash-latest') 
        prompt = create_gemini_prompt(pdf_text)
        gemini_response = model.generate_content(prompt)
        print("Respuesta recibida de Gemini.")

        # --- PASO 5: LIMPIAR Y DEVOLVER LA RESPUESTA JSON ---
        # A veces Gemini envuelve el JSON en ```json ... ```, lo limpiamos.
        cleaned_response = gemini_response.text.strip().replace("```json", "").replace("```", "").strip()
        
        # Convertimos el texto limpio a un objeto JSON de Python (un diccionario)
        quiz_data = json.loads(cleaned_response)
        
        print("Pregunta generada y formateada con éxito.")
        return quiz_data

    except requests.exceptions.RequestException as e:
        # Error específico si falla la descarga del PDF
        print(f"ERROR de red al descargar el PDF: {e}")
        return {"error": f"No se pudo descargar el archivo PDF desde la URL. Causa: {e}"}, 500
    except Exception as e:
        # Captura cualquier otro error en el proceso
        print(f"ERROR inesperado en get_question: {e}")
        return {"error": f"Ocurrió un error inesperado: {str(e)}"}, 500