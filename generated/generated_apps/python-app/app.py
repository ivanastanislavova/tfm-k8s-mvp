from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hola desde Python en Kubernetes")


server = HTTPServer(("0.0.0.0", 80), Handler)
print("Servidor escuchando en puerto 80")
server.serve_forever()