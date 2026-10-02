"""Deterministic local test provider. Never a production default or embedding stub."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_json(self,value,status=200):
        body=json.dumps(value).encode()
        self.send_response(status);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self.send_json({"object":"list","data":[{"id":"synthetic-contract","object":"model","owned_by":"test-only"}]})
        else:self.send_json({"ok":True})
    def do_POST(self):
        try:data=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
        except (ValueError,TypeError):return self.send_json({"error":"invalid JSON"},400)
        if self.path.rstrip("/").endswith("/api/show"):
            return self.send_json({"model_info":{"llama.context_length":8192}})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self.send_json({"error":"unsupported test endpoint"},404)
        answer="Synthetic contract response based on the supplied evidence [1]."
        if data.get("stream"):
            self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
            event={"id":"test","object":"chat.completion.chunk","model":"synthetic-contract","choices":[{"index":0,"delta":{"content":answer},"finish_reason":None}]}
            self.wfile.write(("data: "+json.dumps(event)+"\n\ndata: [DONE]\n\n").encode());self.wfile.flush()
        else:
            self.send_json({"id":"test","object":"chat.completion","model":"synthetic-contract",
                "choices":[{"index":0,"message":{"role":"assistant","content":answer},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":32,"completion_tokens":12,"total_tokens":44}})

if __name__=="__main__":
    ThreadingHTTPServer(("0.0.0.0",8000),Handler).serve_forever()
