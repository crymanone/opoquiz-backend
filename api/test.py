# api/test.py

from http.server import BaseHTTPRequestHandler
import json

# Vercel busca una clase llamada 'handler' en el archivo.
class handler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        # 1. Decir que la respuesta es exitosa (código 200)
        self.send_response(200)
        
        # 2. Indicar que el contenido es un JSON
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # 3. Crear el mensaje de respuesta
        message = {
            "message": "¡ÉXITO ABSOLUTO! El handler de Python más básico está funcionando."
        }
        
        # 4. Enviar la respuesta
        self.wfile.write(json.dumps(message).encode('utf-8'))
        return