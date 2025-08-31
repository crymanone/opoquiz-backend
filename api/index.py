from fastapi import FastAPI
app = FastAPI()

@app.get("/get-question") # <-- ¡CORREGIDO! Sin el "/api" al principio.
def get_question():
    return { "question": "¡FUNCIONA! Esta es la respuesta del despliegue limpio." }