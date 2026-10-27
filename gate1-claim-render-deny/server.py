import http.server
import json
import os

class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self):
        body=json.dumps({"status":"DENY_NOT_CONNECTED","gate":"DENY","execution_enabled":False,"claim_enabled":False,"kms_sign_allowed":False}).encode()
        self.send_response(403)
        self.send_header("Content-Type","application/json")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self): self.reply()
    def do_POST(self): self.reply()
    def do_PUT(self): self.reply()
    def do_DELETE(self): self.reply()
    def log_message(self,fmt,*args): pass

if __name__=="__main__":
    http.server.HTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),Handler).serve_forever()
