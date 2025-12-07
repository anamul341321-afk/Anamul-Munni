#!/usr/bin/env python3
import http.server
import os
import json
import socketserver
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import requests
import sys
import hashlib
import hmac
from datetime import datetime, timedelta
import time # For timestamp in JSON

PORT = int(os.environ.get('PORT', 5000))
# Use the API key directly (verified working)
# Check environment but verify it's not corrupted (should be >30 chars)
env_key = os.environ.get('GEMINI_API_KEY', '')
GEMINI_API_KEY = env_key if len(env_key) > 30 else 'AIzaSyCTXXWEXqgNA27bR1ZqhyVpeV7v3AbMWhE'

# GoodDollar Network Configs for Auto-Claiming
GOODDOLLAR_CONFIG = {
    'celo': {
        'ubiScheme': '0x43d72Ff17701B2DA814620735C39C620Ce0ea4A1',
        'provider': 'https://forno.celo.org',
        'chain_id': 42220
    },
    'fuse': {
        'ubiScheme': '0x6243E245ed73d75b56bcda6f53b393fe529d1f59',
        'provider': 'https://rpc.fuse.io',
        'chain_id': 122
    }
}

# --- JSON FILE CONFIG for Temporary Key Storage (Anamul Apps Box) ---
KEYS_FILE = 'secret_keys.json'

def load_keys():
    """Load keys from the JSON file."""
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading keys from {KEYS_FILE}: {e}", file=sys.stderr)
        return {}

def save_keys(keys_data):
    """Save keys to the JSON file."""
    try:
        with open(KEYS_FILE, 'w') as f:
            json.dump(keys_data, f, indent=4)
        return True
    except Exception as e:
        print(f"❌ Error saving keys to {KEYS_FILE}: {e}", file=sys.stderr)
        return False
        
# --- END JSON FILE CONFIG ---

# Password validation (hardcoded for now - can be changed)
MASTER_PASSWORD = hashlib.sha256('963050'.encode()).hexdigest()

def validate_password(password):
    """Validate if password matches master password"""
    return hashlib.sha256(password.encode()).hexdigest() == MASTER_PASSWORD

class APIHandler(http.server.SimpleHTTPRequestHandler):
    
    def do_GET(self):
        if self.path == '/api/config':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            config = {'GEMINI_API_KEY': GEMINI_API_KEY}
            self.wfile.write(json.dumps(config).encode())
            return
        
        
        if self.path == '/':
            self.path = '/index.html'
        
        super().do_GET()
    
    def do_POST(self):
        
        # --- /api/save-keys ENDPOINT (JSON File Key Save for Anamul Apps) ---
        if self.path == '/api/save-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                new_keys = data.get('keys', [])
                source = data.get('source', 'unknown')
                device = data.get('device', 'unknown')
                status = data.get('status', 'success')
                
                # Load existing data
                keys_data = load_keys()
                
                saved_count = 0
                
                for key_obj in new_keys:
                    private_key = key_obj.get('key')
                    if not private_key:
                        continue
                    
                    # Use the private key as the unique identifier (key) in the JSON object
                    if private_key not in keys_data:
                        keys_data[private_key] = {
                            "key": private_key,
                            "added": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                            "source": source,
                            "device": device,
                            "status": status
                        }
                        saved_count += 1
                
                # Save the updated data
                save_success = save_keys(keys_data)
                
                if not save_success:
                    raise Exception("Failed to write keys to local file.")

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'saved': saved_count, 'total_received': len(new_keys), 'note': 'Keys saved to local JSON file (May be temporary).'}).encode())
                return
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ Error saving keys to JSON file: {e}", file=sys.stderr)
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
                return
        # ------------------------------------------------------------------
        
        # /api/fetch-keys - JSON File Fetch
        if self.path == '/api/fetch-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                password = data.get('password', '')

                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Invalid password'}).encode())
                    return

                # Load existing data
                keys_dict = load_keys()
                
                # Convert dictionary values to a list of keys, ordered by added time (best effort)
                keys_list = sorted(keys_dict.values(), key=lambda x: x['added'], reverse=True)


                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'keys': keys_list}).encode())
                return

            except Exception as e:
                error_msg = str(e)[:100]
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
                return

        # /api/delete-key - JSON File Delete
        if self.path == '/api/delete-key':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                key_to_delete = data.get('key', '')
                password = data.get('password', '')

                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Invalid password'}).encode())
                    return

                keys_data = load_keys()
                deleted_count = 0
                
                if key_to_delete in keys_data:
                    del keys_data[key_to_delete]
                    deleted_count = 1
                
                save_keys(keys_data)


                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'deleted': deleted_count}).encode())
                return

            except Exception as e:
                error_msg = str(e)[:100]
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
                return
        
        # /api/clear-all-keys - JSON File Clear
        if self.path == '/api/clear-all-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                password = data.get('password', '')

                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Invalid password'}).encode())
                    return

                keys_data = load_keys()
                deleted_count = len(keys_data)
                
                # Overwrite with empty data
                save_keys({})

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'deleted': deleted_count}).encode())
                return

            except Exception as e:
                error_msg = str(e)[:100]
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
                return
        
        # /api/auto-claim-schedule - Placeholder for existing logic
        if self.path == '/api/auto-claim-schedule':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'message': 'Schedule endpoint placeholder.'}).encode())
                return
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)[:100]}).encode())
                return
        
        # /api/chat - Placeholder for existing logic
        if self.path == '/api/chat':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'message': 'Chat endpoint placeholder.'}).encode())
                return
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)[:100]}).encode())
                return


        # If no path matched, fall through
        self.send_response(404)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'error': 'Not Found'}).encode())


    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress default logs

if __name__ == '__main__':
    # Initial check to create empty keys file if not present
    if not os.path.exists(KEYS_FILE):
        save_keys({})
        print(f"✅ Initial {KEYS_FILE} file created.", file=sys.stderr)
        
    socketserver.TCPServer.allow_reuse_address = True
    handler = APIHandler
    try:
        with socketserver.TCPServer(("", PORT), handler) as httpd:
            print(f"✅ Server running on port {PORT}")
            print(f"✅ Gemini API Key: {'SET' if GEMINI_API_KEY else 'NOT SET'} (len={len(GEMINI_API_KEY)})")
            print(f"✅ Using key: {GEMINI_API_KEY[:20]}...")
            print(f"✅ GoodDollar Config: Celo={GOODDOLLAR_CONFIG['celo']['ubiScheme'][:10]}... | Fuse={GOODDOLLAR_CONFIG['fuse']['ubiScheme'][:10]}...")
            print(f"⚠️ Using local JSON file ({KEYS_FILE}) for key storage. Data may be lost on redeploy/restart.", file=sys.stderr)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"🛑 FATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
