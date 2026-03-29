"""
ブラウザから ASXN データを受け取って hype_data/asxn_cache.json に保存する。
使い方: python save_asxn.py
        → ポート 9876 で待受け、データ受信後に自動終了
"""
import http.server, json, os, sys

SAVE_PATH = os.path.join(os.path.dirname(__file__), "hype_data", "asxn_cache.json")
PORT = 9876

class Handler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
        with open(SAVE_PATH, "wb") as f:
            f.write(body)
        size_kb = len(body) / 1024
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
        print(f"\n✓ 保存完了: {SAVE_PATH} ({size_kb:.1f} KB)")
        # 保存後にサーバーを終了
        import threading
        threading.Thread(target=self.server.shutdown).start()

    def log_message(self, *args):
        pass  # ログを抑制

if __name__ == "__main__":
    with http.server.HTTPServer(("localhost", PORT), Handler) as srv:
        print(f"待受中... ポート {PORT}")
        srv.serve_forever()
    print("サーバー終了")
