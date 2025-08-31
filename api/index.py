from fastapi import FastAPI
app = FastAPI()

# Le decimos a FastAPI que responda en la ruta raíz "/"
# Vercel convertirá esto en la ruta "/api/"
@app.get("/")
def root_handler():
    return { "status": "OK", "message": "El servidor FastAPI está vivo en la ruta raíz." }