#!/usr/bin/env python3
"""Serve ONLY this extracted reviewer package, on loopback, with no external dependencies."""
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import argparse,os
p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);a=p.parse_args();os.chdir(Path(__file__).resolve().parent)
class Handler(SimpleHTTPRequestHandler):
 def end_headers(self):
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');super().end_headers()
 def list_directory(self,path):self.send_error(403,'Directory listing disabled')
server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
print(f'打开 http://127.0.0.1:{a.port}/index.html （保持本窗口运行；Ctrl+C 停止）',flush=True)
server.serve_forever()
