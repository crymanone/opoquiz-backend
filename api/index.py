from fastapi import FastAPI
app = FastAPI()

@app.get("/api/get-question")
def get_question():
    return { "question": "DIAGNÓSTICO EXITOSO: El nuevo despliegue funciona." }