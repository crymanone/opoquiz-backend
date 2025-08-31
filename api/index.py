# api/index.py - El código de diagnóstico definitivo

from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        # Esta función ahora nos devolverá la ruta exacta que Vercel está pidiendo
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # Creamos una respuesta que INCLUYE la ruta que ha recibido
        response_data = {
            "message": "El servidor está vivo. Vercel está pidiendo esta ruta:",
            "path_recibida": self.path 
        }
        
        self.wfile.write(json.dumps(response_data).encode('utf-8'))
        return